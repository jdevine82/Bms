/**
 * @file
 * @brief bms-mstp: Devine BMS BACnet MS/TP service (milestone 4c).
 *
 * Runs on the Proxmox host, one process per MS/TP trunk (RS485 port), as an
 * MS/TP master node built on bacnet-stack. bms-backend talks to it through a
 * small local API: text request lines over TCP, JSON answer lines.
 *
 * The trunk is set with bacnet-stack's environment variables:
 *   BACNET_IFACE      serial port, e.g. /dev/bms-rs485-2
 *   BACNET_MSTP_BAUD  9600, 19200, 38400, 57600, 76800 or 115200
 *   BACNET_MSTP_MAC   this node's MAC address (0 on the site server, main spec 9)
 *   BACNET_MAX_MASTER highest master MAC to poll for (default 127)
 *   BACNET_MAX_INFO_FRAMES frames sent per token (default 1)
 * and by these options:
 *   --listen ADDRESS:PORT  API address (default 127.0.0.1:47900)
 *   --instance N           this node's BACnet device instance (must be unique)
 *   --apdu-timeout MS      wait per try for an answer (default 3000; bacnet-stack's own MS/TP
 *                          default of 60000 is far too long for polling)
 *   --apdu-retries N       extra tries after the first (default 1)
 *
 * Requests (one per line; <id> is the caller's request ID, echoed back):
 *   PING <id>
 *   STATS <id>
 *   WHOIS <id> <low> <high> <wait_ms>          low/high -1 for every device
 *   READ <id> <mac> <type>:<instance>:<property> [...]
 *        type and property are BACnet numbers, e.g. 0:1:85 is
 *        analog-input 1 present-value. Sent as one ReadPropertyMultiple.
 *   WRITE <id> <mac> <type>:<instance>:<property> <priority> <tag> <value>   (milestone 7A)
 *        One WriteProperty. <priority> is 1 to 16 (0: none). <tag> is real, enum, unsigned
 *        or null; null (with any <value>, e.g. -) releases that priority. Answers
 *        {"ok":true} on the device's SimpleACK.
 * Answers are one JSON object per line, always with "id" and "ok".
 *
 * @copyright Devine BMS. Proprietary. Links the bacnet-stack library
 * (GPL-2.0-or-later WITH GCC-exception-2.0), which permits linking.
 */
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include "bacnet/bacdef.h"
#include "bacnet/bacapp.h"
#include "bacnet/bacerror.h"
#include "bacnet/bactext.h"
#include "bacnet/iam.h"
#include "bacnet/npdu.h"
#include "bacnet/apdu.h"
#include "bacnet/rpm.h"
#include "bacnet/version.h"
#include "bacnet/basic/binding/address.h"
#include "bacnet/basic/object/device.h"
#include "bacnet/basic/services.h"
#include "bacnet/basic/tsm/tsm.h"
#include "bacnet/datalink/datalink.h"
#include "bacnet/datalink/dlenv.h"
#include "bacnet/datalink/dlmstp.h"
#include "bacport.h"

#define SERVICE_VERSION "0.2.0"
#define MAX_CLIENTS 8
#define LINE_MAX_LEN 4096
#define OUT_MAX_LEN 65536
#define MAX_WHOIS 4
#define MAX_FOUND 128
#define MSTP_MAX_APDU 480

static uint8_t Rx_Buf[MAX_MPDU];
static uint8_t Tx_Buf[MAX_PDU];
static volatile sig_atomic_t Stop = 0;
static uint32_t My_Instance = 4194300;
static long My_Mac = 0;

/* ---------- API clients ---------- */

typedef struct {
    int fd;
    char in[LINE_MAX_LEN];
    size_t in_len;
} CLIENT;

static CLIENT Clients[MAX_CLIENTS];

/* A ReadPropertyMultiple waiting for its answer, by invoke ID. */
typedef struct {
    bool active;
    int fd;
    char id[64];
    uint8_t mac;
    bool write; /* a WriteProperty waiting for its SimpleACK */
} PENDING_READ;

static PENDING_READ Pending[256];

typedef struct {
    uint32_t instance;
    uint8_t mac;
    unsigned max_apdu;
    int segmentation;
    uint16_t vendor;
} FOUND_DEVICE;

/* A Who-Is collecting I-Am answers until its deadline. */
typedef struct {
    bool active;
    int fd;
    char id[64];
    uint64_t deadline_ms;
    int32_t low;
    int32_t high;
    FOUND_DEVICE found[MAX_FOUND];
    unsigned count;
} PENDING_WHOIS;

