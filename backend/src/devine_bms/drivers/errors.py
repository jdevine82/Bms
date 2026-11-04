"""Read failures shared by every driver (main spec 5.2.1).

- BusDown: the connection itself is gone; every device on it is down.
- DeviceDown: the connection is fine but this device does not answer.
- DeviceFault: the device answered with an error.
"""


class DeviceDown(Exception):
    pass


class BusDown(DeviceDown):
    pass


class DeviceFault(Exception):
    pass
