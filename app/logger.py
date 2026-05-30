"""
logger.py — CSV packet logger

Handles all disk I/O for packet logging.
Completely decoupled from processing and UI.
"""

import csv
import os
from datetime import datetime


class PacketRecord:
    """Shared data structure used across all modules."""
    __slots__ = ("timestamp", "elapsed", "sig_type", "mac", "channel", "rssi", "node_id")

    def __init__(self, timestamp, elapsed, sig_type, mac, channel, rssi, node_id=1):
        self.timestamp = timestamp
        self.elapsed   = elapsed
        self.sig_type  = sig_type
        self.mac       = mac
        self.channel   = channel
        self.rssi      = rssi
        self.node_id   = node_id


class CSVLogger:
    """
    Appends PacketRecords to a CSV file with real timestamps.
    Thread-safe via flush-on-write.
    """

    HEADER = ["Timestamp", "Elapsed", "NodeID", "Type", "MAC", "Channel", "RSSI"]

    def __init__(self, path: str):
        self.path       = path
        self.row_count  = 0
        write_header    = not os.path.exists(path)
        self._file      = open(path, "a", newline="", encoding="utf-8")
        self._writer    = csv.writer(self._file)
        if write_header:
            self._writer.writerow(self.HEADER)
            self._file.flush()

    def log(self, rec: PacketRecord):
        self._writer.writerow([
            rec.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
            f"{rec.elapsed:.3f}",
            rec.node_id,
            rec.sig_type,
            rec.mac,
            rec.channel,
            rec.rssi,
        ])
        self._file.flush()
        self.row_count += 1

    def close(self):
        if self._file and not self._file.closed:
            self._file.close()
            print(f"  ✔ Log closed — {self.row_count} rows written to {self.path}")

    def __del__(self):
        self.close()