static PENDING_WHOIS Whois[MAX_WHOIS];

static uint64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)(ts.tv_nsec / 1000000);
}

static void on_signal(int sig)
{
    (void)sig;
    Stop = 1;
}

/* ---------- JSON output ---------- */

typedef struct {
    char buf[OUT_MAX_LEN];
    size_t len;
    bool overflow;
} OUT;

static void out_raw(OUT *o, const char *s)
{
    size_t n = strlen(s);
    if (o->len + n + 2 >= sizeof(o->buf)) {
        o->overflow = true;
        return;
    }
    memcpy(o->buf + o->len, s, n);
    o->len += n;
    o->buf[o->len] = 0;
}

static void out_fmt(OUT *o, const char *fmt, ...)
{
    char tmp[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    out_raw(o, tmp);
}

static void out_str(OUT *o, const char *s)
{
    char tmp[8];
    out_raw(o, "\"");
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') {
            tmp[0] = '\\';
            tmp[1] = (char)c;
            tmp[2] = 0;
            out_raw(o, tmp);
        } else if (c < 0x20) {
            snprintf(tmp, sizeof(tmp), "\\u%04x", c);
            out_raw(o, tmp);
        } else {
            tmp[0] = (char)c;
            tmp[1] = 0;
            out_raw(o, tmp);
        }
    }
    out_raw(o, "\"");
}

static void send_line(int fd, OUT *o)
{
    size_t sent = 0;
    if (fd < 0) {
        return;
    }
    if (o->overflow) {
        const char *msg = "{\"ok\":false,\"error\":\"answer too long\"}\n";
        (void)!write(fd, msg, strlen(msg));
        return;
    }
    out_raw(o, "\n");
    while (sent < o->len) {
        ssize_t n = write(fd, o->buf + sent, o->len - sent);
        if (n <= 0) {
            if (errno == EINTR) {
                continue;
            }
            return;
        }
        sent += (size_t)n;
    }
}

static void answer_error(int fd, const char *id, const char *message)
{
    OUT o = { 0 };
    out_raw(&o, "{\"id\":");
    out_str(&o, id);
    out_raw(&o, ",\"ok\":false,\"error\":");
    out_str(&o, message);
    out_raw(&o, "}");
    send_line(fd, &o);
}

/** One application value as JSON. Bit strings (status flags) are arrays of booleans. */
static void out_value(OUT *o, const BACNET_APPLICATION_DATA_VALUE *v)
{
    char text[256];
    switch (v->tag) {
        case BACNET_APPLICATION_TAG_NULL:
            out_raw(o, "null");
            break;
        case BACNET_APPLICATION_TAG_BOOLEAN:
            out_raw(o, v->type.Boolean ? "true" : "false");
            break;
        case BACNET_APPLICATION_TAG_UNSIGNED_INT:
            out_fmt(o, "%llu", (unsigned long long)v->type.Unsigned_Int);
            break;
        case BACNET_APPLICATION_TAG_SIGNED_INT:
            out_fmt(o, "%lld", (long long)v->type.Signed_Int);
            break;
        case BACNET_APPLICATION_TAG_REAL:
            out_fmt(o, "%.9g", (double)v->type.Real);
            break;
        case BACNET_APPLICATION_TAG_DOUBLE:
            out_fmt(o, "%.17g", v->type.Double);
            break;
        case BACNET_APPLICATION_TAG_ENUMERATED:
            out_fmt(o, "%u", (unsigned)v->type.Enumerated);
            break;
        case BACNET_APPLICATION_TAG_CHARACTER_STRING: {
            size_t n = characterstring_length(&v->type.Character_String);
            if (n >= sizeof(text)) {
                n = sizeof(text) - 1;
            }
            memcpy(text, characterstring_value(&v->type.Character_String), n);
            text[n] = 0;
            out_str(o, text);
            break;
        }
        case BACNET_APPLICATION_TAG_BIT_STRING: {
            uint8_t bits = bitstring_bits_used(&v->type.Bit_String);
            out_raw(o, "[");
            for (uint8_t i = 0; i < bits; i++) {
                out_raw(o, i ? "," : "");
                out_raw(o, bitstring_bit(&v->type.Bit_String, i) ? "true" : "false");
            }
            out_raw(o, "]");
            break;
        }
        default: {
            BACNET_OBJECT_PROPERTY_VALUE opv = { 0 };
            BACNET_APPLICATION_DATA_VALUE copy = *v;
            copy.next = NULL;
            opv.value = &copy;
            opv.array_index = BACNET_ARRAY_ALL;
            bacapp_snprintf_value(text, sizeof(text), &opv);
            out_str(o, text);
            break;
        }
    }
}

