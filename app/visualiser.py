"""
visualiser.py — matplotlib rendering engine

Draws all 7 panels of the RF heatmap UI.
Completely decoupled from data logic — receives a Processor instance
and reads its state on every animation frame.

Panels:
  1. 3D signal surface (auto-rotating)
  2. 2D top-down heatmap
  3. RSSI waveform
  4. Per-channel activity bars
  5. Frame type pie chart
  6. Device registry / MAC table
  7. Event log
  8. System stats
"""

import time
from collections import defaultdict
from datetime import datetime

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.animation as animation
from matplotlib.colors import LinearSegmentedColormap
from mpl_toolkits.mplot3d import Axes3D

from config import (
    DARK_BG, PANEL_BG, BORDER_COL,
    ACCENT_CYAN, ACCENT_GREEN, ACCENT_RED, ACCENT_AMBER,
    TEXT_PRIMARY, TEXT_MUTED,
    RSSI_MIN, RSSI_MAX, CHANNEL_RANGE,
    UPDATE_INTERVAL_MS, FIGURE_SIZE,
    ROTATION_SPEED, ROTATION_ELEVATION,
    RF_CMAP_STOPS,
)

# Build custom RF colormap from config stops
RF_CMAP = LinearSegmentedColormap.from_list("rf_heat", RF_CMAP_STOPS, N=256)

# Map event log color keys to hex values
EVENT_COLORS = {
    "amber": ACCENT_AMBER,
    "red":   ACCENT_RED,
    "cyan":  ACCENT_CYAN,
    "green": ACCENT_GREEN,
}


