"""
╔══════════════════════════════════════════════════════════════════╗
║                    config.py — all constants                     ║
║         Edit this file to configure your entire project          ║
╚══════════════════════════════════════════════════════════════════╝
"""

# ══════════════════════════════════════════════
#  SERIAL / HARDWARE
# ══════════════════════════════════════════════

BAUD_RATE           = 115200          # must match firmware Serial.begin()
SERIAL_TIMEOUT      = 1               # seconds before serial read gives up
SERIAL_RETRY_SEC    = 5               # seconds between auto-reconnect attempts

# ══════════════════════════════════════════════
#  SIGNAL PARAMETERS
# ══════════════════════════════════════════════

CHANNEL_RANGE       = (1, 11)         # 2.4GHz WiFi channels
RSSI_MIN            = -95             # weakest signal (dBm)
RSSI_MAX            = -30             # strongest signal (dBm)
RSSI_STRONG_THRESH  = -45             # above this triggers a strong signal alert

# ══════════════════════════════════════════════
#  DATA PROCESSING
# ══════════════════════════════════════════════

MAX_HISTORY         = 500             # rolling packet window kept in memory
GRID_RESOLUTION     = 60             # heatmap grid cells per axis
SMOOTHING_SIGMA     = 2.0            # gaussian blur strength (higher = smoother)
WAVEFORM_SAMPLES    = 120            # how many RSSI samples shown in waveform

# ══════════════════════════════════════════════
#  UI / VISUALISER
# ══════════════════════════════════════════════

UPDATE_INTERVAL_MS  = 300            # matplotlib animation refresh rate
FIGURE_SIZE         = (18, 10)       # window size in inches
ROTATION_SPEED      = 2              # degrees per second for 3D auto-rotation
ROTATION_ELEVATION  = 28             # 3D view elevation angle

# ══════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════

CSV_FILE_LIVE       = "wifi_wave_log.csv"
CSV_FILE_MOCK       = "wifi_wave_log_mock.csv"

# ══════════════════════════════════════════════
#  NOTIFICATIONS (ntfy.sh)
# ══════════════════════════════════════════════

NTFY_BASE_URL       = "https://ntfy.sh"
NTFY_COOLDOWN_SEC   = 10             # min seconds between notifications

# ══════════════════════════════════════════════
#  MAC FILTER DEFAULTS
# ══════════════════════════════════════════════

# Add your real device MACs here to track them (shown with ★ in UI)
DEFAULT_WHITELIST = [
    # "AA:BB:CC:DD:EE:FF",
    # "11:22:33:44:55:66",
]

# Add MACs to permanently ignore (noisy neighbours, broadcast etc)
DEFAULT_BLACKLIST = [
    # "FF:FF:FF:FF:FF:FF",
]

# ══════════════════════════════════════════════
#  COLOUR PALETTE  (dark industrial theme)
# ══════════════════════════════════════════════

DARK_BG       = "#0A0C0F"
PANEL_BG      = "#0F1318"
BORDER_COL    = "#1E2530"
ACCENT_CYAN   = "#00D4FF"
ACCENT_GREEN  = "#00FF88"
ACCENT_RED    = "#FF3355"
ACCENT_AMBER  = "#FFB800"
TEXT_PRIMARY  = "#E8ECF0"
TEXT_MUTED    = "#4A5568"

# Custom RF heatmap colormap stops
RF_CMAP_STOPS = [
    "#000000", "#001830", "#003366",
    "#0066CC", "#00AAFF", "#00FFCC",
    "#00FF66", "#AAFF00", "#FFCC00",
    "#FF6600", "#FF0000"
]