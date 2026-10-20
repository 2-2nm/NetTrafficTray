# NetTrafficTray

![Python](https://img.shields.io/badge/PYTHON-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-TKINTER-2EA44F?style=for-the-badge)
![Platform](https://img.shields.io/badge/PLATFORM-WINDOWS_10_%2F_11-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![License](https://img.shields.io/badge/LICENSE-MIT-97CA00?style=for-the-badge)

A lightweight Windows network speed meter. A small always-visible window shows live download/upload speed, and a detailed **Performance** window shows traffic charts, data usage history and per-app usage.

<p align="center">
  <img width="900" alt="Performance window" src="https://github.com/user-attachments/assets/9d89cb4f-dc14-4196-bf64-ca85d3169fc3" />
</p>

<p align="center">
  <img width="259" alt="Speed window" src="https://github.com/user-attachments/assets/eb4b50ff-e494-441d-9faf-e0ce8874f96d" />
</p>

## Key Features

- **Live speed meter**: small, draggable window with download/upload speed and a mini graph. Optional *Always on top* and *Move next to clock*.
- **Tray icon**: hover for the live speed. Left-click toggles the meter, right-click opens the menu.
- **Performance window**
  - Live traffic chart with peak value and hover tooltip.
  - **Bandwidth used** (total data transferred) for Today, 7 days, 30 days, 6 months, 1 year, or any picked date.
  - Per-app list with icons, download/upload and share bar.
  - Current connection and IP address, e.g. `Connected to MyWiFi (WiFi)` / `Connected to Ethernet (Ethernet)`.
- **Adapter selection**: monitor all adapters or a single one.
- **Units**: bytes (KB/s, MB/s) or bits (Mbps, Gbps).
- **Launch at startup** (current user, no admin needed).
- **Private by design**: everything is stored locally in `%APPDATA%\NetTrafficTray`; nothing is uploaded.

## Requirements

- Windows 10 / 11
- Python 3.9+ (only needed to run from source or build the `.exe`)

## Run from source

```powershell
git clone https://github.com/<your-username>/NetTrafficTray.git
cd NetTrafficTray
pip install -r requirements.txt
python nettraffictray.py
```

## Build a standalone `.exe`

Put all `.py` files next to `build_windows.bat` and double-click it. The result is `dist\NetTrafficTray.exe` (single file, no console window).

## Where is my data?

| File | Purpose |
| --- | --- |
| `%APPDATA%\NetTrafficTray\config.json` | settings |
| `%APPDATA%\NetTrafficTray\usage_history.json` | daily usage history |
| `%APPDATA%\NetTrafficTray\app_paths.json` | app name → exe path (for icons) |
| `%APPDATA%\NetTrafficTray\nettraffictray.log` | log |

Delete the folder to reset everything.

Upgrading from an older build named *NetSpeedTray*? On first launch your settings, history and "launch at startup" entry are moved automatically to the new name.

## Notes and limitations

- **Per-app usage is an estimate.** Windows does not expose per-process network bytes without a driver, so NetTrafficTray uses I/O counters of processes that have active connections. These counters can include disk I/O, so treat the per-app numbers as approximate. Total traffic (download/upload) comes from the network adapters and is accurate.
- **Wi-Fi name**: on recent Windows 11 builds, `netsh` may hide the SSID unless Location services are enabled. NetTrafficTray falls back to your saved Wi-Fi profiles, so the name normally still shows correctly.
- The speed window is a regular always-on-top window, not an embedded taskbar widget.

## Built with

[pystray](https://github.com/moses-palmer/pystray) · [psutil](https://github.com/giampaolo/psutil) · [Pillow](https://python-pillow.org/) · Tkinter · [PyInstaller](https://pyinstaller.org/)

## License

[MIT](LICENSE)
