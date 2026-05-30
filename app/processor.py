"""
processor.py — data processing pipeline

Responsibilities:
  - Parse raw CSV strings from reader.py into PacketRecords
  - Apply MAC whitelist / blacklist filtering
  - Compute rolling stats per channel
  - Compute interpolated 2D/3D grid for visualiser.py
  - Trigger notifications via notifier.py
  - Detect suspicious activity (strong signal, out-of-hours, new device)

Rust acceleration:
  If the rf_core Rust module is compiled and available, heavy math
  (griddata interpolation + gaussian smoothing) is offloaded to it.
  Falls back to scipy automatically if Rust module is not found.
"""

import time
import queue
from datetime import datetime
from collections import defaultdict, deque

import numpy as np
import pandas as pd
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter

from logger import PacketRecord, CSVLogger
from notifier import NtfyNotifier
from config import (
    CHANNEL_RANGE, RSSI_MIN, RSSI_MAX,
    MAX_HISTORY, GRID_RESOLUTION, SMOOTHING_SIGMA,
    RSSI_STRONG_THRESH, DEFAULT_WHITELIST, DEFAULT_BLACKLIST,
)

# ── Try to load Rust-accelerated core ─────────────────────────────
try:
    import rf_core
    _RUST_AVAILABLE = True
    print("  ✔ rf_core Rust module loaded — using accelerated processing")
except ImportError:
    _RUST_AVAILABLE = False
    print("  ℹ rf_core not found — using Python/scipy fallback (run 'maturin develop' to build)")


# ══════════════════════════════════════════════
#  MAC FILTER ENGINE
# ══════════════════════════════════════════════

class FilterEngine:
    """
    Whitelist / blacklist MAC filter.
    - Passive mode (default): all MACs pass, blacklist still applies
    - Whitelist mode: only whitelisted MACs pass
    """

    def __init__(self, whitelist=None, blacklist=None, whitelist_mode: bool = False):
        self.whitelist      = set(m.upper() for m in (whitelist or DEFAULT_WHITELIST))
        self.blacklist      = set(m.upper() for m in (blacklist or DEFAULT_BLACKLIST))
        self.whitelist_mode = whitelist_mode
        self.seen_macs      = set()
        self.blocked_count  = 0

    def allow(self, mac: str) -> bool:
        mac = mac.upper()
        self.seen_macs.add(mac)
        if mac in self.blacklist:
            self.blocked_count += 1
            return False
        if self.whitelist_mode and self.whitelist and mac not in self.whitelist:
            self.blocked_count += 1
            return False
        return True

    def add_to_whitelist(self, mac: str):
        self.whitelist.add(mac.upper())

    def add_to_blacklist(self, mac: str):
        self.blacklist.add(mac.upper())

    @property
    def unique_devices(self) -> int:
        return len(self.seen_macs)


# ══════════════════════════════════════════════
#  PACKET PARSER
# ══════════════════════════════════════════════

def parse_packet(raw: str, start_time: float, node_id: int = 1) -> PacketRecord | None:
    """
    Parse a raw CSV line from the ESP32 or mock reader into a PacketRecord.
    Expected format: TYPE,MAC,CHANNEL,RSSI
    Returns None on any parse error or out-of-range value.
    """
    parts = raw.strip().split(",")
    if len(parts) != 4:
        return None
    try:
        sig_type = parts[0]
        mac      = parts[1].upper()
        channel  = int(parts[2])
        rssi     = int(parts[3])

        if sig_type not in ("ROUTER_BEACON", "DEVICE_PROBE", "DATA_FRAME"):
            return None
        if not (CHANNEL_RANGE[0] <= channel <= CHANNEL_RANGE[1]):
            return None
        if not (RSSI_MIN <= rssi <= RSSI_MAX):
            return None

        return PacketRecord(
            timestamp = datetime.now(),
            elapsed   = time.time() - start_time,
            sig_type  = sig_type,
            mac       = mac,
            channel   = channel,
            rssi      = rssi,
            node_id   = node_id,
        )
    except (ValueError, IndexError):
        return None


# ══════════════════════════════════════════════
#  GRID COMPUTATION  (Python fallback)
# ══════════════════════════════════════════════