/* ---------- BACnet handlers ---------- */

static void finish_read(uint8_t invoke_id)
{
    Pending[invoke_id].active = false;
    Pending[invoke_id].fd = -1;
}

static bool pending_from(uint8_t invoke_id, const BACNET_ADDRESS *src)
{
    return Pending[invoke_id].active && src->mac_len == 1 && src->mac[0] == Pending[invoke_id].mac;
}

static void rpm_ack_handler(
    uint8_t *service_request,
    uint16_t service_len,
    BACNET_ADDRESS *src,
    BACNET_CONFIRMED_SERVICE_ACK_DATA *service_data)
{
    uint8_t invoke_id = service_data->invoke_id;
    BACNET_READ_ACCESS_DATA *rpm_data = NULL;
    int len = 0;
    OUT o = { 0 };
    bool first = true;

    if (!pending_from(invoke_id, src)) {
        return;
    }
    rpm_data = calloc(1, sizeof(BACNET_READ_ACCESS_DATA));
    if (rpm_data) {
        len = rpm_ack_decode_service_request(service_request, service_len, rpm_data);
    }
    if (len <= 0) {
        answer_error(Pending[invoke_id].fd, Pending[invoke_id].id, "malformed ReadPropertyMultiple answer");
        while (rpm_data) {
            rpm_data = rpm_data_free(rpm_data);
        }
        finish_read(invoke_id);
        return;
    }
    out_raw(&o, "{\"id\":");
    out_str(&o, Pending[invoke_id].id);
    out_raw(&o, ",\"ok\":true,\"values\":[");
    for (BACNET_READ_ACCESS_DATA *obj = rpm_data; obj; obj = obj->next) {
        for (BACNET_PROPERTY_REFERENCE *prop = obj->listOfProperties; prop; prop = prop->next) {
            out_raw(&o, first ? "" : ",");
            first = false;
            out_fmt(&o, "{\"type\":%u,\"instance\":%lu,\"property\":%u,", (unsigned)obj->object_type,
                (unsigned long)obj->object_instance, (unsigned)prop->propertyIdentifier);
            if (prop->value) {
                out_raw(&o, "\"value\":");
                if (prop->value->next) {
                    out_raw(&o, "[");
                    for (BACNET_APPLICATION_DATA_VALUE *v = prop->value; v; v = v->next) {
                        out_raw(&o, v == prop->value ? "" : ",");
                        out_value(&o, v);
                    }
                    out_raw(&o, "]");
                } else {
                    out_value(&o, prop->value);
                }
                out_raw(&o, "}");
            } else {
                out_raw(&o, "\"error\":{\"class\":");
                out_str(&o, bactext_error_class_name((int)prop->error.error_class));
                out_raw(&o, ",\"code\":");
                out_str(&o, bactext_error_code_name((int)prop->error.error_code));
                out_raw(&o, "}}");
            }
        }
    }
    out_raw(&o, "]}");
    send_line(Pending[invoke_id].fd, &o);
    while (rpm_data) {
        rpm_data = rpm_data_free(rpm_data);
    }
    finish_read(invoke_id);
}

static void write_ack_handler(BACNET_ADDRESS *src, uint8_t invoke_id)
{
    OUT o = { 0 };
    if (!pending_from(invoke_id, src) || !Pending[invoke_id].write) {
        return;
    }
    out_raw(&o, "{\"id\":");
    out_str(&o, Pending[invoke_id].id);
    out_raw(&o, ",\"ok\":true}");
    send_line(Pending[invoke_id].fd, &o);
    finish_read(invoke_id);
}

static void error_handler(
    BACNET_ADDRESS *src, uint8_t invoke_id, BACNET_ERROR_CLASS error_class, BACNET_ERROR_CODE error_code)
{
    char text[200];
    if (!pending_from(invoke_id, src)) {
        return;
    }
    snprintf(text, sizeof(text), "BACnet error %s: %s", bactext_error_class_name((int)error_class),
        bactext_error_code_name((int)error_code));
    answer_error(Pending[invoke_id].fd, Pending[invoke_id].id, text);
    finish_read(invoke_id);
}

