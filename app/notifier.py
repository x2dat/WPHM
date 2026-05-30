"""
notifier.py — Ntfy push notification manager

Sends alerts to your phone via ntfy.sh.
Never crashes the main loop — all errors are silently swallowed.

Usage:
    notifier = NtfyNotifier(channel="my-rf-alerts", enabled=True)
    notifier.alert_new_device("AA:BB:CC:DD:EE:FF", channel=6)
    notifier.alert_strong_signal("AA:BB:CC:DD:EE:FF", rssi=-42, channel=6)
    notifier.alert_whitelist_seen("AA:BB:CC:DD:EE:FF", rssi=-55)
    notifier.alert_suspicious_hour("AA:BB:CC:DD:EE:FF", hour=2)
"""

import time

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

from config import NTFY_BASE_URL, NTFY_COOLDOWN_SEC


class NtfyNotifier:
    """
    Push notification manager via ntfy.sh.
    Install the free Ntfy app on your phone, subscribe to your channel,
    and all alerts will appear as push notifications instantly.
    """

    def __init__(self, channel: str, enabled: bool = True):
        self.channel     = channel
        self.enabled     = enabled and _REQUESTS_AVAILABLE
        self.sent_count  = 0
        self._last_sent: dict = {}   # per-type cooldown tracking

        if enabled and not _REQUESTS_AVAILABLE:
            print("  ⚠ Ntfy disabled — install requests: pip install requests")

    def _send(self, title: str, message: str, priority: str = "default",
              tags: str = "", cooldown_key: str = "default"):
        if not self.enabled:
            return

        now = time.time()
        last = self._last_sent.get(cooldown_key, 0)
        if now - last < NTFY_COOLDOWN_SEC:
            return

        self._last_sent[cooldown_key] = now
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
            pass  # never crash the main pipeline on a notification failure

    def alert_new_device(self, mac: str, channel: int):
        self._send(
            title        = "📡  New Device Detected",
            message      = f"Unknown device {mac} appeared on ch{channel}",
            priority     = "default",
            tags         = "mag,wifi",
            cooldown_key = f"new_{mac}",
        )

    def alert_strong_signal(self, mac: str, rssi: int, channel: int):
        self._send(
            title        = "⚠️  Strong Signal Detected",
            message      = f"Device {mac} on ch{channel} at {rssi} dBm — possible nearby device",
            priority     = "high",
            tags         = "warning,wifi",
            cooldown_key = f"strong_{mac}",
        )

    def alert_whitelist_seen(self, mac: str, rssi: int):
        self._send(
            title        = "✅  Tracked Device Online",
            message      = f"Whitelisted device {mac} detected at {rssi} dBm",
            priority     = "low",
            tags         = "white_check_mark,wifi",
            cooldown_key = f"white_{mac}",
        )

    def alert_suspicious_hour(self, mac: str, hour: int):
        self._send(
            title        = "🚨  Suspicious Activity",
            message      = f"Device {mac} detected at {hour:02d}:00 — outside normal hours",
            priority     = "urgent",
            tags         = "rotating_light,wifi",
            cooldown_key = f"hour_{mac}",
        )

    def alert_node_offline(self, node_id: int):
        self._send(
            title        = "⛔  Node Offline",
            message      = f"ESP32 node {node_id} stopped sending data",
            priority     = "high",
            tags         = "x,wifi",
            cooldown_key = f"node_{node_id}",
        )

    @property
    def status(self) -> str:
        if not self.enabled:
            return "OFF"
        return f"{self.channel} ({self.sent_count} sent)"