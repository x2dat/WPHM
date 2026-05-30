"""
╔══════════════════════════════════════════════════════════════════╗
║           PASSIVE Wi-Fi RF HEATMAP — collector.py                ║
║                     LIVE MODE — ESP32 required                   ║
╚══════════════════════════════════════════════════════════════════╝

USAGE:
  python app/collector.py                        # auto-detect port
  python app/collector.py --port COM7            # specific port
  python app/collector.py --filter-mac           # whitelist mode
  python app/collector.py --ntfy my-channel      # push notifications
  python app/collector.py --port COM7 --ntfy ch  # combined

For simulation without hardware:
  python app/mock_collector.py
"""

import argparse
import queue
import sys
import threading
import time

from config import CSV_FILE_LIVE, DEFAULT_WHITELIST, DEFAULT_BLACKLIST
from logger import CSVLogger
from notifier import NtfyNotifier
from processor import FilterEngine, Processor
from reader import SerialReader, auto_detect_port, list_ports
from visualiser import RFVisualiser


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║        PASSIVE Wi-Fi RF HEATMAP  //  Signal Intelligence         ║
╠══════════════════════════════════════════════════════════════════╣
║  3D surface · MAC filter · Multi-node · Ntfy alerts  [LIVE]      ║
╚══════════════════════════════════════════════════════════════════╝
""")


def build_args():
    p = argparse.ArgumentParser(
        description="RF Heatmap — live ESP32 mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--port",       default=None,  help="Serial port (e.g. COM7, /dev/ttyUSB0)")
    p.add_argument("--filter-mac", action="store_true", help="Whitelist-only mode")
    p.add_argument("--whitelist",  nargs="*", default=[], metavar="MAC")
    p.add_argument("--blacklist",  nargs="*", default=[], metavar="MAC")
    p.add_argument("--ntfy",       default=None,  metavar="CHANNEL")
    return p.parse_args()


def main():
    print_banner()
    args = build_args()

    # port detection
    port = args.port or auto_detect_port()
    if not port:
        print("  No ESP32 found. Available ports:")
        for p in list_ports():
            print(f"      {p}")
        print("  Use --port COM7 (or your port), or run mock_collector.py for simulation.")
        sys.exit(1)
    print(f"  Using port: {port}")

    # setup
    filter_engine = FilterEngine(
        whitelist      = list(DEFAULT_WHITELIST) + (args.whitelist or []),
        blacklist      = list(DEFAULT_BLACKLIST) + (args.blacklist or []),
        whitelist_mode = args.filter_mac,
    )
    notifier   = NtfyNotifier(channel=args.ntfy or "rf-heatmap", enabled=bool(args.ntfy))
    logger     = CSVLogger(CSV_FILE_LIVE)
    start_time = time.time()
    processor  = Processor(filter_engine, logger, notifier, start_time)

    print(f"  Filter: {'WHITELIST' if args.filter_mac else 'PASSIVE'}")
    print(f"  Ntfy:   {notifier.status}")
    print(f"  Log:    {CSV_FILE_LIVE}\n")

    # reader thread
    data_queue = queue.Queue()
    stop_event = threading.Event()
    SerialReader(port, data_queue, stop_event, node_id=1).start()

    # launch UI
    vis = RFVisualiser(processor, mode_label="LIVE")
    try:
        vis.run(data_queue, start_time)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        logger.close()


if __name__ == "__main__":
    main()