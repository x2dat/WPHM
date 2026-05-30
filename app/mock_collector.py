"""
╔══════════════════════════════════════════════════════════════════╗
║        PASSIVE Wi-Fi RF HEATMAP — mock_collector.py              ║
║   Exact replica of collector.py — NO ESP32 / hardware needed     ║
╚══════════════════════════════════════════════════════════════════╝

USAGE:
  python app/mock_collector.py               # run simulation
  python app/mock_collector.py --fast        # faster packet rate
  python app/mock_collector.py --whitelist   # whitelist filter mode

This is a fully standalone simulation. No serial port, no ESP32,
no extra config needed. Just install the requirements and run.

  pip install -r requirements.txt
  python app/mock_collector.py
"""

import time
import random
import math
import threading
import queue
import csv
import os
import argparse
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


# ══════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════

CSV_FILE           = "wifi_wave_log_mock.csv"
MAX_HISTORY        = 500
GRID_RESOLUTION    = 60
SMOOTHING_SIGMA    = 2.0
UPDATE_INTERVAL_MS = 300
CHANNEL_RANGE      = (1, 11)
RSSI_MIN           = -95
RSSI_MAX           = -30

# Two of the fake MACs are pre-whitelisted so you can see the ★ tracking feature
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

DEFAULT_WHITELIST = {"AA:BB:CC:11:22:33", "DD:EE:FF:44:55:66"}
DEFAULT_BLACKLIST = set()


# ══════════════════════════════════════════════
#  COLOUR PALETTE  (dark industrial — matches collector.py exactly)
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
    def __init__(self, whitelist=None, blacklist=None, whitelist_mode=False):
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

    @property
    def unique_devices(self):
        return len(self.seen_macs)


class CSVLogger:
    def __init__(self, path: str):
        self.path = path
        write_header = not os.path.exists(path)
        self.file   = open(path, "a", newline="", encoding="utf-8")
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
        self.file.close()


# ══════════════════════════════════════════════
#  MOCK DATA GENERATOR
# ══════════════════════════════════════════════

def mock_reader(data_queue: queue.Queue, stop_event: threading.Event, fast: bool = False):
    """
    Generates realistic simulated Wi-Fi packets.
    Mimics real-world signal behaviour:
      - Router beacons are steady and strong
      - Device probes wander channels and vary in strength
      - Data frames spike and decay
      - Multiple overlapping sine waves simulate walking/movement
    """
    t = 0.0
    sleep_interval = 0.04 if fast else 0.08

    # Give each fake MAC a "home channel" and signal personality
    mac_profiles = {}
    for i, mac in enumerate(FAKE_MACS):
        mac_profiles[mac] = {
            "home_ch":    (i % 11) + 1,
            "base_rssi":  -50 - (i * 3),
            "drift":      random.uniform(0.2, 1.2),
            "phase":      random.uniform(0, math.pi * 2),
            "type_bias":  random.choices(
                ["ROUTER_BEACON", "DEVICE_PROBE", "DATA_FRAME"],
                weights=[0.6, 0.25, 0.15] if i < 3 else [0.1, 0.5, 0.4]
            )[0],
        }

    while not stop_event.is_set():
        time.sleep(sleep_interval)
        t += sleep_interval

        # Pick a MAC weighted by activity
        mac = random.choices(FAKE_MACS, weights=[3,2,2,2,1,1,1,1,1,1,1,1][:len(FAKE_MACS)])[0]
        p   = mac_profiles[mac]

        sig_type = random.choices(
            ["ROUTER_BEACON", "DEVICE_PROBE", "DATA_FRAME"],
            weights=[0.5, 0.3, 0.2]
        )[0]

        # Channel: mostly home channel, occasionally wanders
        if random.random() < 0.15:
            channel = random.randint(CHANNEL_RANGE[0], CHANNEL_RANGE[1])
        else:
            channel = p["home_ch"]

        # RSSI: multi-wave signal simulates realistic environment
        base  = p["base_rssi"]
        wave1 = math.sin(t * p["drift"] + p["phase"]) * 18
        wave2 = math.sin(t * 0.3 + p["phase"] * 0.5) * 8
        noise = random.gauss(0, 4)
        rssi  = int(max(RSSI_MIN, min(RSSI_MAX, base + wave1 + wave2 + noise)))

        data_queue.put(("mock", f"{sig_type},{mac},{channel},{rssi}"))


def parse_line(raw: str, start_time: float, node_id: int = 1):
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
        return PacketRecord(datetime.now(), time.time() - start_time, sig_type, mac, channel, rssi, node_id)
    except (ValueError, IndexError):
        return None


# ══════════════════════════════════════════════
#  VISUALISER  (exact match to collector.py)
# ══════════════════════════════════════════════

