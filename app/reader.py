"""
reader.py — data ingestion layer

Two modes:
  - SerialReader   : reads live data from ESP32 over USB serial
  - MockReader     : generates realistic simulated packets (no hardware needed)

Both push raw CSV strings into a shared queue consumed by processor.py.
Both run as background daemon threads.
"""

import time
import math
import random
import queue
import threading

try:
    import serial
    import serial.tools.list_ports
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False

from config import BAUD_RATE, SERIAL_TIMEOUT, SERIAL_RETRY_SEC, CHANNEL_RANGE, RSSI_MIN, RSSI_MAX


# ══════════════════════════════════════════════
#  PORT AUTO-DETECTION
# ══════════════════════════════════════════════

def auto_detect_port() -> str | None:
    """
    Scan all serial ports and return the first one that looks like an ESP32.
    Matches common USB-UART bridge chips: CP210x, CH340, FTDI.
    """
    if not _SERIAL_AVAILABLE:
        return None
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        if any(k in desc for k in ("cp210", "ch340", "ftdi", "uart", "esp", "usb serial")):
            return p.device
    ports = serial.tools.list_ports.comports()
    return ports[0].device if ports else None


def list_ports() -> list[str]:
    """Return all available serial port names."""
    if not _SERIAL_AVAILABLE:
        return []
    return [p.device for p in serial.tools.list_ports.comports()]


# ══════════════════════════════════════════════
#  LIVE SERIAL READER
# ══════════════════════════════════════════════

class SerialReader:
    """
    Reads CSV lines from an ESP32 over USB serial.
    Automatically retries connection if the port drops.
    Runs in a background daemon thread.

    Output format expected from firmware:
        TYPE,MAC,CHANNEL,RSSI
        ROUTER_BEACON,AA:BB:CC:DD:EE:FF,6,-62
    """

    def __init__(self, port: str, data_queue: queue.Queue, stop_event: threading.Event,
                 node_id: int = 1):
        self.port        = port
        self.queue       = data_queue
        self.stop        = stop_event
        self.node_id     = node_id
        self.connected   = False
        self.packet_count = 0
        self._thread     = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self.stop.is_set():
            try:
                print(f"  ► Node {self.node_id}: connecting to {self.port}...")
                ser = serial.Serial(self.port, BAUD_RATE, timeout=SERIAL_TIMEOUT)
                time.sleep(2)
                ser.flushInput()
                self.connected = True
                print(f"  ✔ Node {self.node_id}: connected on {self.port}")

                while not self.stop.is_set():
                    if ser.in_waiting > 0:
                        try:
                            line = ser.readline().decode("utf-8", errors="ignore").strip()
                            if line and line != "TYPE,MAC,CHANNEL,RSSI":
                                self.queue.put((self.node_id, line))
                                self.packet_count += 1
                        except Exception:
                            pass

            except Exception as e:
                self.connected = False
                print(f"  ✘ Node {self.node_id}: {e} — retrying in {SERIAL_RETRY_SEC}s...")
                time.sleep(SERIAL_RETRY_SEC)

            finally:
                try:
                    ser.close()
                except Exception:
                    pass

    @property
    def status(self) -> str:
        return "ONLINE" if self.connected else "OFFLINE"


# ══════════════════════════════════════════════
#  MOCK READER
# ══════════════════════════════════════════════

# Simulated device MAC addresses
FAKE_MACS = [
    "AA:BB:CC:11:22:33",
    "AA:BB:CC:44:55:66",
    "AA:BB:CC:77:88:99",
    "DD:EE:FF:11:22:33",
    "DD:EE:FF:44:55:66",
    "DD:EE:FF:77:88:99",
    "11:22:33:AA:BB:CC",
    "44:55:66:AA:BB:CC",
    "77:88:99:AA:BB:CC",
    "11:22:33:DD:EE:FF",
    "44:55:66:DD:EE:FF",
    "77:88:99:DD:EE:FF",
]


class MockReader:
    """
    Generates realistic simulated Wi-Fi packets — no hardware needed.
    Each fake MAC has a personality: home channel, base RSSI, drift speed.
    Uses overlapping sine waves to simulate real-world signal behaviour.
    Runs in a background daemon thread.
    """

    def __init__(self, data_queue: queue.Queue, stop_event: threading.Event,
                 fast: bool = False, node_id: int = 1):
        self.queue       = data_queue
        self.stop        = stop_event
        self.fast        = fast
        self.node_id     = node_id
        self.packet_count = 0
        self.connected   = True   # always "connected" in mock mode
        self._thread     = None
        self._profiles   = self._build_profiles()

    def _build_profiles(self) -> dict:
        profiles = {}
        for i, mac in enumerate(FAKE_MACS):
            profiles[mac] = {
                "home_ch":   (i % 11) + 1,
                "base_rssi": -50 - (i * 3),
                "drift":     random.uniform(0.2, 1.2),
                "phase":     random.uniform(0, math.pi * 2),
                "is_router": i < 3,   # first 3 MACs behave like routers
            }
        return profiles

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        t = 0.0
        sleep_interval = 0.04 if self.fast else 0.08

        while not self.stop.is_set():
            time.sleep(sleep_interval)
            t += sleep_interval

            mac = random.choices(
                FAKE_MACS,
                weights=[3, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1][:len(FAKE_MACS)]
            )[0]
            p = self._profiles[mac]

            # Frame type — routers mostly send beacons, devices send probes/data
            if p["is_router"]:
                sig_type = random.choices(
                    ["ROUTER_BEACON", "DEVICE_PROBE", "DATA_FRAME"],
                    weights=[0.7, 0.2, 0.1]
                )[0]
            else:
                sig_type = random.choices(
                    ["ROUTER_BEACON", "DEVICE_PROBE", "DATA_FRAME"],
                    weights=[0.1, 0.5, 0.4]
                )[0]

            # Channel: mostly home channel, occasionally wanders
            channel = (
                random.randint(CHANNEL_RANGE[0], CHANNEL_RANGE[1])
                if random.random() < 0.15
                else p["home_ch"]
            )

            # RSSI: multi-wave simulation
            base  = p["base_rssi"]
            wave1 = math.sin(t * p["drift"] + p["phase"]) * 18
            wave2 = math.sin(t * 0.3 + p["phase"] * 0.5) * 8
            noise = random.gauss(0, 4)
            rssi  = int(max(RSSI_MIN, min(RSSI_MAX, base + wave1 + wave2 + noise)))

            self.queue.put((self.node_id, f"{sig_type},{mac},{channel},{rssi}"))
            self.packet_count += 1

    @property
    def status(self) -> str:
        return "SIMULATION"