static void abort_handler(BACNET_ADDRESS *src, uint8_t invoke_id, uint8_t abort_reason, bool server)
{
    char text[200];
    (void)server;
    if (!pending_from(invoke_id, src)) {
        return;
    }
    snprintf(text, sizeof(text), "BACnet abort: %s", bactext_abort_reason_name((int)abort_reason));
    answer_error(Pending[invoke_id].fd, Pending[invoke_id].id, text);
    finish_read(invoke_id);
}

static void reject_handler(BACNET_ADDRESS *src, uint8_t invoke_id, uint8_t reject_reason)
{
    char text[200];
    if (!pending_from(invoke_id, src)) {
        return;
    }
    snprintf(text, sizeof(text), "BACnet reject: %s", bactext_reject_reason_name((int)reject_reason));
    answer_error(Pending[invoke_id].fd, Pending[invoke_id].id, text);
    finish_read(invoke_id);
}

static void i_am_handler(uint8_t *service_request, uint16_t service_len, BACNET_ADDRESS *src)
{
    uint32_t device_id = 0;
    unsigned max_apdu = 0;
    int segmentation = 0;
    uint16_t vendor_id = 0;
    (void)service_len;

    if (iam_decode_service_request(service_request, &device_id, &max_apdu, &segmentation, &vendor_id) < 0) {
        return;
    }
    if (src->mac_len != 1) {
        return; /* not a device on this trunk */
    }
    for (int w = 0; w < MAX_WHOIS; w++) {
        PENDING_WHOIS *p = &Whois[w];
        bool seen = false;
        if (!p->active) {
            continue;
        }
        if ((p->low >= 0 && device_id < (uint32_t)p->low) || (p->high >= 0 && device_id > (uint32_t)p->high)) {
            continue;
        }
        for (unsigned i = 0; i < p->count; i++) {
            if (p->found[i].instance == device_id) {
                seen = true;
            }
        }
        if (!seen && p->count < MAX_FOUND) {
            p->found[p->count].instance = device_id;
            p->found[p->count].mac = src->mac[0];
            p->found[p->count].max_apdu = max_apdu;
            p->found[p->count].segmentation = segmentation;
            p->found[p->count].vendor = vendor_id;
            p->count++;
        }
    }
}

static void init_service_handlers(void)
{
    Device_Init(NULL);
    Device_Set_Object_Instance_Number(My_Instance);
    /* answer Who-Is and ReadProperty so tools on the trunk can see this node */
    apdu_set_unconfirmed_handler(SERVICE_UNCONFIRMED_WHO_IS, handler_who_is);
    apdu_set_unconfirmed_handler(SERVICE_UNCONFIRMED_I_AM, i_am_handler);
    apdu_set_unrecognized_service_handler_handler(handler_unrecognized_service);
    apdu_set_confirmed_handler(SERVICE_CONFIRMED_READ_PROPERTY, handler_read_property);
    apdu_set_confirmed_ack_handler(SERVICE_CONFIRMED_READ_PROP_MULTIPLE, rpm_ack_handler);
    apdu_set_error_handler(SERVICE_CONFIRMED_READ_PROP_MULTIPLE, error_handler);
    apdu_set_confirmed_simple_ack_handler(SERVICE_CONFIRMED_WRITE_PROPERTY, write_ack_handler);
    apdu_set_error_handler(SERVICE_CONFIRMED_WRITE_PROPERTY, error_handler);
    apdu_set_abort_handler(abort_handler);
    apdu_set_reject_handler(reject_handler);
}

/* ---------- requests ---------- */

