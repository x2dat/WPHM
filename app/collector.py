import serial
import time
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import griddata

# --- CONFIGURATION ---
SERIAL_PORT = 'COM3'  # Update this port to match your OS connection
BAUD_RATE = 115200
CSV_FILE = "wifi_wave_log.csv"
RUN_TIME_SECONDS = 60  

captured_data = []

print(f"Connecting to ESP32-S3 on {SERIAL_PORT}...")
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    time.sleep(2) 
    ser.flushInput()
    print("Connection established. Data logging active...")
except Exception as e:
    print(f"Error opening serial port: {e}")
    exit()

start_time = time.time()

try:
    while time.time() - start_time < RUN_TIME_SECONDS:
        if ser.in_waiting > 0:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            parts = line.split(',')
            if len(parts) == 4 and parts[0] != "TYPE":
                sig_type, mac, channel, rssi = parts[0], parts[1], int(parts[2]), int(parts[3])
                elapsed = time.time() - start_time
                captured_data.append([elapsed, channel, rssi, sig_type, mac])
                print(f"[{elapsed:.1f}s] Caught {sig_type} | Signal: {rssi} dBm")
except KeyboardInterrupt:
    print("\nSession paused by user.")

ser.close()

if len(captured_data) < 5:
    print("Insufficient data points collected to plot.")
    exit()

# Save the matrix locally
df = pd.DataFrame(captured_data, columns=['TimeOffset', 'Channel', 'RSSI', 'Type', 'MAC'])
df.to_csv(CSV_FILE, index=False)
print(f"Data matrix saved to {CSV_FILE}")

# Mathematical grid mapping and interpolation
x = df['TimeOffset'].values
y = df['Channel'].values
z = df['RSSI'].values

xi = np.linspace(x.min(), x.max(), 100)
yi = np.linspace(1, 11, 11)
xi, yi = np.meshgrid(xi, yi)

zi = griddata((x, y), z, (xi, yi), method='linear', fill_value=np.min(z))

# Plotting properties
plt.figure(figsize=(12, 6))
heatmap = plt.imshow(zi, extent=[x.min(), x.max(), 1, 11], origin='lower',
                     cmap='jet', aspect='auto', vmin=-95, vmax=-30)

plt.colorbar(heatmap, label='Signal Strength (RSSI in dBm)')
plt.title('Passive Radio Frequency Space Map')
plt.xlabel('Relative Travel Timeline (Seconds)')
plt.ylabel('Wi-Fi Channel Frequencies')
plt.scatter(x, y, c='black', s=5, alpha=0.3)
plt.grid(True, linestyle='--', alpha=0.5)
plt.show()
