# Passive Wi-Fi Radio Wave Heatmap Sniffer

This project uses an ESP32-S3 microcontroller running in promiscuous mode to passively intercept and map 2.4GHz radio frequency waves (Wi-Fi beacon signals and device probes) without connecting to any networks. The logged data is streamed via serial connection to a Python environment to compute a continuous signal spatial heatmap.

## Installation & Setup

1. **Firmware Deployment:**
   * Open `firmware/firmware.ino` inside the Arduino IDE.
   * Select your target **ESP32-S3** board profile.
   * Flash the compilation code directly onto your microcontroller.

2. **Python Environment Setup:**
   * Install dependencies via terminal execution:
     ```bash
     pip install -r requirements.txt
     ```

3. **Execution Execution:**
   * Look up your microcontroller's assigned serial port address and substitute it inside the `SERIAL_PORT` variable configuration line inside `app/collector.py`.
   * Start your monitoring capture:
     ```bash
     python app/collector.py
     ```
   * Walk smoothly through your study space with the rig running to gather signal density measurements.