static void handle_read(int fd, const char *id, char *args)
{
    BACNET_READ_ACCESS_DATA *head = NULL;
    BACNET_READ_ACCESS_DATA *tail = NULL;
    BACNET_ADDRESS dest = { 0 };
    char *save = NULL;
    char *token = strtok_r(args, " ", &save);
    long mac;
    uint8_t invoke_id;

    if (!token) {
        answer_error(fd, id, "READ needs a MAC address and at least one object");
        return;
    }
    mac = strtol(token, NULL, 10);
    if (mac < 0 || mac > 254 || mac == My_Mac) {
        answer_error(fd, id, "bad MAC address");
        return;
    }
    while ((token = strtok_r(NULL, " ", &save)) != NULL) {
        unsigned type = 0, property = 0;
        unsigned long instance = 0;
        BACNET_READ_ACCESS_DATA *obj;
        if (sscanf(token, "%u:%lu:%u", &type, &instance, &property) != 3 || type >= MAX_BACNET_OBJECT_TYPE ||
            instance > BACNET_MAX_INSTANCE || property > MAX_BACNET_PROPERTY_ID) {
            answer_error(fd, id, "bad object reference; use <type>:<instance>:<property>");
            goto cleanup;
        }
        /* group properties of the same object into one read access specification */
        if (tail && tail->object_type == (BACNET_OBJECT_TYPE)type && tail->object_instance == instance) {
            obj = tail;
        } else {
            obj = calloc(1, sizeof(BACNET_READ_ACCESS_DATA));
            if (!obj) {
                answer_error(fd, id, "out of memory");
                goto cleanup;
            }
            obj->object_type = (BACNET_OBJECT_TYPE)type;
            obj->object_instance = (uint32_t)instance;
            if (tail) {
                tail->next = obj;
            } else {
                head = obj;
            }
            tail = obj;
        }
        BACNET_PROPERTY_REFERENCE *ref = calloc(1, sizeof(BACNET_PROPERTY_REFERENCE));
        if (!ref) {
            answer_error(fd, id, "out of memory");
            goto cleanup;
        }
        ref->propertyIdentifier = (BACNET_PROPERTY_ID)property;
        ref->propertyArrayIndex = BACNET_ARRAY_ALL;
        ref->next = obj->listOfProperties;
        obj->listOfProperties = ref;
    }
    if (!head) {
        answer_error(fd, id, "READ needs at least one object");
        return;
    }
    /* properties were added in reverse; put them back in order */
    for (BACNET_READ_ACCESS_DATA *obj = head; obj; obj = obj->next) {
        BACNET_PROPERTY_REFERENCE *prev = NULL, *cur = obj->listOfProperties;
        while (cur) {
            BACNET_PROPERTY_REFERENCE *next = cur->next;
            cur->next = prev;
            prev = cur;
            cur = next;
        }
        obj->listOfProperties = prev;
    }
    dest.mac_len = 1;
    dest.mac[0] = (uint8_t)mac;
    dest.net = 0;
    dest.len = 0;
    invoke_id = Send_Read_Property_Multiple_Request_Address(&dest, MSTP_MAX_APDU, Tx_Buf, sizeof(Tx_Buf), head);
    if (invoke_id == 0) {
        answer_error(fd, id, "busy: no free invoke ID or the request is too long");
    } else {
        Pending[invoke_id].active = true;
        Pending[invoke_id].write = false;
        Pending[invoke_id].fd = fd;
        Pending[invoke_id].mac = (uint8_t)mac;
        snprintf(Pending[invoke_id].id, sizeof(Pending[invoke_id].id), "%s", id);
    }
cleanup:
    while (head) {
        BACNET_READ_ACCESS_DATA *next = head->next;
        BACNET_PROPERTY_REFERENCE *ref = head->listOfProperties;
        while (ref) {
            BACNET_PROPERTY_REFERENCE *n = ref->next;
            free(ref);
            ref = n;
        }
        free(head);
        head = next;
    }
}

static void handle_write(int fd, const char *id, char *args)
{
    long mac = -1, priority = 0;
    unsigned type = 0, property = 0;
    unsigned long instance = 0;
    char tag[16] = "", value[64] = "";
    BACNET_APPLICATION_DATA_VALUE v = { 0 };
    BACNET_ADDRESS dest = { 0 };
    uint8_t invoke_id;

    if (sscanf(args, "%ld %u:%lu:%u %ld %15s %63s", &mac, &type, &instance, &property, &priority, tag, value) != 7) {
        answer_error(fd, id, "WRITE needs <mac> <type>:<instance>:<property> <priority> <tag> <value>");
        return;
    }
    if (mac < 0 || mac > 254 || mac == My_Mac || type >= MAX_BACNET_OBJECT_TYPE || instance > BACNET_MAX_INSTANCE ||
        property > MAX_BACNET_PROPERTY_ID || priority < 0 || priority > 16) {
        answer_error(fd, id, "bad MAC address, object reference or priority");
        return;
    }
    if (strcmp(tag, "real") == 0) {
        v.tag = BACNET_APPLICATION_TAG_REAL;
        v.type.Real = strtof(value, NULL);
    } else if (strcmp(tag, "enum") == 0) {
        v.tag = BACNET_APPLICATION_TAG_ENUMERATED;
        v.type.Enumerated = (uint32_t)strtoul(value, NULL, 10);
    } else if (strcmp(tag, "unsigned") == 0) {
        v.tag = BACNET_APPLICATION_TAG_UNSIGNED_INT;
        v.type.Unsigned_Int = (BACNET_UNSIGNED_INTEGER)strtoull(value, NULL, 10);
    } else if (strcmp(tag, "null") == 0) {
        v.tag = BACNET_APPLICATION_TAG_NULL;
    } else {
        answer_error(fd, id, "the value tag is real, enum, unsigned or null");
        return;
    }
    dest.mac_len = 1;
    dest.mac[0] = (uint8_t)mac;
    invoke_id = Send_Write_Property_Request_Address(&dest, MSTP_MAX_APDU, (BACNET_OBJECT_TYPE)type,
        (uint32_t)instance, (BACNET_PROPERTY_ID)property, &v, (uint8_t)priority, BACNET_ARRAY_ALL);
    if (invoke_id == 0) {
        answer_error(fd, id, "busy: no free invoke ID");
        return;
    }
    Pending[invoke_id].active = true;
    Pending[invoke_id].write = true;
    Pending[invoke_id].fd = fd;
    Pending[invoke_id].mac = (uint8_t)mac;
    snprintf(Pending[invoke_id].id, sizeof(Pending[invoke_id].id), "%s", id);
}