class RFVisualiser:

    def __init__(self, filter_engine: FilterEngine):
        self.filter      = filter_engine
        self.history     = deque(maxlen=MAX_HISTORY)
        self.logger      = CSVLogger(CSV_FILE)
        self.channel_stats = defaultdict(lambda: deque(maxlen=60))
        self.event_log   = deque(maxlen=12)
        self.known_macs  = set()

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
        self.cbar = None

    # ── layout (identical to collector.py) ────

    def _build_layout(self):
        self.fig = plt.figure(figsize=(18, 10), facecolor=DARK_BG)
        self.fig.canvas.manager.set_window_title("RF HEATMAP  //  SIMULATION MODE  //  No ESP32 required")

        gs = gridspec.GridSpec(
            3, 4,
            figure=self.fig,
            left=0.04, right=0.97,
            top=0.93,  bottom=0.06,
            hspace=0.45, wspace=0.35,
        )

        self.fig.text(
            0.5, 0.965,
            "◈  PASSIVE RF HEATMAP  //  Wi-Fi Signal Intelligence  //  [SIMULATION MODE]",
            ha="center", va="center",
            fontsize=13, fontweight="bold",
            color=ACCENT_AMBER, fontfamily="monospace",
        )
        self.status_text = self.fig.text(
            0.5, 0.945, "● INITIALISING...",
            ha="center", va="center",
            fontsize=8, color=ACCENT_AMBER, fontfamily="monospace",
        )

        # 3D surface — large, spans 2 rows and 2 cols
        self.ax3d = self.fig.add_subplot(gs[0:2, 0:2], projection="3d")
        self._style_3d(self.ax3d, "3D  SIGNAL SURFACE")

        # 2D top-down heatmap
        self.ax2d = self.fig.add_subplot(gs[0:2, 2])
        self._style_ax(self.ax2d, "TOP-DOWN  HEATMAP")

        # RSSI waveform
        self.ax_wave = self.fig.add_subplot(gs[0, 3])
        self._style_ax(self.ax_wave, "RSSI  WAVEFORM")

        # Per-channel bar chart
        self.ax_bar = self.fig.add_subplot(gs[1, 3])
        self._style_ax(self.ax_bar, "CHANNEL  ACTIVITY")

        # Frame type pie
        self.ax_pie = self.fig.add_subplot(gs[2, 0])
        self._style_ax(self.ax_pie, "FRAME  TYPES")

        # MAC device table
        self.ax_mac = self.fig.add_subplot(gs[2, 1])
        self._style_ax(self.ax_mac, "DEVICE  REGISTRY")
        self.ax_mac.axis("off")

        # Event log
        self.ax_log = self.fig.add_subplot(gs[2, 2])
        self._style_ax(self.ax_log, "EVENT  LOG")
        self.ax_log.axis("off")

        # Stats panel
        self.ax_stats = self.fig.add_subplot(gs[2, 3])
        self._style_ax(self.ax_stats, "SYSTEM  STATS")
        self.ax_stats.axis("off")

    def _style_ax(self, ax, title):
        ax.set_facecolor(PANEL_BG)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER_COL)
            spine.set_linewidth(0.8)
        ax.set_title(title, fontsize=7, color=ACCENT_CYAN,
                     fontfamily="monospace", pad=5, loc="left")
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
        ax.set_title(title, fontsize=7, color=ACCENT_CYAN,
                     fontfamily="monospace", pad=4, loc="left")
        ax.tick_params(labelsize=6, colors=TEXT_MUTED)

    # ── ingest ────────────────────────────────

    def ingest(self, rec: PacketRecord):
        if not self.filter.allow(rec.mac):
            return
        self.history.append(rec)
        self.channel_stats[rec.channel].append(rec.rssi)
        self.logger.log(rec)

        if rec.mac not in self.known_macs:
            self.known_macs.add(rec.mac)
            self.event_log.appendleft(
                (datetime.now(), f"NEW  {rec.mac[:17]}", ACCENT_AMBER)
            )

        if rec.rssi >= -45:
            self.event_log.appendleft(
                (datetime.now(), f"STRONG {rec.rssi}dBm  ch{rec.channel}", ACCENT_RED)
            )

    # ── grid computation ──────────────────────

    def _compute_grid(self):
        if len(self.history) < 4:
            return None, None, None
        df = pd.DataFrame([
            (r.elapsed, r.channel, r.rssi) for r in self.history
        ], columns=["t", "ch", "rssi"])

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

    # ── draw: 3D surface ──────────────────────

    def _draw_3d(self, xi, yi, zi):
        self.ax3d.cla()
        self._style_3d(self.ax3d, "3D  SIGNAL SURFACE")

        norm   = (zi - RSSI_MIN) / (RSSI_MAX - RSSI_MIN)
        colors = RF_CMAP(norm)

        self.ax3d.plot_surface(
            xi, yi, zi,
            facecolors=colors,
            linewidth=0, antialiased=True,
            alpha=0.92, shade=True,
            lightsource=matplotlib.colors.LightSource(azdeg=45, altdeg=35),
        )
        # Cyan wireframe overlay — the "radar" feel
        self.ax3d.plot_wireframe(
            xi, yi, zi,
            color=ACCENT_CYAN, linewidth=0.12, alpha=0.15,
            rstride=6, cstride=6,
        )
        self.ax3d.set_xlabel("Time (s)",   fontsize=6, color=TEXT_MUTED, labelpad=2)
        self.ax3d.set_ylabel("Channel",    fontsize=6, color=TEXT_MUTED, labelpad=2)
        self.ax3d.set_zlabel("RSSI (dBm)", fontsize=6, color=TEXT_MUTED, labelpad=2)
        self.ax3d.set_zlim(RSSI_MIN, RSSI_MAX)

        # Slow auto-rotation: full 360° every 3 minutes
        self.ax3d.view_init(elev=28, azim=(-45 + time.time() * 2) % 360)

    # ── draw: 2D heatmap ──────────────────────

    def _draw_2d(self, xi, yi, zi):
        self.ax2d.cla()
        self._style_ax(self.ax2d, "TOP-DOWN  HEATMAP")

        im = self.ax2d.imshow(
            zi,
            extent=[xi.min(), xi.max(), CHANNEL_RANGE[0], CHANNEL_RANGE[1]],
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

    # ── draw: RSSI waveform ───────────────────

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
        self.ax_wave.axhline(-70, color=ACCENT_AMBER, linewidth=0.6,
                             linestyle="--", alpha=0.6, label="-70 dBm")
        self.ax_wave.set_ylim(RSSI_MIN, RSSI_MAX)
        self.ax_wave.set_ylabel("dBm", fontsize=6)
        self.ax_wave.legend(fontsize=5, loc="upper right")

    # ── draw: channel bars ────────────────────

    def _draw_channel_bar(self):
        self.ax_bar.cla()
        self._style_ax(self.ax_bar, "CHANNEL  ACTIVITY")

        channels = list(range(CHANNEL_RANGE[0], CHANNEL_RANGE[1] + 1))
        avgs = [
            np.mean(list(self.channel_stats[ch])) if self.channel_stats[ch] else RSSI_MIN
            for ch in channels
        ]
        norm_avgs = [(v - RSSI_MIN) / (RSSI_MAX - RSSI_MIN) for v in avgs]
        colors    = [RF_CMAP(n) for n in norm_avgs]

        self.ax_bar.barh(channels, avgs, color=colors, edgecolor=DARK_BG, linewidth=0.4)
        self.ax_bar.set_xlim(RSSI_MIN, RSSI_MAX)
        self.ax_bar.set_xlabel("Avg RSSI (dBm)", fontsize=6)
        self.ax_bar.set_yticks(channels)
        self.ax_bar.set_yticklabels([f"ch{c}" for c in channels], fontsize=5)

        if avgs:
            best_ch = channels[int(np.argmax(avgs))]
            self.ax_bar.axvline(
                max(avgs), color=ACCENT_RED, linewidth=0.8, linestyle=":",
                label=f"peak ch{best_ch}"
            )
            self.ax_bar.legend(fontsize=5)

    # ── draw: pie chart ───────────────────────

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

    # ── draw: MAC table ───────────────────────

    def _draw_mac_table(self):
        self.ax_mac.cla()
        self._style_ax(self.ax_mac, "DEVICE  REGISTRY")
        self.ax_mac.axis("off")
        if not self.history:
            return

        mac_data = defaultdict(lambda: {"count": 0, "rssi": []})
        for r in self.history:
            mac_data[r.mac]["count"] += 1
            mac_data[r.mac]["rssi"].append(r.rssi)

        rows = sorted(mac_data.items(), key=lambda x: -x[1]["count"])[:8]
        y = 0.95
        self.ax_mac.text(
            0.0, y, f"{'MAC':<20} {'PKT':>4} {'AVG':>6}",
            fontsize=6, color=ACCENT_CYAN,
            fontfamily="monospace", transform=self.ax_mac.transAxes
        )
        y -= 0.08
        self.ax_mac.axhline(y + 0.02, color=BORDER_COL, linewidth=0.5,
                            transform=self.ax_mac.transAxes)

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

    # ── draw: event log ───────────────────────

    def _draw_event_log(self):
        self.ax_log.cla()
        self._style_ax(self.ax_log, "EVENT  LOG")
        self.ax_log.axis("off")

        y = 0.95
        for ts, msg, color in list(self.event_log)[:10]:
            self.ax_log.text(
                0.0, y, f"{ts.strftime('%H:%M:%S')}  {msg}",
                fontsize=5.5, color=color,
                fontfamily="monospace", transform=self.ax_log.transAxes,
            )
            y -= 0.09

        if not self.event_log:
            self.ax_log.text(
                0.1, 0.5, "Waiting for events...",
                fontsize=7, color=TEXT_MUTED,
                fontfamily="monospace", transform=self.ax_log.transAxes
            )

    # ── draw: stats panel ─────────────────────

    def _draw_stats(self, elapsed: float):
        self.ax_stats.cla()
        self._style_ax(self.ax_stats, "SYSTEM  STATS")
        self.ax_stats.axis("off")

        total    = len(self.history)
        pps      = total / max(elapsed, 1)
        filter_m = "WHITELIST" if self.filter.whitelist_mode else "PASSIVE"

        lines = [
            ("MODE",    "SIMULATION",          ACCENT_AMBER),
            ("SOURCE",  "MOCK GENERATOR",      ACCENT_AMBER),
            ("FILTER",  filter_m,              ACCENT_CYAN),
            ("PACKETS", f"{total}",            TEXT_PRIMARY),
            ("PKT/S",   f"{pps:.1f}",          TEXT_PRIMARY),
            ("DEVICES", f"{self.filter.unique_devices}", ACCENT_CYAN),
            ("BLOCKED", f"{self.filter.blocked_count}",  ACCENT_RED),
            ("UPTIME",  f"{int(elapsed)}s",    TEXT_MUTED),
            ("LOG",     CSV_FILE[-16:],         TEXT_MUTED),
        ]

        y = 0.95
        for label, val, color in lines:
            self.ax_stats.text(
                0.0, y, f"{label:<9}",
                fontsize=6, color=TEXT_MUTED,
                fontfamily="monospace", transform=self.ax_stats.transAxes
            )
            self.ax_stats.text(
                0.55, y, val,
                fontsize=6, color=color,
                fontfamily="monospace", transform=self.ax_stats.transAxes
            )
            y -= 0.10

    # ── status bar ────────────────────────────

    def _update_status(self, elapsed: float):
        count = len(self.history)
        ts    = datetime.now().strftime("%H:%M:%S")
        text  = (
            f"● [SIMULATION]  {ts}  |  {count} packets  |  "
            f"{self.filter.unique_devices} devices  |  "
            f"uptime {int(elapsed)}s  |  No ESP32 required"
        )
        self.status_text.set_text(text)
        self.status_text.set_color(ACCENT_AMBER)

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

    # ── main loop ─────────────────────────────

    def run(self, data_queue: queue.Queue, start_time: float):
        def drain_and_draw(frame):
            while not data_queue.empty():
                try:
                    _, raw = data_queue.get_nowait()
                    rec = parse_line(raw, start_time)
                    if rec:
                        self.ingest(rec)
                except queue.Empty:
                    break
            return self.update_frame(frame, start_time)

        ani = animation.FuncAnimation(
            self.fig, drain_and_draw,
            interval=UPDATE_INTERVAL_MS,
            blit=False, cache_frame_data=False,
        )

        try:
            plt.show()
        except KeyboardInterrupt:
            pass
        finally:
            self.logger.close()
            print(f"\n  ✔ Session ended — {len(self.history)} packets logged to {CSV_FILE}")


# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════

def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║        PASSIVE Wi-Fi RF HEATMAP  //  SIMULATION MODE             ║
╠══════════════════════════════════════════════════════════════════╣
║  No ESP32 needed · Full 3D map · MAC filter · Event log          ║
╚══════════════════════════════════════════════════════════════════╝
""")


def main():
    parser = argparse.ArgumentParser(description="RF Heatmap — simulation mode")
    parser.add_argument("--fast",      action="store_true", help="Higher packet rate")
    parser.add_argument("--whitelist", action="store_true", help="Whitelist filter mode (only tracked MACs)")
    args = parser.parse_args()

    print_banner()

    filter_engine = FilterEngine(
        whitelist      = DEFAULT_WHITELIST,
        blacklist      = DEFAULT_BLACKLIST,
        whitelist_mode = args.whitelist,
    )

    if args.whitelist:
        print(f"  ► Whitelist mode — tracking {len(filter_engine.whitelist)} device(s)")
    else:
        print("  ► Passive mode — all simulated devices visible")

    print(f"  ► Packet rate: {'fast' if args.fast else 'normal'}")
    print(f"  ► Logging to: {CSV_FILE}")
    print(f"  ► Starting simulation...\n")

    data_queue = queue.Queue()
    stop_event = threading.Event()
    start_time = time.time()

    reader_thread = threading.Thread(
        target=mock_reader,
        args=(data_queue, stop_event, args.fast),
        daemon=True,
    )
    reader_thread.start()

    vis = RFVisualiser(filter_engine)
    try:
        vis.run(data_queue, start_time)
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
