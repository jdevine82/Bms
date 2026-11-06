/**
 * @file
 * @brief bms-mstp's BACnet router (milestone 8). See router.c.
 */
#ifndef BMS_MSTP_ROUTER_H
#define BMS_MSTP_ROUTER_H

#include <stdbool.h>
#include <stdint.h>
#include "bacnet/bacdef.h"
#include "bacnet/npdu.h"

typedef struct {
    unsigned long from_ip;   /* packets received on the BACnet/IP port */
    unsigned long from_mstp; /* packets received on the trunk */
    unsigned long to_ip;     /* packets sent out the BACnet/IP port */
    unsigned long to_mstp;   /* packets sent onto the trunk */
    unsigned long rejects;   /* Reject-Message-To-Network received */
    unsigned long clashes;   /* another router announced one of our network numbers */
} ROUTER_STATS;

extern ROUTER_STATS Router_Stats;

bool router_start(const char *iface, uint16_t ip_net, uint16_t mstp_net);
bool router_enabled(void);
uint16_t router_ip_net(void);
uint16_t router_mstp_net(void);
void router_mstp_received(BACNET_ADDRESS *src, uint8_t *pdu, uint16_t pdu_len);
void router_poll(void);
int router_send_local(BACNET_ADDRESS *dest, BACNET_NPDU_DATA *npdu_data, uint8_t *pdu, unsigned pdu_len);
void router_cleanup(void);

#endif
