# Built inside the bacnet-stack apps folder by ../build.sh, which supplies CFLAGS, LFLAGS
# and the library (bacnet-stack exports them to every app folder).
TARGET = bms-mstp
BACNET_OBJECT_DIR = $(BACNET_SRC_DIR)/bacnet/basic/object
SRC = main.c router.c \
	$(BACNET_OBJECT_DIR)/client/device-client.c \
	$(BACNET_OBJECT_DIR)/netport.c

TARGET_BIN = ${TARGET}$(TARGET_EXT)
OBJS += ${SRC:.c=.o}

all: ${BACNET_LIB_TARGET} Makefile ${TARGET_BIN}

${TARGET_BIN}: ${OBJS} Makefile ${BACNET_LIB_TARGET}
	${CC} ${PFLAGS} ${OBJS} ${LFLAGS} -lpthread -o $@

.c.o:
	${CC} -c ${CFLAGS} -std=gnu11 -Wno-unused-parameter -Wno-declaration-after-statement $*.c -o $@

.PHONY: clean
clean:
	rm -f ${TARGET_BIN} ${OBJS}