class RFVisualiser:
    """
    Full 7-panel matplotlib visualiser.
    Call run() to start the animation loop.
    """

    def __init__(self, processor, mode_label: str = "LIVE"):
        self.processor   = processor
        self.mode_label  = mode_label
        self.cbar        = None

        self._apply_theme()
        self._build_layout()

    # ── theme ─────────────────────────────────

    def _apply_theme(self):
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

    # ── layout ────────────────────────────────

    def _build_layout(self):
        self.fig = plt.figure(figsize=FIGURE_SIZE, facecolor=DARK_BG)
        title_color = ACCENT_AMBER if self.mode_label == "SIMULATION" else ACCENT_CYAN
        self.fig.canvas.manager.set_window_title(
            f"RF HEATMAP  //  {self.mode_label}  //  Passive Wi-Fi Sniffer"
        )

        gs = gridspec.GridSpec(
            3, 4, figure=self.fig,
            left=0.04, right=0.97,
            top=0.93,  bottom=0.06,
            hspace=0.45, wspace=0.35,
        )

        self.fig.text(
            0.5, 0.965,
            f"◈  PASSIVE RF HEATMAP  //  Wi-Fi Signal Intelligence  //  [{self.mode_label}]",
            ha="center", va="center",
            fontsize=13, fontweight="bold",
            color=title_color, fontfamily="monospace",
        )
        self.status_text = self.fig.text(
            0.5, 0.945, "● INITIALISING...",
            ha="center", va="center",
            fontsize=8, color=ACCENT_AMBER, fontfamily="monospace",
        )

        # 3D surface — large, spans rows 0-1 and cols 0-1
        self.ax3d    = self.fig.add_subplot(gs[0:2, 0:2], projection="3d")
        self._style_3d(self.ax3d, "3D  SIGNAL SURFACE")

        # 2D top-down heatmap
        self.ax2d    = self.fig.add_subplot(gs[0:2, 2])
        self._style_ax(self.ax2d, "TOP-DOWN  HEATMAP")

        # RSSI waveform
        self.ax_wave = self.fig.add_subplot(gs[0, 3])
        self._style_ax(self.ax_wave, "RSSI  WAVEFORM")

        # Channel activity bars
        self.ax_bar  = self.fig.add_subplot(gs[1, 3])
        self._style_ax(self.ax_bar, "CHANNEL  ACTIVITY")

        # Frame type pie
        self.ax_pie  = self.fig.add_subplot(gs[2, 0])
        self._style_ax(self.ax_pie, "FRAME  TYPES")

        # MAC device table
        self.ax_mac  = self.fig.add_subplot(gs[2, 1])
        self._style_ax(self.ax_mac, "DEVICE  REGISTRY")
        self.ax_mac.axis("off")

        # Event log
        self.ax_log  = self.fig.add_subplot(gs[2, 2])
        self._style_ax(self.ax_log, "EVENT  LOG")
        self.ax_log.axis("off")

        # Stats panel
        self.ax_stats = self.fig.add_subplot(gs[2, 3])
        self._style_ax(self.ax_stats, "SYSTEM  STATS")
        self.ax_stats.axis("off")

    def _style_ax(self, ax, title: str):
        ax.set_facecolor(PANEL_BG)
        for spine in ax.spines.values():
            spine.set_edgecolor(BORDER_COL)
            spine.set_linewidth(0.8)
        ax.set_title(title, fontsize=7, color=ACCENT_CYAN,
                     fontfamily="monospace", pad=5, loc="left")
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3)

    def _style_3d(self, ax, title: str):
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
        self.ax3d.plot_wireframe(
            xi, yi, zi,
            color=ACCENT_CYAN, linewidth=0.12, alpha=0.15,
            rstride=6, cstride=6,
        )
        self.ax3d.set_xlabel("Time (s)",   fontsize=6, color=TEXT_MUTED, labelpad=2)
        self.ax3d.set_ylabel("Channel",    fontsize=6, color=TEXT_MUTED, labelpad=2)
        self.ax3d.set_zlabel("RSSI (dBm)", fontsize=6, color=TEXT_MUTED, labelpad=2)
        self.ax3d.set_zlim(RSSI_MIN, RSSI_MAX)
        self.ax3d.view_init(
            elev=ROTATION_ELEVATION,
            azim=(-45 + time.time() * ROTATION_SPEED) % 360
        )

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
        history = self.processor.history
        if len(history) < 2:
            return
        recent = list(history)[-120:]
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
        stats    = self.processor.channel_stats
        avgs = [
            np.mean(list(stats[ch])) if stats[ch] else RSSI_MIN
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
            self.ax_bar.axvline(max(avgs), color=ACCENT_RED, linewidth=0.8,
                                linestyle=":", label=f"peak ch{best_ch}")
            self.ax_bar.legend(fontsize=5)

    # ── draw: pie chart ───────────────────────

    def _draw_pie(self):
        self.ax_pie.cla()
        self._style_ax(self.ax_pie, "FRAME  TYPES")
        self.ax_pie.set_facecolor(PANEL_BG)
        self.ax_pie.axis("off")
        if not self.processor.history:
            return
        counts = defaultdict(int)
        for r in self.processor.history:
            counts[r.sig_type] += 1
        labels = list(counts.keys())
        sizes  = list(counts.values())
        colors = [ACCENT_CYAN, ACCENT_GREEN, ACCENT_AMBER][:len(labels)]
        wedges, _, autotexts = self.ax_pie.pie(
            sizes, colors=colors,
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
        if not self.processor.history:
            return
        mac_data = defaultdict(lambda: {"count": 0, "rssi": []})
        for r in self.processor.history:
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
            is_white = mac in self.processor.filter.whitelist
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
            f"Blocked: {self.processor.filter.blocked_count}"
            f"  Unique: {self.processor.filter.unique_devices}",
            fontsize=5.5, color=TEXT_MUTED,
            fontfamily="monospace", transform=self.ax_mac.transAxes,
        )

    # ── draw: event log ───────────────────────

    def _draw_event_log(self):
        self.ax_log.cla()
        self._style_ax(self.ax_log, "EVENT  LOG")
        self.ax_log.axis("off")
        y = 0.95
        for ts, msg, color_key in list(self.processor.event_log)[:10]:
            color = EVENT_COLORS.get(color_key, TEXT_PRIMARY)
            self.ax_log.text(
                0.0, y, f"{ts.strftime('%H:%M:%S')}  {msg}",
                fontsize=5.5, color=color,
                fontfamily="monospace", transform=self.ax_log.transAxes,
            )
            y -= 0.09
        if not self.processor.event_log:
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
        proc     = self.processor
        total    = proc.total_packets
        pps      = total / max(elapsed, 1)
        filter_m = "WHITELIST" if proc.filter.whitelist_mode else "PASSIVE"
        ntfy_s   = proc.notifier.status
        lines = [
            ("MODE",    self.mode_label,           ACCENT_AMBER if self.mode_label == "SIMULATION" else ACCENT_GREEN),
            ("FILTER",  filter_m,                  ACCENT_CYAN),
            ("PACKETS", f"{total}",                TEXT_PRIMARY),
            ("PKT/S",   f"{pps:.1f}",              TEXT_PRIMARY),
            ("DEVICES", f"{proc.filter.unique_devices}", ACCENT_CYAN),
            ("BLOCKED", f"{proc.filter.blocked_count}",  ACCENT_RED),
            ("UPTIME",  f"{int(elapsed)}s",        TEXT_MUTED),
            ("NTFY",    ntfy_s,                    ACCENT_GREEN if "sent" in ntfy_s else TEXT_MUTED),
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
        proc  = self.processor
        ts    = datetime.now().strftime("%H:%M:%S")
        text  = (
            f"● [{self.mode_label}]  {ts}  |  "
            f"{proc.total_packets} packets  |  "
            f"{proc.filter.unique_devices} devices  |  "
            f"uptime {int(elapsed)}s"
        )
        self.status_text.set_text(text)
        self.status_text.set_color(
            ACCENT_AMBER if self.mode_label == "SIMULATION" else ACCENT_GREEN
        )

    # ── animation frame ───────────────────────

    def _frame(self, frame: int, data_queue, start_time: float):
        """Called on every animation tick."""
        elapsed = time.time() - start_time

        # Drain reader queue into processor
        self.processor.drain(data_queue)

        # Compute grid
        xi, yi, zi = self.processor.get_grid()
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

    # ── run ───────────────────────────────────

    def run(self, data_queue, start_time: float):
        """Start the matplotlib animation loop. Blocks until window is closed."""
        ani = animation.FuncAnimation(
            self.fig,
            lambda f: self._frame(f, data_queue, start_time),
            interval=UPDATE_INTERVAL_MS,
            blit=False,
            cache_frame_data=False,
        )
        try:
            plt.show()
        except KeyboardInterrupt:
            pass