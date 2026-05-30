"""
╔══════════════════════════════════════════════════════════════════╗
║           PASSIVE Wi-Fi RF HEATMAP — collector.py                ║
║  Real-time 3D signal map | MAC filter | Multi-node | Ntfy alerts ║
╚══════════════════════════════════════════════════════════════════╝

USAGE:
  python app/collector.py                        # auto-detect port
  python app/collector.py --port COM7            # specific port
  python app/collector.py --mock                 # simulation mode
  python app/collector.py --filter-mac           # whitelist mode
  python app/collector.py --ntfy my-channel      # push notifications
  python app/collector.py --mock --ntfy test-ch  # combine flags
"""

import serial
import serial.tools.list_ports
import time
import argparse
import threading
import queue
import csv
import os
import sys
import json
import random
import math
from datetime import datetime
from collections import defaultdict, deque

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.animation as animation
from matplotlib.colors import LinearSegmentedColormap
from mpl_toolkits.mplot3d import Axes3D
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter

# ─────────────────────────────────────────────
# OPTIONAL: Ntfy notifications (pip install requests)
# ─────────────────────────────────────────────
try:
    import requests
    NTFY_AVAILABLE = True
except ImportError:
    NTFY_AVAILABLE = False


# ══════════════════════════════════════════════
#  CONFIGURATION — edit these to suit your setup
# ══════════════════════════════════════════════

BAUD_RATE          = 115200
CSV_FILE           = "wifi_wave_log.csv"
MAX_HISTORY        = 500        # rolling window of packets kept in memory
GRID_RESOLUTION    = 60         # heatmap grid cells per axis
SMOOTHING_SIGMA    = 2.0        # gaussian blur on heatmap (higher = smoother)
UPDATE_INTERVAL_MS = 300        # UI refresh rate in milliseconds
CHANNEL_RANGE      = (1, 11)    # 2.4GHz channels to visualise
RSSI_MIN           = -95        # weakest signal shown (dBm)
RSSI_MAX           = -30        # strongest signal shown (dBm)
NTFY_BASE_URL      = "https://ntfy.sh"
NTFY_COOLDOWN_SEC  = 10         # minimum seconds between notifications

# MAC whitelist — add real MACs here to only track those devices
# Leave empty [] to capture everything (promiscuous / security mode)
DEFAULT_WHITELIST = [
    # "AA:BB:CC:DD:EE:FF",
    # "11:22:33:44:55:66",
]

# MAC blacklist — add MACs to permanently ignore (noisy neighbours etc)
DEFAULT_BLACKLIST = [
    # "FF:FF:FF:FF:FF:FF",  # broadcast — ignored by default
]


# ══════════════════════════════════════════════
#  COLOUR PALETTE  (dark industrial theme)
# ══════════════════════════════════════════════

DARK_BG      = "#0A0C0F"
PANEL_BG     = "#0F1318"
BORDER_COL   = "#1E2530"
ACCENT_CYAN  = "#00D4FF"
ACCENT_GREEN = "#00FF88"
ACCENT_RED   = "#FF3355"
ACCENT_AMBER = "#FFB800"
TEXT_PRIMARY = "#E8ECF0"
TEXT_MUTED   = "#4A5568"

# Custom heatmap: black → deep blue → cyan → green → yellow → red
RF_CMAP = LinearSegmentedColormap.from_list("rf_heat", [
    "#000000", "#001830", "#003366",
    "#0066CC", "#00AAFF", "#00FFCC",
    "#00FF66", "#AAFF00", "#FFCC00",
    "#FF6600", "#FF0000"
], N=256)


# ══════════════════════════════════════════════
#  DATA STRUCTURES
# ══════════════════════════════════════════════

class PacketRecord:
    __slots__ = ("timestamp", "elapsed", "sig_type", "mac", "channel", "rssi", "node_id")
    def __init__(self, timestamp, elapsed, sig_type, mac, channel, rssi, node_id=1):
        self.timestamp = timestamp
        self.elapsed   = elapsed
        self.sig_type  = sig_type
        self.mac       = mac
        self.channel   = channel
        self.rssi      = rssi
        self.node_id   = node_id