static void handle_whois(int fd, const char *id, char *args)
{
    long low = -1, high = -1, wait = 3000;
    int slot = -1;
    if (sscanf(args, "%ld %ld %ld", &low, &high, &wait) < 3 || wait < 100 || wait > 60000) {
        answer_error(fd, id, "WHOIS needs <low> <high> <wait_ms> (wait 100 to 60000)");
        return;
    }
    for (int w = 0; w < MAX_WHOIS; w++) {
        if (!Whois[w].active) {
            slot = w;
            break;
        }
    }
    if (slot < 0) {
        answer_error(fd, id, "busy: too many Who-Is requests at once");
        return;
    }
    memset(&Whois[slot], 0, sizeof(Whois[slot]));
    Whois[slot].active = true;
    Whois[slot].fd = fd;
    Whois[slot].low = (int32_t)low;
    Whois[slot].high = (int32_t)high;
    Whois[slot].deadline_ms = now_ms() + (uint64_t)wait;
    snprintf(Whois[slot].id, sizeof(Whois[slot].id), "%s", id);
    Send_WhoIs((int32_t)low, (int32_t)high);
}

static void handle_stats(int fd, const char *id)
{
    struct dlmstp_statistics s = { 0 };
    OUT o = { 0 };
    dlmstp_fill_statistics(&s);
    out_raw(&o, "{\"id\":");
    out_str(&o, id);
    out_fmt(&o,
        ",\"ok\":true,\"mac\":%ld,\"transmitFrames\":%lu,\"receiveValidFrames\":%lu,"
        "\"receiveInvalidFrames\":%lu,\"receiveFramesNotForUs\":%lu,\"transmitPdus\":%lu,"
        "\"receivePdus\":%lu,\"lostToken\":%lu,\"badCrc\":%lu,\"pollForMaster\":%lu}",
        My_Mac, (unsigned long)s.transmit_frame_counter, (unsigned long)s.receive_valid_frame_counter,
        (unsigned long)s.receive_invalid_frame_counter, (unsigned long)s.receive_valid_frame_not_for_us_counter,
        (unsigned long)s.transmit_pdu_counter, (unsigned long)s.receive_pdu_counter,
        (unsigned long)s.lost_token_counter, (unsigned long)s.bad_crc_counter,
        (unsigned long)s.poll_for_master_counter);
    send_line(fd, &o);
}

static void handle_line(int fd, char *line)
{
    char *save = NULL;
    char *cmd = strtok_r(line, " ", &save);
    char *id = strtok_r(NULL, " ", &save);
    static char empty[1] = "";
    char *rest = save ? save : empty;
    if (!cmd) {
        return;
    }
    if (!id) {
        answer_error(fd, "", "every request needs a request ID");
        return;
    }
    if (strcmp(cmd, "PING") == 0) {
        OUT o = { 0 };
        out_raw(&o, "{\"id\":");
        out_str(&o, id);
        out_fmt(&o, ",\"ok\":true,\"service\":\"bms-mstp\",\"version\":\"%s\",\"bacnetStack\":\"%s\","
                    "\"mac\":%ld,\"instance\":%lu}",
            SERVICE_VERSION, BACNET_VERSION_TEXT, My_Mac, (unsigned long)My_Instance);
        send_line(fd, &o);
    } else if (strcmp(cmd, "STATS") == 0) {
        handle_stats(fd, id);
    } else if (strcmp(cmd, "WHOIS") == 0) {
        handle_whois(fd, id, rest);
    } else if (strcmp(cmd, "READ") == 0) {
        handle_read(fd, id, rest);
    } else if (strcmp(cmd, "WRITE") == 0) {
        handle_write(fd, id, rest);
    } else {
        answer_error(fd, id, "unknown request; use PING, STATS, WHOIS, READ or WRITE");
    }
}

