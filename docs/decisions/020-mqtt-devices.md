# MQTT field devices

Date: 5 Nov 2026. Status: decided.

MQTT points take their value from one exact topic, either the whole payload (plain) or a field picked by a JSON path. Devices can name an availability topic (LWT) whose offline text makes their points Bad (down). Sparkplug B is not supported. Broker passwords come from an environment variable named in the config, never from the config file. The BMS only subscribes in this milestone.

Spec: main spec 5.1; docs/specs/m4d-mqtt.md