class FilterEngine:
    """Handles MAC whitelist / blacklist filtering."""

    def __init__(self, whitelist=None, blacklist=None, whitelist_mode=False):
        self.whitelist      = set(m.upper() for m in (whitelist or []))
        self.blacklist      = set(m.upper() for m in (blacklist or DEFAULT_BLACKLIST))
        self.whitelist_mode = whitelist_mode   # if True, ONLY show whitelisted MACs
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
    def unique_devices(self):
        return len(self.seen_macs)


class NtfyNotifier:
    """Push notifications via ntfy.sh."""

    def __init__(self, channel: str, enabled: bool = True):
        self.channel    = channel
        self.enabled    = enabled and NTFY_AVAILABLE
        self.last_sent  = 0
        self.sent_count = 0

    def send(self, title: str, message: str, priority: str = "default", tags: str = ""):
        if not self.enabled:
            return
        now = time.time()
        if now - self.last_sent < NTFY_COOLDOWN_SEC:
            return
        self.last_sent = now
        self.sent_count += 1
        try:
            requests.post(
                f"{NTFY_BASE_URL}/{self.channel}",
                data=message.encode("utf-8"),
                headers={
                    "Title":    title,
                    "Priority": priority,
                    "Tags":     tags,
                },
                timeout=4,
            )
        except Exception:
            pass  # never crash the main loop on notification failure

    def alert_strong_signal(self, mac: str, rssi: int, channel: int):
        self.send(
            title   = "⚠️  Strong Signal Detected",
            message = f"Device {mac} on ch{channel} at {rssi} dBm",
            priority= "high",
            tags    = "warning,wifi",
        )

    def alert_new_device(self, mac: str, channel: int):
        self.send(
            title   = "📡  New Device Detected",
            message = f"Unknown device {mac} appeared on ch{channel}",
            priority= "default",
            tags    = "mag,wifi",
        )

    def alert_whitelist_seen(self, mac: str, rssi: int):
        self.send(
            title   = "✅  Tracked Device Online",
            message = f"Whitelisted device {mac} detected at {rssi} dBm",
            priority= "low",
            tags    = "white_check_mark,wifi",
        )


# ══════════════════════════════════════════════
#  SERIAL / MOCK DATA SOURCE
# ══════════════════════════════════════════════

def auto_detect_port() -> str | None:
    """Scan serial ports and return the first that looks like an ESP32."""
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        if any(k in desc for k in ("cp210", "ch340", "ftdi", "uart", "esp", "usb serial")):
            return p.device
    ports = serial.tools.list_ports.comports()
    return ports[0].device if ports else None


def serial_reader(port: str, data_queue: queue.Queue, stop_event: threading.Event):
    """Background thread: reads lines from ESP32 serial and puts them in queue."""
    print(f"  ► Connecting to ESP32 on {port}...")
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
        time.sleep(2)
        ser.flushInput()
        print(f"  ✔ Serial connection established on {port}")
    except Exception as e:
        print(f"  ✘ Could not open {port}: {e}")
        stop_event.set()
        return

    while not stop_event.is_set():
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if line and line != "TYPE,MAC,CHANNEL,RSSI":
                    data_queue.put(("serial", line))
        except Exception:
            pass
    ser.close()


def mock_reader(data_queue: queue.Queue, stop_event: threading.Event):
    """Background thread: generates realistic simulated packets."""
    fake_macs = [
        f"AA:BB:CC:{random.randint(10,99):02X}:{random.randint(10,99):02X}:{random.randint(10,99):02X}"
        for _ in range(12)
    ]
    t = 0.0
    while not stop_event.is_set():
        time.sleep(0.08)
        t += 0.08
        sig_type = random.choices(
            ["ROUTER_BEACON", "DEVICE_PROBE", "DATA_FRAME"],
            weights=[0.5, 0.3, 0.2]
        )[0]
        mac      = random.choice(fake_macs)
        channel  = int((t * 1.5) % 11) + 1
        base     = -60 + math.sin(t * 0.4) * 22 + math.sin(t * 1.1) * 8
        rssi     = int(max(RSSI_MIN, min(RSSI_MAX, base + random.gauss(0, 5))))
        data_queue.put(("mock", f"{sig_type},{mac},{channel},{rssi}"))