static void drop_client(int index)
{
    int fd = Clients[index].fd;
    for (int i = 0; i < 256; i++) {
        if (Pending[i].active && Pending[i].fd == fd) {
            Pending[i].fd = -1; /* the answer is dropped when it arrives */
        }
    }
    for (int w = 0; w < MAX_WHOIS; w++) {
        if (Whois[w].active && Whois[w].fd == fd) {
            Whois[w].fd = -1;
        }
    }
    close(fd);
    Clients[index].fd = -1;
    Clients[index].in_len = 0;
}

static void read_client(int index)
{
    CLIENT *c = &Clients[index];
    ssize_t n = read(c->fd, c->in + c->in_len, sizeof(c->in) - c->in_len - 1);
    if (n <= 0) {
        drop_client(index);
        return;
    }
    c->in_len += (size_t)n;
    c->in[c->in_len] = 0;
    for (;;) {
        char *nl = memchr(c->in, '\n', c->in_len);
        if (!nl) {
            if (c->in_len >= sizeof(c->in) - 1) {
                answer_error(c->fd, "", "request line too long");
                c->in_len = 0;
            }
            return;
        }
        *nl = 0;
        if (nl > c->in && nl[-1] == '\r') {
            nl[-1] = 0;
        }
        handle_line(c->fd, c->in);
        if (Clients[index].fd < 0) {
            return;
        }
        size_t used = (size_t)(nl - c->in) + 1;
        memmove(c->in, nl + 1, c->in_len - used);
        c->in_len -= used;
    }
}

static void check_timeouts(void)
{
    uint64_t now = now_ms();
    for (int i = 1; i < 256; i++) {
        if (Pending[i].active && tsm_invoke_id_failed((uint8_t)i)) {
            answer_error(Pending[i].fd, Pending[i].id, "no answer (timeout)");
            tsm_free_invoke_id((uint8_t)i);
            finish_read((uint8_t)i);
        }
    }
    for (int w = 0; w < MAX_WHOIS; w++) {
        PENDING_WHOIS *p = &Whois[w];
        if (!p->active || now < p->deadline_ms) {
            continue;
        }
        OUT o = { 0 };
        out_raw(&o, "{\"id\":");
        out_str(&o, p->id);
        out_raw(&o, ",\"ok\":true,\"devices\":[");
        for (unsigned i = 0; i < p->count; i++) {
            out_fmt(&o, "%s{\"instance\":%lu,\"mac\":%u,\"maxApdu\":%u,\"segmentation\":%d,\"vendor\":%u}",
                i ? "," : "", (unsigned long)p->found[i].instance, (unsigned)p->found[i].mac,
                p->found[i].max_apdu, p->found[i].segmentation, (unsigned)p->found[i].vendor);
        }
        out_raw(&o, "]}");
        send_line(p->fd, &o);
        p->active = false;
    }
}

static int open_listener(const char *listen_at)
{
    char host[64] = "127.0.0.1";
    int port = 47900;
    const char *colon = strrchr(listen_at, ':');
    struct sockaddr_in addr = { 0 };
    int fd, one = 1;
    if (colon) {
        size_t n = (size_t)(colon - listen_at);
        if (n > 0 && n < sizeof(host)) {
            memcpy(host, listen_at, n);
            host[n] = 0;
        }
        port = atoi(colon + 1);
    }
    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, host, &addr.sin_addr) != 1 || bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0 ||
        listen(fd, 4) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

