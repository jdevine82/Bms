# Pi hardware and OS

Date: 1 Nov 2026. Status: decided.

The Pi HMI and standalone mode run on a Raspberry Pi 4 or Pi 5 with 64-bit Raspberry Pi OS only. The 32-bit OS is not supported or tested; a Pi with 32-bit Raspberry Pi OS is reflashed with the 64-bit image before install, and the provisioning script refuses to install on a 32-bit OS. Reasons: ready-made 64-bit Python packages, Raspberry Pi's recommendation for Pi 4/5, and a single build and test target.

Spec: 4, 13