def _compute_grid_python(history: deque):
    """
    Scipy fallback for grid interpolation.
    Called automatically when Rust module is not available.
    """
    if len(history) < 4:
        return None, None, None

    df = pd.DataFrame(
        [(r.elapsed, r.channel, r.rssi) for r in history],
        columns=["t", "ch", "rssi"]
    )
    x  = df["t"].values
    y  = df["ch"].values
    z  = df["rssi"].values
    xi = np.linspace(x.min(), x.max(), GRID_RESOLUTION)
    yi = np.linspace(CHANNEL_RANGE[0], CHANNEL_RANGE[1], GRID_RESOLUTION)
    xi, yi = np.meshgrid(xi, yi)

    try:
        zi = griddata((x, y), z, (xi, yi), method="cubic",  fill_value=RSSI_MIN)
    except Exception:
        zi = griddata((x, y), z, (xi, yi), method="linear", fill_value=RSSI_MIN)

    zi = gaussian_filter(zi, sigma=SMOOTHING_SIGMA)
    zi = np.clip(zi, RSSI_MIN, RSSI_MAX)
    return xi, yi, zi


def compute_grid(history: deque):
    """
    Compute interpolated heatmap grid.
    Uses Rust rf_core if available, falls back to scipy.
    Returns (xi, yi, zi) numpy arrays ready for plotting.
    """
    if _RUST_AVAILABLE:
        try:
            elapsed_vals  = np.array([r.elapsed  for r in history], dtype=np.float64)
            channel_vals  = np.array([r.channel  for r in history], dtype=np.float64)
            rssi_vals     = np.array([r.rssi     for r in history], dtype=np.float64)
            xi, yi, zi = rf_core.compute_grid(
                elapsed_vals, channel_vals, rssi_vals,
                GRID_RESOLUTION, CHANNEL_RANGE[0], CHANNEL_RANGE[1],
                RSSI_MIN, SMOOTHING_SIGMA
            )
            return xi, yi, zi
        except Exception as e:
            print(f"  ⚠ Rust grid compute failed ({e}), falling back to scipy")

    return _compute_grid_python(history)


# ══════════════════════════════════════════════
#  MAIN PROCESSOR
# ══════════════════════════════════════════════

class Processor:
    """
    Central data pipeline.
    Drains the reader queue, filters, parses, logs, and updates
    all rolling state consumed by the visualiser.
    """

    def __init__(self, filter_engine: FilterEngine, logger: CSVLogger,
                 notifier: NtfyNotifier, start_time: float):
        self.filter      = filter_engine
        self.logger      = logger
        self.notifier    = notifier
        self.start_time  = start_time

        # Rolling history consumed by visualiser
        self.history: deque[PacketRecord] = deque(maxlen=MAX_HISTORY)

        # Per-channel rolling RSSI stats
        self.channel_stats: dict[int, deque] = defaultdict(lambda: deque(maxlen=60))

        # Event log for UI display
        self.event_log: deque = deque(maxlen=12)

        # Known MACs for new-device detection
        self._known_macs: set = set()

        # Suspicious hours (alert if activity outside these hours)
        self.quiet_hours: tuple = (23, 6)   # 11pm to 6am

    def drain(self, data_queue: queue.Queue):
        """
        Drain all pending items from the reader queue.
        Call this on every animation frame from the visualiser.
        """
        while not data_queue.empty():
            try:
                node_id, raw = data_queue.get_nowait()
                rec = parse_packet(raw, self.start_time, node_id)
                if rec:
                    self._ingest(rec)
            except queue.Empty:
                break

    def _ingest(self, rec: PacketRecord):
        """Process a single validated packet."""
        if not self.filter.allow(rec.mac):
            return

        # Store and log
        self.history.append(rec)
        self.channel_stats[rec.channel].append(rec.rssi)
        self.logger.log(rec)

        # New device detection
        if rec.mac not in self._known_macs:
            self._known_macs.add(rec.mac)
            self.event_log.appendleft(
                (datetime.now(), f"NEW  {rec.mac[:17]}", "amber")
            )
            self.notifier.alert_new_device(rec.mac, rec.channel)
            if rec.mac in self.filter.whitelist:
                self.notifier.alert_whitelist_seen(rec.mac, rec.rssi)

        # Strong signal detection
        if rec.rssi >= RSSI_STRONG_THRESH:
            self.event_log.appendleft(
                (datetime.now(), f"STRONG {rec.rssi}dBm  ch{rec.channel}", "red")
            )
            self.notifier.alert_strong_signal(rec.mac, rec.rssi, rec.channel)

        # Out-of-hours detection
        hour = rec.timestamp.hour
        start_quiet, end_quiet = self.quiet_hours
        if start_quiet > end_quiet:
            is_quiet = hour >= start_quiet or hour < end_quiet
        else:
            is_quiet = start_quiet <= hour < end_quiet

        if is_quiet:
            self.event_log.appendleft(
                (datetime.now(), f"OOH  {rec.mac[:11]}  {hour:02d}h", "red")
            )
            self.notifier.alert_suspicious_hour(rec.mac, hour)

    def get_grid(self):
        """Return computed heatmap grid — delegates to Rust or scipy."""
        return compute_grid(self.history)

    @property
    def total_packets(self) -> int:
        return len(self.history)