int main(int argc, char *argv[])
{
    const char *listen_at = "127.0.0.1:47900";
    long apdu_timeout_ms = 3000;
    long apdu_retries = 1;
    BACNET_ADDRESS src = { 0 };
    uint64_t last_ms;
    int listener;
    const char *env;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--listen") == 0 && i + 1 < argc) {
            listen_at = argv[++i];
        } else if (strcmp(argv[i], "--instance") == 0 && i + 1 < argc) {
            My_Instance = (uint32_t)strtoul(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--apdu-timeout") == 0 && i + 1 < argc) {
            apdu_timeout_ms = strtol(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--apdu-retries") == 0 && i + 1 < argc) {
            apdu_retries = strtol(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--version") == 0) {
            printf("bms-mstp %s (bacnet-stack %s)\n", SERVICE_VERSION, BACNET_VERSION_TEXT);
            return 0;
        } else {
            fprintf(stderr, "usage: bms-mstp [--listen ADDRESS:PORT] [--instance N] [--apdu-timeout MS] "
                            "[--apdu-retries N]\n"
                            "The trunk comes from BACNET_IFACE, BACNET_MSTP_BAUD, BACNET_MSTP_MAC,\n"
                            "BACNET_MAX_MASTER and BACNET_MAX_INFO_FRAMES.\n");
            return 2;
        }
    }
    if (apdu_timeout_ms < 100 || apdu_timeout_ms > 60000 || apdu_retries < 0 || apdu_retries > 10) {
        fprintf(stderr, "bms-mstp: --apdu-timeout must be 100 to 60000 and --apdu-retries 0 to 10\n");
        return 2;
    }
    if (My_Instance > BACNET_MAX_INSTANCE) {
        fprintf(stderr, "bms-mstp: --instance must be 0 to %u\n", BACNET_MAX_INSTANCE);
        return 2;
    }
    env = getenv("BACNET_MSTP_MAC");
    My_Mac = env ? strtol(env, NULL, 10) : 127;

    for (int i = 0; i < MAX_CLIENTS; i++) {
        Clients[i].fd = -1;
    }
    for (int i = 0; i < 256; i++) {
        Pending[i].fd = -1;
    }
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);

    address_init();
    init_service_handlers();
    dlenv_init();
    atexit(datalink_cleanup);
    /* after dlenv_init, which sets its own MS/TP defaults */
    apdu_timeout_set((uint16_t)apdu_timeout_ms);
    apdu_retries_set((uint8_t)apdu_retries);

    listener = open_listener(listen_at);
    if (listener < 0) {
        fprintf(stderr, "bms-mstp: cannot listen on %s: %s\n", listen_at, strerror(errno));
        return 1;
    }
    fprintf(stderr, "bms-mstp %s: MS/TP MAC %ld, device %lu, API on %s, timeout %ld ms x %ld tries\n",
        SERVICE_VERSION, My_Mac, (unsigned long)My_Instance, listen_at, apdu_timeout_ms, apdu_retries + 1);

    last_ms = now_ms();
    while (!Stop) {
        fd_set readable;
        struct timeval tv = { 0, 0 };
        int max_fd = listener;
        uint64_t now = now_ms();
        uint16_t pdu_len;

        if (now > last_ms) {
            tsm_timer_milliseconds((uint16_t)((now - last_ms) > 60000 ? 60000 : (now - last_ms)));
            datalink_maintenance_timer((uint16_t)((now - last_ms) / 1000));
            last_ms = now;
        }
        check_timeouts();

        FD_ZERO(&readable);
        FD_SET(listener, &readable);
        for (int i = 0; i < MAX_CLIENTS; i++) {
            if (Clients[i].fd >= 0) {
                FD_SET(Clients[i].fd, &readable);
                if (Clients[i].fd > max_fd) {
                    max_fd = Clients[i].fd;
                }
            }
        }
        if (select(max_fd + 1, &readable, NULL, NULL, &tv) > 0) {
            if (FD_ISSET(listener, &readable)) {
                int fd = accept(listener, NULL, NULL);
                int slot = -1;
                for (int i = 0; i < MAX_CLIENTS && fd >= 0; i++) {
                    if (Clients[i].fd < 0) {
                        slot = i;
                        break;
                    }
                }
                if (fd >= 0 && slot >= 0) {
                    Clients[slot].fd = fd;
                    Clients[slot].in_len = 0;
                } else if (fd >= 0) {
                    const char *msg = "{\"ok\":false,\"error\":\"too many clients\"}\n";
                    (void)!write(fd, msg, strlen(msg));
                    close(fd);
                }
            }
            for (int i = 0; i < MAX_CLIENTS; i++) {
                if (Clients[i].fd >= 0 && FD_ISSET(Clients[i].fd, &readable)) {
                    read_client(i);
                }
            }
        }

        /* waits up to 5 ms for a frame; the MS/TP state machine runs in its own thread */
        pdu_len = datalink_receive(&src, &Rx_Buf[0], MAX_MPDU, 5);
        if (pdu_len) {
            npdu_handler(&src, &Rx_Buf[0], pdu_len);
        }
    }
    fprintf(stderr, "bms-mstp: stopping\n");
    close(listener);
    return 0;
}