def parse_line(raw: str, start_time: float, node_id: int = 1) -> PacketRecord | None:
    """Parse a CSV line from the ESP32 into a PacketRecord."""
    parts = raw.strip().split(",")
    if len(parts) != 4:
        return None
    try:
        sig_type = parts[0]
        mac      = parts[1].upper()
        channel  = int(parts[2])
        rssi     = int(parts[3])
        if not (CHANNEL_RANGE[0] <= channel <= CHANNEL_RANGE[1]):
            return None
        if not (RSSI_MIN <= rssi <= RSSI_MAX):
            return None
        now     = datetime.now()
        elapsed = time.time() - start_time
        return PacketRecord(now, elapsed, sig_type, mac, channel, rssi, node_id)
    except (ValueError, IndexError):
        return None


# ══════════════════════════════════════════════
#  CSV LOGGER
# ══════════════════════════════════════════════

class CSVLogger:
    def __init__(self, path: str):
        self.path   = path
        self.writer = None
        self.file   = None
        self._open()

    def _open(self):
        write_header = not os.path.exists(self.path)
        self.file   = open(self.path, "a", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        if write_header:
            self.writer.writerow(["Timestamp", "Elapsed", "NodeID", "Type", "MAC", "Channel", "RSSI"])

    def log(self, rec: PacketRecord):
        self.writer.writerow([
            rec.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
            f"{rec.elapsed:.3f}",
            rec.node_id,
            rec.sig_type,
            rec.mac,
            rec.channel,
            rec.rssi,
        ])
        self.file.flush()

    def close(self):
        if self.file:
            self.file.close()


# ══════════════════════════════════════════════
#  3D + UI VISUALISER
# ══════════════════════════════════════════════

class RFVisualiser:

    def __init__(self, filter_engine: FilterEngine, notifier: NtfyNotifier):
        self.filter   = filter_engine
        self.notifier = notifier
        self.history  = deque(maxlen=MAX_HISTORY)
        self.logger   = CSVLogger(CSV_FILE)

        # per-channel rolling stats
        self.channel_stats = defaultdict(lambda: deque(maxlen=60))

        # live event log (shown in sidebar)
        self.event_log = deque(maxlen=12)

        # track seen MACs for new-device alerts
        self.known_macs: set = set()

        # matplotlib dark theme
        plt.rcParams.update({
            "figure.facecolor":  DARK_BG,
            "axes.facecolor":    PANEL_BG,
            "axes.edgecolor":    BORDER_COL,
            "axes.labelcolor":   TEXT_PRIMARY,
            "xtick.color":       TEXT_MUTED,
            "ytick.color":       TEXT_MUTED,
            "text.color":        TEXT_PRIMARY,
            "grid.color":        BORDER_COL,
            "grid.linewidth":    0.5,
            "font.family":       "monospace",
        })

        self._build_layout()
        self._last_ntfy_strong = 0

    # ── layout ────────────────────────────────

    def _build_layout(self):
        self.fig = plt.figure(figsize=(18, 10), facecolor=DARK_BG)
        self.fig.canvas.manager.set_window_title("RF HEATMAP  //  Passive Wi-Fi Sniffer")

        gs = gridspec.GridSpec(
            3, 4,
            figure=self.fig,
            left=0.04, right=0.97,
            top=0.93,  bottom=0.06,
            hspace=0.45, wspace=0.35,
        )

        # ── title bar
        self.fig.text(
            0.5, 0.965,
            "◈  PASSIVE RF HEATMAP  //  Wi-Fi Signal Intelligence",
            ha="center", va="center",
            fontsize=13, fontweight="bold",
            color=ACCENT_CYAN, fontfamily="monospace",
        )
        self.status_text = self.fig.text(
            0.5, 0.945, "● INITIALISING...",
            ha="center", va="center",
            fontsize=8, color=ACCENT_AMBER, fontfamily="monospace",
        )

        # ── 3D surface (large, left)
        self.ax3d = self.fig.add_subplot(gs[0:2, 0:2], projection="3d")
        self._style_3d(self.ax3d, "3D  SIGNAL SURFACE")

        # ── 2D top-down heatmap
        self.ax2d = self.fig.add_subplot(gs[0:2, 2])
        self._style_ax(self.ax2d, "TOP-DOWN  HEATMAP")

        # ── RSSI timeline waveform
        self.ax_wave = self.fig.add_subplot(gs[0, 3])
        self._style_ax(self.ax_wave, "RSSI  WAVEFORM")

        # ── per-channel bar chart
        self.ax_bar = self.fig.add_subplot(gs[1, 3])
        self._style_ax(self.ax_bar, "CHANNEL  ACTIVITY")

        # ── packet type distribution
        self.ax_pie = self.fig.add_subplot(gs[2, 0])
        self._style_ax(self.ax_pie, "FRAME  TYPES")

        # ── device list / MAC table
        self.ax_mac = self.fig.add_subplot(gs[2, 1])
        self._style_ax(self.ax_mac, "DEVICE  REGISTRY")
        self.ax_mac.axis("off")

        # ── event log
        self.ax_log = self.fig.add_subplot(gs[2, 2])
        self._style_ax(self.ax_log, "EVENT  LOG")
        self.ax_log.axis("off")

        # ── stats panel
        self.ax_stats = self.fig.add_subplot(gs[2, 3])
        self._style_ax(self.ax_stats, "SYSTEM  STATS")
        self.ax_stats.axis("off")

        # colourbar placeholder
        self.cbar = None

    def _style_ax(self, ax, title):
        ax.set_facecolor(PANEL_BG)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER_COL)
            spine.set_linewidth(0.8)
        ax.set_title(
            title, fontsize=7, color=ACCENT_CYAN,
            fontfamily="monospace", pad=5, loc="left",
        )
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3)

    def _style_3d(self, ax, title):
        ax.set_facecolor(DARK_BG)
        ax.xaxis.pane.fill = False
        ax.yaxis.pane.fill = False
        ax.zaxis.pane.fill = False
        ax.xaxis.pane.set_edgecolor(BORDER_COL)
        ax.yaxis.pane.set_edgecolor(BORDER_COL)
        ax.zaxis.pane.set_edgecolor(BORDER_COL)
        ax.set_title(title, fontsize=7, color=ACCENT_CYAN, fontfamily="monospace", pad=4, loc="left")
        ax.tick_params(labelsize=6, colors=TEXT_MUTED)

    # ── ingest ────────────────────────────────

    def ingest(self, rec: PacketRecord):
        """Accept a new packet record, apply filters, update state."""
        if not self.filter.allow(rec.mac):
            return

        self.history.append(rec)
        self.channel_stats[rec.channel].append(rec.rssi)
        self.logger.log(rec)

        # new device alert
        if rec.mac not in self.known_macs:
            self.known_macs.add(rec.mac)
            self.event_log.appendleft(
                (datetime.now(), f"NEW  {rec.mac[:17]}", ACCENT_AMBER)
            )
            self.notifier.alert_new_device(rec.mac, rec.channel)
            if rec.mac in self.filter.whitelist:
                self.notifier.alert_whitelist_seen(rec.mac, rec.rssi)

        # strong signal alert
        if rec.rssi >= -45:
            self.event_log.appendleft(
                (datetime.now(), f"STRONG {rec.rssi}dBm  ch{rec.channel}", ACCENT_RED)
            )
            self.notifier.alert_strong_signal(rec.mac, rec.rssi, rec.channel)

    # ── heatmap computation ───────────────────

    def _compute_grid(self):
        if len(self.history) < 4:
            return None, None, None
        df  = pd.DataFrame([
            (r.elapsed, r.channel, r.rssi) for r in self.history
        ], columns=["t", "ch", "rssi"])

        x   = df["t"].values
        y   = df["ch"].values
        z   = df["rssi"].values

        xi  = np.linspace(x.min(), x.max(), GRID_RESOLUTION)
        yi  = np.linspace(CHANNEL_RANGE[0], CHANNEL_RANGE[1], GRID_RESOLUTION)
        xi, yi = np.meshgrid(xi, yi)

        try:
            zi = griddata((x, y), z, (xi, yi), method="cubic", fill_value=RSSI_MIN)
        except Exception:
            zi = griddata((x, y), z, (xi, yi), method="linear", fill_value=RSSI_MIN)

        zi = gaussian_filter(zi, sigma=SMOOTHING_SIGMA)
        zi = np.clip(zi, RSSI_MIN, RSSI_MAX)
        return xi, yi, zi

    # ── draw routines ─────────────────────────

    def _draw_3d(self, xi, yi, zi):
        self.ax3d.cla()
        self._style_3d(self.ax3d, "3D  SIGNAL SURFACE")

        norm = (zi - RSSI_MIN) / (RSSI_MAX - RSSI_MIN)
        colors = RF_CMAP(norm)

        self.ax3d.plot_surface(
            xi, yi, zi,
            facecolors=colors,
            linewidth=0, antialiased=True,
            alpha=0.92, shade=True,
            lightsource=matplotlib.colors.LightSource(azdeg=45, altdeg=35),
        )
        # wireframe overlay for that "radar" feel
        self.ax3d.plot_wireframe(
            xi, yi, zi,
            color=ACCENT_CYAN, linewidth=0.12, alpha=0.15,
            rstride=6, cstride=6,
        )
        self.ax3d.set_xlabel("Time (s)", fontsize=6, color=TEXT_MUTED, labelpad=2)
        self.ax3d.set_ylabel("Channel",  fontsize=6, color=TEXT_MUTED, labelpad=2)
        self.ax3d.set_zlabel("RSSI (dBm)", fontsize=6, color=TEXT_MUTED, labelpad=2)
        self.ax3d.set_zlim(RSSI_MIN, RSSI_MAX)
        self.ax3d.view_init(elev=28, azim=(-45 + time.time() * 2) % 360)  # slow auto-rotate

    def _draw_2d(self, xi, yi, zi):
        self.ax2d.cla()
        self._style_ax(self.ax2d, "TOP-DOWN  HEATMAP")

        im = self.ax2d.imshow(
            zi, extent=[xi.min(), xi.max(), CHANNEL_RANGE[0], CHANNEL_RANGE[1]],
            origin="lower", cmap=RF_CMAP, aspect="auto",
            vmin=RSSI_MIN, vmax=RSSI_MAX,
        )
        if self.cbar is None:
            self.cbar = self.fig.colorbar(im, ax=self.ax2d, pad=0.02, fraction=0.035)
            self.cbar.ax.tick_params(labelsize=6, colors=TEXT_MUTED)
            self.cbar.set_label("RSSI (dBm)", fontsize=6, color=TEXT_MUTED)
        else:
            self.cbar.update_normal(im)

        self.ax2d.set_xlabel("Time (s)", fontsize=6)
        self.ax2d.set_ylabel("Channel",  fontsize=6)

    def _draw_waveform(self):
        self.ax_wave.cla()
        self._style_ax(self.ax_wave, "RSSI  WAVEFORM")

        if len(self.history) < 2:
            return

        recent = list(self.history)[-120:]
        times  = [r.elapsed for r in recent]
        rssis  = [r.rssi    for r in recent]

        self.ax_wave.plot(times, rssis, color=ACCENT_CYAN, linewidth=0.9, alpha=0.9)
        self.ax_wave.fill_between(times, rssis, RSSI_MIN, alpha=0.15, color=ACCENT_CYAN)
        self.ax_wave.axhline(-70, color=ACCENT_AMBER, linewidth=0.6, linestyle="--", alpha=0.6, label="-70 dBm")
        self.ax_wave.set_ylim(RSSI_MIN, RSSI_MAX)
        self.ax_wave.set_ylabel("dBm", fontsize=6)
        self.ax_wave.legend(fontsize=5, loc="upper right")

    def _draw_channel_bar(self):
        self.ax_bar.cla()
        self._style_ax(self.ax_bar, "CHANNEL  ACTIVITY")

        channels = list(range(CHANNEL_RANGE[0], CHANNEL_RANGE[1] + 1))
        avgs     = [
            np.mean(list(self.channel_stats[ch])) if self.channel_stats[ch] else RSSI_MIN
            for ch in channels
        ]
        norm_avgs = [(v - RSSI_MIN) / (RSSI_MAX - RSSI_MIN) for v in avgs]
        colors    = [RF_CMAP(n) for n in norm_avgs]

        bars = self.ax_bar.barh(channels, avgs, color=colors, edgecolor=DARK_BG, linewidth=0.4)
        self.ax_bar.set_xlim(RSSI_MIN, RSSI_MAX)
        self.ax_bar.set_xlabel("Avg RSSI (dBm)", fontsize=6)
        self.ax_bar.set_yticks(channels)
        self.ax_bar.set_yticklabels([f"ch{c}" for c in channels], fontsize=5)

        # label busiest channel
        if avgs:
            best_ch = channels[np.argmax(avgs)]
            self.ax_bar.axvline(
                max(avgs), color=ACCENT_RED, linewidth=0.8, linestyle=":",
                label=f"peak ch{best_ch}"
            )
            self.ax_bar.legend(fontsize=5)

    def _draw_pie(self):
        self.ax_pie.cla()
        self._style_ax(self.ax_pie, "FRAME  TYPES")
        self.ax_pie.set_facecolor(PANEL_BG)
        self.ax_pie.axis("off")

        if not self.history:
            return

        counts = defaultdict(int)
        for r in self.history:
            counts[r.sig_type] += 1

        labels = list(counts.keys())
        sizes  = list(counts.values())
        colors = [ACCENT_CYAN, ACCENT_GREEN, ACCENT_AMBER, ACCENT_RED][:len(labels)]

        wedges, texts, autotexts = self.ax_pie.pie(
            sizes, labels=None, colors=colors,
            autopct="%1.0f%%", startangle=90,
            pctdistance=0.7,
            wedgeprops={"linewidth": 0.5, "edgecolor": DARK_BG},
        )
        for at in autotexts:
            at.set_fontsize(7)
            at.set_color(DARK_BG)
            at.set_fontfamily("monospace")

        self.ax_pie.legend(
            wedges, labels, fontsize=6,
            loc="lower center", ncol=2,
            facecolor=PANEL_BG, edgecolor=BORDER_COL,
            labelcolor=TEXT_PRIMARY,
        )

    def _draw_mac_table(self):
        self.ax_mac.cla()
        self._style_ax(self.ax_mac, "DEVICE  REGISTRY")
        self.ax_mac.axis("off")

        if not self.history:
            return

        # aggregate per-MAC
        mac_data = defaultdict(lambda: {"count": 0, "rssi": []})
        for r in self.history:
            mac_data[r.mac]["count"] += 1
            mac_data[r.mac]["rssi"].append(r.rssi)

        rows = sorted(mac_data.items(), key=lambda x: -x[1]["count"])[:8]
        y = 0.95
        self.ax_mac.text(0.0, y, f"{'MAC':<20} {'PKT':>4} {'AVG':>6}", fontsize=6,
                         color=ACCENT_CYAN, fontfamily="monospace", transform=self.ax_mac.transAxes)
        y -= 0.08
        self.ax_mac.axhline(y + 0.02, color=BORDER_COL, linewidth=0.5, transform=self.ax_mac.transAxes)

        for mac, data in rows:
            avg_rssi = int(np.mean(data["rssi"]))
            is_white = mac in self.filter.whitelist
            color    = ACCENT_GREEN if is_white else TEXT_PRIMARY
            tag      = "★" if is_white else " "
            self.ax_mac.text(
                0.0, y,
                f"{tag}{mac[:17]:<18} {data['count']:>4} {avg_rssi:>5}",
                fontsize=5.5, color=color,
                fontfamily="monospace", transform=self.ax_mac.transAxes,
            )
            y -= 0.09

        self.ax_mac.text(
            0.0, 0.03,
            f"Blocked: {self.filter.blocked_count}  Unique: {self.filter.unique_devices}",
            fontsize=5.5, color=TEXT_MUTED,
            fontfamily="monospace", transform=self.ax_mac.transAxes,
        )

    def _draw_event_log(self):
        self.ax_log.cla()
        self._style_ax(self.ax_log, "EVENT  LOG")
        self.ax_log.axis("off")

        y = 0.95
        for ts, msg, color in list(self.event_log)[:10]:
            ts_str = ts.strftime("%H:%M:%S")
            self.ax_log.text(
                0.0, y, f"{ts_str}  {msg}",
                fontsize=5.5, color=color,
                fontfamily="monospace", transform=self.ax_log.transAxes,
            )
            y -= 0.09

        if not self.event_log:
            self.ax_log.text(0.1, 0.5, "Waiting for events...",
                             fontsize=7, color=TEXT_MUTED,
                             fontfamily="monospace", transform=self.ax_log.transAxes)

    def _draw_stats(self, elapsed: float):
        self.ax_stats.cla()
        self._style_ax(self.ax_stats, "SYSTEM  STATS")
        self.ax_stats.axis("off")

        total   = len(self.history)
        pps     = total / max(elapsed, 1)
        mode    = "MOCK" if args.mock else "LIVE"
        filter_m = "WHITELIST" if self.filter.whitelist_mode else "PASSIVE"
        ntfy_s  = f"{self.notifier.channel}" if self.notifier.enabled else "OFF"

        lines = [
            ("MODE",      mode,                    ACCENT_AMBER if mode=="MOCK" else ACCENT_GREEN),
            ("FILTER",    filter_m,                ACCENT_CYAN),
            ("PACKETS",   f"{total}",              TEXT_PRIMARY),
            ("PKT/S",     f"{pps:.1f}",            TEXT_PRIMARY),
            ("DEVICES",   f"{self.filter.unique_devices}", ACCENT_CYAN),
            ("BLOCKED",   f"{self.filter.blocked_count}",  ACCENT_RED),
            ("UPTIME",    f"{int(elapsed)}s",      TEXT_MUTED),
            ("NTFY",      ntfy_s,                  ACCENT_GREEN if self.notifier.enabled else TEXT_MUTED),
            ("LOG",       CSV_FILE[-14:],           TEXT_MUTED),
        ]

        y = 0.95
        for label, val, color in lines:
            self.ax_stats.text(0.0, y, f"{label:<9}", fontsize=6,
                               color=TEXT_MUTED, fontfamily="monospace",
                               transform=self.ax_stats.transAxes)
            self.ax_stats.text(0.55, y, val, fontsize=6,
                               color=color, fontfamily="monospace",
                               transform=self.ax_stats.transAxes)
            y -= 0.10

    def _update_status(self, elapsed: float):
        count   = len(self.history)
        mode    = "[SIMULATION]" if args.mock else "[LIVE]"
        ts      = datetime.now().strftime("%H:%M:%S")
        text    = f"● {mode}  {ts}  |  {count} packets  |  {self.filter.unique_devices} devices  |  uptime {int(elapsed)}s"
        self.status_text.set_text(text)
        self.status_text.set_color(ACCENT_AMBER if args.mock else ACCENT_GREEN)

    # ── animation frame ───────────────────────

    def update_frame(self, frame, start_time: float):
        elapsed = time.time() - start_time
        xi, yi, zi = self._compute_grid()

        if xi is not None:
            self._draw_3d(xi, yi, zi)
            self._draw_2d(xi, yi, zi)

        self._draw_waveform()
        self._draw_channel_bar()
        self._draw_pie()
        self._draw_mac_table()
        self._draw_event_log()
        self._draw_stats(elapsed)
        self._update_status(elapsed)

        return []

    def run(self, data_queue: queue.Queue, start_time: float):
        """Drain the data queue and run the animation loop."""

        def drain_queue(_frame):
            while not data_queue.empty():
                try:
                    _, raw = data_queue.get_nowait()
                    rec = parse_line(raw, start_time)
                    if rec:
                        self.ingest(rec)
                except queue.Empty:
                    break
            return self.update_frame(_frame, start_time)

        ani = animation.FuncAnimation(
            self.fig, drain_queue,
            interval=UPDATE_INTERVAL_MS,
            blit=False, cache_frame_data=False,
        )

        try:
            plt.show()
        except KeyboardInterrupt:
            pass
        finally:
            self.logger.close()
            print("\n  ✔ Session ended — data saved to", CSV_FILE)


# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════

def build_args():
    p = argparse.ArgumentParser(
        description="Passive Wi-Fi RF Heatmap — collector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--port",       default=None,  help="Serial port (e.g. COM7, /dev/ttyUSB0)")
    p.add_argument("--mock",       action="store_true", help="Run in simulation mode (no hardware needed)")
    p.add_argument("--filter-mac", action="store_true", help="Enable whitelist-only mode")
    p.add_argument("--whitelist",  nargs="*", default=[], metavar="MAC", help="MACs to whitelist")
    p.add_argument("--blacklist",  nargs="*", default=[], metavar="MAC", help="MACs to blacklist")
    p.add_argument("--ntfy",       default=None,  metavar="CHANNEL", help="Ntfy.sh channel for push alerts")
    return p.parse_args()


def print_banner():
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║        PASSIVE Wi-Fi RF HEATMAP  //  Signal Intelligence         ║
╠══════════════════════════════════════════════════════════════════╣
║  3D surface map · MAC filter · Multi-node · Ntfy alerts          ║
╚══════════════════════════════════════════════════════════════════╝
""")


args = None  # set globally so draw routines can read --mock flag

def main():
    global args
    args = build_args()
    print_banner()

    # ── filter setup
    filter_engine = FilterEngine(
        whitelist      = DEFAULT_WHITELIST + (args.whitelist or []),
        blacklist      = DEFAULT_BLACKLIST + (args.blacklist or []),
        whitelist_mode = args.filter_mac,
    )
    if args.filter_mac:
        print(f"  ► Whitelist mode ON — tracking {len(filter_engine.whitelist)} MAC(s)")
    else:
        print("  ► Passive mode — capturing all visible devices")

    # ── notifier setup
    notifier = NtfyNotifier(
        channel = args.ntfy or "rf-heatmap",
        enabled = bool(args.ntfy),
    )
    if notifier.enabled:
        print(f"  ► Ntfy alerts → {NTFY_BASE_URL}/{args.ntfy}")
    else:
        print("  ► Ntfy disabled (pass --ntfy <channel> to enable)")

    # ── data source
    data_queue  = queue.Queue()
    stop_event  = threading.Event()
    start_time  = time.time()

    if args.mock:
        print("  ► Starting simulation mode...\n")
        t = threading.Thread(target=mock_reader, args=(data_queue, stop_event), daemon=True)
    else:
        port = args.port or auto_detect_port()
        if not port:
            print("  ✘ No serial port found. Use --port COM7 or --mock for simulation.")
            sys.exit(1)
        t = threading.Thread(target=serial_reader, args=(port, data_queue, stop_event), daemon=True)

    t.start()

    # ── launch visualiser
    vis = RFVisualiser(filter_engine, notifier)
    try:
        vis.run(data_queue, start_time)
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
