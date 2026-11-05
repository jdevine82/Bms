# Extra rule for bacnet-stack's apps/Makefile: make BACDL=mstp -C apps -f Makefile -f <this file> bms-mstp
.PHONY: bms-mstp
bms-mstp: $(BACNET_LIB_TARGET)
	$(MAKE) -B -C $@
