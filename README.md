# 📡 PASSIVE Wi-Fi RF HEATMAP & SIGNAL INTELLIGENCE

**A 3D spatial visualization and intrusion detection system for 802.11 networks.**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python&logoColor=white)](#)
[![C++ ESP32](https://img.shields.io/badge/Firmware-C%2B%2B%20(ESP32)-orange.svg?logo=c%2B%2B&logoColor=white)](#)
[![Rust Core](https://img.shields.io/badge/Backend-Rust%20Accelerated-black.svg?logo=rust)](#)
[![Matplotlib](https://img.shields.io/badge/UI-Matplotlib-11557c.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-success.svg)](#)

*Monitor, map, and analyze ambient Wi-Fi traffic in real-time—without ever connecting to a network.*

[Features](#-core-features) • [Installation](#-installation-and-setup) • [Usage](#-usage) • [Architecture](#-architecture) 

</div>

---

## ⚡ Overview

This project uses an **ESP32** running in promiscuous mode to passively sniff 802.11 frames (Router Beacons, Device Probes, and Data Frames) across 2.4GHz channels. The raw signal data is streamed via serial to a Python engine that reconstructs the RF environment in a **7-panel 3D Matplotlib dashboard**. 

It features an optional **Rust backend** for high-speed interpolation, an intelligent MAC filtering engine, and real-time mobile push notifications via **Ntfy.sh** for physical security and device tracking.

---

## ✨ Core Features

* 🥷 **100% Passive Sniffing:** Tracks devices via MAC address and RSSI without connecting to any AP.
* 🗺️ **3D Surface & Heatmaps:** Real-time spatial and temporal visualization of channel saturation.
* 🦀 **Rust Acceleration:** Heavy grid data interpolation and Gaussian smoothing offloaded to a compiled `rf_core` Rust library (with automatic Scipy fallback).
* 📱 **Ntfy Push Alerts:** Instantly pushes mobile alerts for strong signals, out-of-hours activity, or unrecognized devices.
* 🛡️ **Signal Intelligence:** Whitelist/Blacklist filtering, new device discovery, and channel hopping.
* 🧪 **Mock Simulation:** Fully functional without hardware using realistically modeled overlapping sine-wave signal drift.

---

## 🖥️ The Dashboard

The UI is built with a custom dark-industrial theme and runs at 300ms intervals. 

| Panel | Description |
| :--- | :--- |
| **3D Signal Surface** | Auto-rotating 3D projection of time vs. channel vs. RSSI (dBm). |
| **Top-Down Heatmap** | 2D projection of the RF environment using custom color mapping. |
| **RSSI Waveform** | Rolling 120-sample line graph of recent signal strengths. |
| **Channel Activity** | Bar chart showing average saturation across channels 1–11. |
| **Frame Types** | Live pie chart of intercepted traffic (`ROUTER_BEACON`, `DEVICE_PROBE`, `DATA_FRAME`). |
| **Device Registry** | Real-time leaderboard of the most active MAC addresses and their average RSSI. |
| **Event Log & Stats** | Live feed of system triggers (e.g., strong signals, whitelisted devices) and metrics. |

---

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


<div align="center">study space with the rig running to gather signal density measurements.
