from __future__ import annotations

import ctypes
import datetime
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path


_DOWN = (46, 230, 166, 255)
_UP = (255, 176, 32, 255)
ICON_FILE = "nettraffictray.ico"

APP_NAME = "NetTrafficTray"
MUTEX_NAME = "Local\\NetTrafficTray.Singleton"
IPC_PORT = 57382


def config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "NetTrafficTray"
    return Path.home() / ".config" / "nettraffictray"


def log(message: str) -> None:
    try:
        folder = config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with (folder / "nettraffictray.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except Exception:
        pass


def _tell_user(message: str) -> None:
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
            return
        except Exception:
            pass
    try:
        sys.stderr.write(message + "\n")
    except Exception:
        pass


def _require_pil():
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        _tell_user(
            "Pillow is not installed.\n\n"
            "Open a command prompt and run:\n"
            "    python -m pip install pystray psutil pillow"
        )
        raise SystemExit(1)
    return Image, ImageDraw


def speed_parts(bps, mode: str) -> tuple[str, str]:
    if bps is None:
        return ("—", "")
    bps = max(0.0, float(bps))
    if mode == "mbps":
        bits = bps * 8.0
        if bits >= 1_000_000_000:
            return _trim(bits / 1_000_000_000, 2 if bits < 10_000_000_000 else 1), "Gbps"
        if bits >= 1_000_000:
            value = bits / 1_000_000
            return _trim(value, 2 if value < 10 else 1 if value < 100 else 0), "Mbps"
        value = bits / 1_000.0
        if value < 10:
            return _trim(value, 1), "Kbps"
        return _trim(value, 0 if value >= 100 else 1), "Kbps"
    if mode == "kbs":
        value = bps / 1024.0
        return _trim(value, 1 if value < 100 else 0), "KB/s"
    if mode == "mbs":
        value = bps / (1024.0 ** 2)
        return _trim(value, 2 if value < 10 else 1 if value < 100 else 0), "MB/s"
    steps = (
        (1024 ** 4, "TB/s"),
        (1024 ** 3, "GB/s"),
        (1024 ** 2, "MB/s"),
        (1024, "KB/s"),
    )
    for scale, unit in steps:
        if bps >= scale:
            value = bps / scale
            digits = 2 if value < 10 else 1 if value < 100 else 0
            return _trim(value, digits), unit
    return _trim(bps, 0), "B/s"


def format_speed(bps, mode: str) -> str:
    number, unit = speed_parts(bps, mode)
    return number if not unit else f"{number} {unit}"


def format_bytes(num: float) -> str:
    num = max(0.0, float(num))
    for scale, unit in (
        (1024 ** 4, "TB"),
        (1024 ** 3, "GB"),
        (1024 ** 2, "MB"),
        (1024, "KB"),
    ):
        if num >= scale:
            value = num / scale
            digits = 2 if value < 10 else 1 if value < 100 else 0
            return f"{_trim(value, digits)} {unit}"
    return f"{int(round(num))} B"


def _trim(value: float, digits: int) -> str:
    if digits <= 0:
        return str(int(round(value)))
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _draw_app_icon(size: int):
    Image, ImageDraw = _require_pil()
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = max(1, size // 16)
    radius = max(4, size // 6)
    draw.rounded_rectangle(
        (margin, margin, size - 1 - margin, size - 1 - margin),
        radius=radius,
        fill=(16, 20, 28, 255),
    )
    cx = size / 2.0
    arm = size * 0.16
    y0 = size * 0.30
    draw.polygon(
        [
            (cx, y0 + arm * 1.15),
            (cx - arm, y0),
            (cx - arm * 0.42, y0),
            (cx - arm * 0.42, y0 - arm * 0.95),
            (cx + arm * 0.42, y0 - arm * 0.95),
            (cx + arm * 0.42, y0),
            (cx + arm, y0),
        ],
        fill=_DOWN,
    )
    y1 = size * 0.70
    draw.polygon(
        [
            (cx, y1 - arm * 1.15),
            (cx - arm, y1),
            (cx - arm * 0.42, y1),
            (cx - arm * 0.42, y1 + arm * 0.95),
            (cx + arm * 0.42, y1 + arm * 0.95),
            (cx + arm * 0.42, y1),
            (cx + arm, y1),
        ],
        fill=_UP,
    )
    return image


def save_app_icon(path: str | Path) -> Path:
    path = Path(path)
    _draw_app_icon(256).save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)],
    )
    return path


def resource_path(name: str) -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    root = Path(bundle) if bundle else Path(__file__).resolve().parent
    return root / name


def load_tray_icon():
    Image, _ = _require_pil()
    image = None
    path = resource_path(ICON_FILE)
    if path.exists():
        try:
            with Image.open(path) as handle:
                image = handle.convert("RGBA")
        except Exception:
            log("could not read " + str(path) + "\n" + traceback.format_exc())
            image = None
    if image is None:
        image = _draw_app_icon(256)
    return image.resize((64, 64), Image.LANCZOS)


def _psutil():
    try:
        import psutil
    except ImportError:
        _tell_user(
            "psutil is not installed.\n\n"
            "Open a command prompt and run:\n"
            "    python -m pip install pystray psutil pillow"
        )
        raise SystemExit(1)
    return psutil


def loopback_names() -> set[str]:
    psutil = _psutil()
    names = set()
    try:
        tables = psutil.net_if_addrs()
    except Exception:
        return names
    for name, addresses in tables.items():
        low = name.lower()
        if low == "lo" or low.startswith("loopback") or "loopback" in low:
            names.add(name)
            continue
        for addr in addresses:
            ip = getattr(addr, "address", "") or ""
            if ip.startswith("127.") or ip == "::1":
                names.add(name)
                break
    return names


_VIRTUAL_HINTS = (
    "loopback", "virtual", "vethernet", "vmware", "vbox", "virtualbox", "hyper-v",
    "tap-", "tun", "vpn", "bluetooth", "pseudo", "docker", "wsl", "npcap",
    "tailscale", "zerotier", "hamachi", "host-only",
)
_WIFI_HINTS = ("wi-fi", "wifi", "wlan", "wireless")
_conn_lock = threading.Lock()
_conn_cache: dict = {}
_conn_busy: set = set()


def _run_hidden(cmd: list, timeout: float) -> str:
    if os.name != "nt":
        return ""
    import subprocess

    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=flags, errors="ignore",
        ).stdout
    except Exception:
        return ""


def _wifi_ssids() -> dict:
    out = _run_hidden(["netsh", "wlan", "show", "interfaces"], 4)
    result: dict = {}
    iface = None
    for line in out.splitlines():
        m = re.match(r"^\s*(?:Name|Nama)\s*:\s*(.+?)\s*$", line)
        if m:
            iface = m.group(1)
            continue
        m = re.match(r"^\s*SSID\s*:\s*(.+?)\s*$", line)
        if m and iface:
            result[iface] = m.group(1)
    return result


def _profile_names() -> dict:
    out = _run_hidden(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-NetConnectionProfile | ForEach-Object { $_.InterfaceAlias + '|' + $_.Name }",
        ],
        8,
    )
    result: dict = {}
    for line in out.splitlines():
        if "|" in line:
            alias, _, name = line.partition("|")
            if alias.strip() and name.strip():
                result[alias.strip()] = name.strip()
    return result


def _ssid_from_profile(profile: str) -> str | None:
    out = _run_hidden(["netsh", "wlan", "show", "profile", f"name={profile}"], 4)
    for line in out.splitlines():
        m = re.match(r'^\s*[^:]*SSID[^:]*:\s*"(.*)"\s*$', line)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def _wlan_profiles() -> list:
    out = _run_hidden(["netsh", "wlan", "show", "profiles"], 4)
    names = []
    for line in out.splitlines():
        m = re.match(r"^\s+[^:]+:\s*(.+?)\s*$", line)
        if m and m.group(1) != "<None>":
            names.append(m.group(1))
    return names


def _clean_wifi_name(network_name: str) -> str:
    profiles = _wlan_profiles()
    lookup = {p.lower(): p for p in profiles}
    name = network_name
    while True:
        hit = lookup.get(name.lower())
        if hit:
            return _ssid_from_profile(hit) or hit
        stripped = re.sub(r"\s+\d+$", "", name)
        if stripped == name:
            break
        name = stripped
    return _ssid_from_profile(network_name) or network_name


def _is_virtual(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _VIRTUAL_HINTS)


def _is_wifi_name(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _WIFI_HINTS)


def _primary_adapter(addrs: dict, stats: dict) -> str | None:
    ip = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(1.0)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        finally:
            sock.close()
    except Exception:
        ip = None
    if ip:
        for name, items in addrs.items():
            for a in items:
                if getattr(a, "family", None) == socket.AF_INET and a.address == ip:
                    return name
    return None


def _describe(adapter: str | None) -> tuple:
    psutil = _psutil()
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
    except Exception:
        return "", ""
    skip = loopback_names()

    def usable(name: str) -> bool:
        st = stats.get(name)
        if st is None or not st.isup or name in skip:
            return False
        return any(
            getattr(a, "family", None) == socket.AF_INET
            and not (a.address or "").startswith("169.254.")
            for a in addrs.get(name, [])
        )

    chosen = None
    if adapter:
        chosen = adapter
    else:
        primary = _primary_adapter(addrs, stats)
        if primary and usable(primary) and not _is_virtual(primary):
            chosen = primary
        else:
            for name in stats:
                if usable(name) and not _is_virtual(name):
                    chosen = name
                    break
    if not chosen or (not adapter and not usable(chosen)):
        return "Not connected", ""

    ip = ""
    for a in addrs.get(chosen, []):
        addr = a.address or ""
        if getattr(a, "family", None) == socket.AF_INET and not addr.startswith("169.254."):
            ip = addr
            break

    ssids = _wifi_ssids()
    if chosen in ssids or _is_wifi_name(chosen):
        ssid = ssids.get(chosen)
        if not ssid:
            profile = _profile_names().get(chosen)
            if profile:
                ssid = _clean_wifi_name(profile)
        return f"Connected to {ssid or chosen} (WiFi)", ip
    return f"Connected to {chosen} (Ethernet)", ip


def _refresh_connection(key) -> None:
    try:
        text, ip = _describe(key)
    except Exception:
        text, ip = "", ""
    with _conn_lock:
        old = _conn_cache.get(key)
        if not text and old:
            text, ip = old[0], old[1]
        _conn_cache[key] = (text, ip, time.time())
        _conn_busy.discard(key)


def connection_info(adapter: str | None = None) -> tuple:
    with _conn_lock:
        entry = _conn_cache.get(adapter)
        stale = entry is None or time.time() - entry[2] > 5.0
        if stale and adapter not in _conn_busy:
            _conn_busy.add(adapter)
            threading.Thread(
                target=_refresh_connection, args=(adapter,),
                name="nettraffic-conn", daemon=True,
            ).start()
        if entry and entry[0]:
            return entry[0], entry[1]
        return "Checking connection...", ""


def connection_label(adapter: str | None = None) -> str:
    return connection_info(adapter)[0]


def read_totals(adapter: str | None):
    psutil = _psutil()
    try:
        counters = psutil.net_io_counters(pernic=True) or {}
    except Exception:
        return None
    if adapter:
        row = counters.get(adapter)
        if row is None:
            return None
        return int(row.bytes_recv), int(row.bytes_sent)
    skip = loopback_names()
    recv = sent = 0
    for name, row in counters.items():
        if name in skip:
            continue
        recv += int(row.bytes_recv)
        sent += int(row.bytes_sent)
    return recv, sent


def enable_dpi() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NetTrafficTray.App")
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def notify_running_instance() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(("127.0.0.1", IPC_PORT))
            s.sendall(b"SHOW\n")
            return True
    except Exception:
        return False


def start_ipc_server(state: AppState) -> None:
    def server_loop():
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", IPC_PORT))
            server.listen(5)
            server.settimeout(2.0)
        except Exception as e:
            log(f"IPC server bind failed: {e}")
            return

        while state.running:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            try:
                conn.settimeout(1.0)
                data = conn.recv(1024).strip()
                if data in (b"SHOW", b"TOGGLE"):
                    state.show_and_raise()
                conn.close()
            except Exception:
                pass
        try:
            server.close()
        except Exception:
            pass

    threading.Thread(target=server_loop, name="nettraffic-ipc", daemon=True).start()


def already_running() -> bool:
    if notify_running_instance():
        log("sent show signal to running instance")
        return True
    if os.name == "nt":
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.windll.kernel32.GetLastError() == 183:
            return True
        globals()["_MUTEX_HANDLE"] = handle
    return False


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --autostart'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else python
    script = Path(__file__).resolve()
    return f'"{exe}" "{script}" --autostart'


def startup_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
        ) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def set_startup(enabled: bool) -> bool:
    if os.name != "nt":
        return False
    import winreg

    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
    return True


def _legacy_config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "NetSpeedTray"
    return Path.home() / ".config" / "netspeedtray"


def migrate_legacy() -> None:
    try:
        old = _legacy_config_dir()
        new = config_dir()
        if old.is_dir() and old != new:
            import shutil

            if not new.exists():
                try:
                    shutil.move(str(old), str(new))
                except Exception:
                    shutil.copytree(str(old), str(new))
                log(f"migrated data from {old}")
            elif not (new / "config.json").exists():
                shutil.copytree(str(old), str(new), dirs_exist_ok=True)
                log(f"copied data from {old}")
    except Exception:
        log("data migration failed\n" + traceback.format_exc())
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as key:
            try:
                winreg.QueryValueEx(key, "NetSpeedTray")
            except FileNotFoundError:
                return
            winreg.DeleteValue(key, "NetSpeedTray")
        set_startup(True)
        log("moved autostart entry to the new name")
    except Exception:
        log("autostart migration failed\n" + traceback.format_exc())


class AppState:
    UNITS = ("auto", "kbs", "mbs", "mbps")
    PERIOD_DAYS = {"today": 1, "weekly": 7, "monthly": 30, "halfyear": 180, "yearly": 365}

    def __init__(self) -> None:
        self.unit = "auto"
        self.interval = 1.0
        self.adapter: str | None = None
        self.show_window = True
        self.show_perf_window = False
        self.always_on_top = True
        self.launch_at_startup = False
        self.window_x: int | None = None
        self.window_y: int | None = None
        self.perf_x: int | None = None
        self.perf_y: int | None = None
        self.down_bps = 0.0
        self.up_bps = 0.0
        self.session_down = 0
        self.session_up = 0
        self.adapter_missing = False
        self.history: deque = deque(maxlen=48)
        self.usage_history: dict = {"daily": {}}
        self.running = True
        self.icon = None
        self.win = None
        self.perf_win = None

        self._lock = threading.Lock()
        self._usage_lock = threading.Lock()
        self._active_apps_lock = threading.Lock()
        self._active_apps: list[tuple[str, int, int]] = []
        self._proc_io_cache: dict[int, tuple[int, int, float]] = {}
        self._app_paths: dict[str, str] = {}
        self._paths_lock = threading.Lock()
        self._paths_dirty = False

        self._baseline = None
        self._icon_key = None
        self._sampler_started = False
        self._last_toggle = 0.0
        self._last_usage_save = 0.0

    def load(self) -> None:
        path = config_dir() / "config.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        unit = data.get("unit", "auto")
        self.unit = unit if unit in self.UNITS else "auto"
        try:
            interval = float(data.get("interval", 1.0))
        except (TypeError, ValueError):
            interval = 1.0
        self.interval = interval if interval in (0.5, 1.0, 2.0) else 1.0
        adapter = data.get("adapter")
        self.adapter = adapter if isinstance(adapter, str) and adapter else None

        is_autostart = "--autostart" in sys.argv
        if is_autostart:
            self.show_window = bool(data.get("show_window", False))
        else:
            self.show_window = True

        self.always_on_top = bool(data.get("always_on_top", True))
        self.launch_at_startup = startup_enabled() or bool(data.get("launch_at_startup", False))
        self.window_x = _opt_int(data.get("window_x"))
        self.window_y = _opt_int(data.get("window_y"))
        self.perf_x = _opt_int(data.get("perf_x"))
        self.perf_y = _opt_int(data.get("perf_y"))
        self.load_usage()
        self.load_app_paths()

    def save(self) -> None:
        payload = {
            "unit": self.unit,
            "interval": self.interval,
            "adapter": self.adapter,
            "show_window": self.show_window,
            "always_on_top": self.always_on_top,
            "launch_at_startup": self.launch_at_startup,
            "window_x": self.window_x,
            "window_y": self.window_y,
            "perf_x": self.perf_x,
            "perf_y": self.perf_y,
        }
        try:
            folder = config_dir()
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "config.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(target)
        except Exception:
            log("could not save config\n" + traceback.format_exc())

    def load_usage(self) -> None:
        path = config_dir() / "usage_history.json"
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                with self._usage_lock:
                    if "daily" in raw and isinstance(raw["daily"], dict):
                        self.usage_history = raw
                    else:
                        self.usage_history = {"daily": {}}
                        for k, v in raw.items():
                            if isinstance(v, dict):
                                self.usage_history["daily"][k] = {
                                    "down": int(v.get("down", 0)),
                                    "up": int(v.get("up", 0)),
                                    "apps": v.get("apps", {})
                                }
            else:
                with self._usage_lock:
                    self.usage_history = {"daily": {}}
        except Exception:
            with self._usage_lock:
                self.usage_history = {"daily": {}}

    def save_usage(self) -> None:
        try:
            folder = config_dir()
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "usage_history.json"
            tmp = target.with_suffix(".tmp")
            with self._usage_lock:
                data = json.loads(json.dumps(self.usage_history))
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(target)
        except Exception:
            log("could not save daily usage history\n" + traceback.format_exc())

    def load_app_paths(self) -> None:
        path = config_dir() / "app_paths.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(raw, dict):
            return
        with self._paths_lock:
            self._app_paths = {
                str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v
            }

    def save_app_paths(self) -> None:
        with self._paths_lock:
            if not self._paths_dirty:
                return
            data = dict(self._app_paths)
            self._paths_dirty = False
        try:
            folder = config_dir()
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "app_paths.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(target)
        except Exception:
            log("could not save app paths\n" + traceback.format_exc())

    def remember_app_path(self, name: str, exe: str) -> None:
        if not name or not exe:
            return
        with self._paths_lock:
            if self._app_paths.get(name) != exe:
                self._app_paths[name] = exe
                self._paths_dirty = True

    def app_path(self, name: str) -> str | None:
        with self._paths_lock:
            return self._app_paths.get(name)

    def _history_app_names(self) -> set[str]:
        names: set[str] = set()
        with self._usage_lock:
            for day in self.usage_history.get("daily", {}).values():
                names.update(day.get("apps", {}).keys())
        return names

    def _backfill_app_paths(self) -> None:
        with self._paths_lock:
            known = set(self._app_paths)
        missing = {n for n in self._history_app_names() if n not in known and n != "System / Other"}
        if not missing:
            return
        psutil = _psutil()
        for proc in psutil.process_iter(["name", "exe"]):
            info = proc.info
            name = info.get("name")
            exe = info.get("exe")
            if name in missing and exe:
                self.remember_app_path(name, exe)

    def get_period_usage(
        self, period: str = "today", date: datetime.date | None = None
    ) -> tuple[int, int, list[tuple[str, int, int, int]]]:
        today = datetime.date.today()
        if period == "date" and date is not None:
            target_dates = {date.strftime("%Y-%m-%d")}
        else:
            days = self.PERIOD_DAYS.get(period, 1)
            target_dates = {(today - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)}

        total_down = 0
        total_up = 0
        app_totals: dict[str, dict[str, int]] = {}

        with self._usage_lock:
            daily_map = self.usage_history.get("daily", {})
            for date_str in target_dates:
                day_data = daily_map.get(date_str)
                if not day_data:
                    continue
                total_down += int(day_data.get("down", 0))
                total_up += int(day_data.get("up", 0))

                apps_data = day_data.get("apps", {})
                for app_name, app_stat in apps_data.items():
                    if app_name not in app_totals:
                        app_totals[app_name] = {"down": 0, "up": 0}
                    app_totals[app_name]["down"] += int(app_stat.get("down", 0))
                    app_totals[app_name]["up"] += int(app_stat.get("up", 0))

        sorted_apps = []
        for name, stats in app_totals.items():
            d = stats["down"]
            u = stats["up"]
            tot = d + u
            if tot > 0:
                sorted_apps.append((name, d, u, tot))

        sorted_apps.sort(key=lambda x: x[3], reverse=True)
        return total_down, total_up, sorted_apps

    def usage_dates(self) -> set[str]:
        with self._usage_lock:
            return {
                key
                for key, day in self.usage_history.get("daily", {}).items()
                if isinstance(day, dict) and int(day.get("down", 0)) + int(day.get("up", 0)) > 0
            }

    def snapshot(self) -> dict:
        with self._lock:
            down_val = self.down_bps
            up_val = self.up_bps
            s_down = self.session_down
            s_up = self.session_up
            hist = list(self.history)
            unit_val = self.unit
            adapter_val = self.adapter
            missing_val = self.adapter_missing
            show_win = self.show_window
            top_val = self.always_on_top

        today_down, today_up, _ = self.get_period_usage("today")

        return {
            "down": down_val,
            "up": up_val,
            "session_down": s_down,
            "session_up": s_up,
            "today_down": today_down,
            "today_up": today_up,
            "history": hist,
            "unit": unit_val,
            "adapter": adapter_val,
            "connection": connection_info(adapter_val)[0],
            "ip": connection_info(adapter_val)[1],
            "missing": missing_val,
            "show_window": show_win,
            "always_on_top": top_val,
        }

    def tooltip(self) -> str:
        snap = self.snapshot()
        if snap["adapter"] is not None and snap["missing"]:
            which = f"{snap['adapter']} (offline)"
        else:
            which = snap["connection"]
        return (
            f"Down: {format_speed(snap['down'], snap['unit'])}\n"
            f"Up: {format_speed(snap['up'], snap['unit'])}\n"
            f"Today: {format_bytes(snap['today_down'])} ↓ / {format_bytes(snap['today_up'])} ↑\n"
            f"Session: {format_bytes(snap['session_down'])} ↓ / {format_bytes(snap['session_up'])} ↑\n"
            f"{which}"
        )

    def start_sampler(self) -> None:
        if self._sampler_started:
            return
        self._sampler_started = True
        threading.Thread(
            target=self._sample_loop, name="nettraffic-sampler", daemon=True
        ).start()
        threading.Thread(
            target=self._app_tracker_loop, name="nettraffic-app-tracker", daemon=True
        ).start()

    def _sample_loop(self) -> None:
        while self.running:
            started = time.perf_counter()
            try:
                self._sample_once()
            except Exception:
                log("sample failed\n" + traceback.format_exc())
            delay = self.interval
            left = delay - (time.perf_counter() - started)
            if left > 0:
                time.sleep(left)

    def _app_tracker_loop(self) -> None:
        tick = 0
        while self.running:
            try:
                apps_io = self._scan_active_network_apps()
                with self._active_apps_lock:
                    self._active_apps = apps_io
            except Exception:
                pass
            if tick % 15 == 0:
                try:
                    self._backfill_app_paths()
                except Exception:
                    pass
                self.save_app_paths()
            tick += 1
            time.sleep(2.0)

    def _scan_active_network_apps(self) -> list[tuple[str, int, int]]:
        psutil = _psutil()
        app_io_map: dict[str, list[int]] = {}
        now = time.time()
        try:
            conns = psutil.net_connections(kind="inet")
            active_pids = set()
            active_states = ("ESTABLISHED", "SYN_SENT", "SYN_RECV")
            for c in conns:
                if c.pid and getattr(c, "status", "") in active_states:
                    active_pids.add(c.pid)

            for pid in active_pids:
                try:
                    p = psutil.Process(pid)
                    name = p.name()
                    if not name or name.lower() in ("system", "idle", "svchost.exe"):
                        continue
                    try:
                        exe = p.exe()
                    except Exception:
                        exe = ""
                    if exe:
                        self.remember_app_path(name, exe)
                    io = p.io_counters()
                    r_bytes, w_bytes = io.read_bytes, io.write_bytes

                    if pid in self._proc_io_cache:
                        prev_r, prev_w, prev_t = self._proc_io_cache[pid]
                        dr = max(0, r_bytes - prev_r)
                        dw = max(0, w_bytes - prev_w)
                    else:
                        dr, dw = 0, 0

                    self._proc_io_cache[pid] = (r_bytes, w_bytes, now)

                    if name not in app_io_map:
                        app_io_map[name] = [0, 0]
                    app_io_map[name][0] += dr
                    app_io_map[name][1] += dw
                except Exception:
                    pass
        except Exception:
            pass

        for pid in list(self._proc_io_cache.keys()):
            if now - self._proc_io_cache[pid][2] > 30.0:
                del self._proc_io_cache[pid]

        result = []
        for name, (dr, dw) in app_io_map.items():
            result.append((name, dr, dw))
        return result

    def _sample_once(self) -> None:
        with self._lock:
            adapter = self.adapter
        totals = read_totals(adapter)
        now = time.perf_counter()

        d_recv = 0
        d_sent = 0

        with self._lock:
            if self.adapter != adapter:
                return
            mode = self.unit
            if totals is None:
                self.adapter_missing = True
                self.down_bps = 0.0
                self.up_bps = 0.0
                self._baseline = None
            else:
                self.adapter_missing = False
                if self._baseline is None:
                    self._baseline = (totals, now)
                    self.down_bps = 0.0
                    self.up_bps = 0.0
                else:
                    (prev, prev_t) = self._baseline
                    self._baseline = (totals, now)
                    dt = now - prev_t
                    if 0.2 <= dt <= 10.0:
                        d_recv = max(0, totals[0] - prev[0])
                        d_sent = max(0, totals[1] - prev[1])
                        self.down_bps = d_recv / dt
                        self.up_bps = d_sent / dt
                        self.session_down += d_recv
                        self.session_up += d_sent
                        self.history.append((self.down_bps, self.up_bps))

            down = self.down_bps
            up = self.up_bps

        if d_recv > 0 or d_sent > 0:
            with self._active_apps_lock:
                current_apps_io = list(self._active_apps)
            self._record_usage(d_recv, d_sent, current_apps_io)

        if now - self._last_usage_save > 15.0:
            self._last_usage_save = now
            self.save_usage()

        self._publish(down, up, mode)

    def _record_usage(self, d_recv: int, d_sent: int, active_apps_io: list[tuple[str, int, int]]) -> None:
        today_key = time.strftime("%Y-%m-%d")

        tot_read_io = sum(dr for _, dr, _ in active_apps_io)
        tot_write_io = sum(dw for _, _, dw in active_apps_io)

        with self._usage_lock:
            daily_map = self.usage_history.setdefault("daily", {})
            if today_key not in daily_map:
                daily_map[today_key] = {"down": 0, "up": 0, "apps": {}}

            day_rec = daily_map[today_key]
            day_rec["down"] += d_recv
            day_rec["up"] += d_sent

            if "apps" not in day_rec:
                day_rec["apps"] = {}

            if not active_apps_io or (tot_read_io == 0 and tot_write_io == 0):
                if "System / Other" not in day_rec["apps"]:
                    day_rec["apps"]["System / Other"] = {"down": 0, "up": 0}
                day_rec["apps"]["System / Other"]["down"] += d_recv
                day_rec["apps"]["System / Other"]["up"] += d_sent
            else:
                for app_name, dr, dw in active_apps_io:
                    if app_name not in day_rec["apps"]:
                        day_rec["apps"][app_name] = {"down": 0, "up": 0}

                    if tot_read_io > 0:
                        app_d = int(d_recv * (dr / tot_read_io))
                    elif tot_write_io > 0:
                        app_d = int(d_recv * ((dr + dw) / (tot_read_io + tot_write_io)))
                    else:
                        app_d = d_recv // len(active_apps_io)

                    if tot_write_io > 0:
                        app_u = int(d_sent * (dw / tot_write_io))
                    elif tot_read_io > 0:
                        app_u = int(d_sent * ((dr + dw) / (tot_read_io + tot_write_io)))
                    else:
                        app_u = d_sent // len(active_apps_io)

                    day_rec["apps"][app_name]["down"] += app_d
                    day_rec["apps"][app_name]["up"] += app_u

    def _publish(self, down, up, mode: str) -> None:
        icon = self.icon
        if icon is None:
            return
        key = (format_speed(down, mode), format_speed(up, mode), mode)
        try:
            if key != self._icon_key:
                self._icon_key = key
                icon.title = self.tooltip()
        except Exception:
            log("tooltip update failed\n" + traceback.format_exc())

    def _on_ui(self, fn) -> None:
        if self.win is None:
            return
        if threading.current_thread() is threading.main_thread():
            try:
                fn()
                return
            except Exception:
                log("window command failed\n" + traceback.format_exc())
        self.win.ui(fn)

    def refresh_menu(self) -> None:
        icon = self.icon
        if icon is None:
            return
        try:
            icon.update_menu()
        except Exception:
            log("tray menu refresh failed\n" + traceback.format_exc())

    def set_show_window(self, visible: bool) -> None:
        self.show_window = bool(visible)
        self.save()
        self.refresh_menu()

    def toggle_window(self, _icon=None, _item=None) -> None:
        now = time.perf_counter()
        if now - self._last_toggle < 0.25:
            return
        self._last_toggle = now
        self.set_show_window(not self.show_window)
        self._on_ui(self.win.apply_visibility if self.win else (lambda: None))

    def toggle_performance(self, _icon=None, _item=None) -> None:
        self.show_perf_window = not self.show_perf_window
        if self.perf_win:
            self._on_ui(self.perf_win.apply_visibility)

    def show_and_raise(self) -> None:
        self.set_show_window(True)
        if self.win is not None:
            self._on_ui(self.win.show_and_raise)

    def toggle_top(self, _icon=None, _item=None) -> None:
        self.always_on_top = not self.always_on_top
        self.save()
        self.refresh_menu()
        self._on_ui(self.win.apply_top if self.win else (lambda: None))
        if self.perf_win:
            self._on_ui(self.perf_win.apply_top)

    def toggle_startup(self, _icon=None, _item=None) -> None:
        target = not startup_enabled()
        if set_startup(target):
            self.launch_at_startup = target
            self.save()
            self.refresh_menu()

    def snap(self, _icon=None, _item=None) -> None:
        self.set_show_window(True)
        self._on_ui(self.win.snap if self.win else (lambda: None))

    def quit(self, icon=None, _item=None) -> None:
        self.running = False
        self.save()
        self.save_usage()
        self.save_app_paths()
        if self.win is not None:
            if threading.current_thread() is threading.main_thread():
                self.win.close()
            else:
                self.win.ui(self.win.close)
        target = icon or self.icon
        if target is not None and hasattr(target, "stop"):
            try:
                target.stop()
            except Exception:
                pass

    def build_pystray_menu(self):
        import pystray
        from pystray import Menu, MenuItem

        def toggle_win_label(_item):
            return "Hide speed window" if self.show_window else "Show speed window"

        return Menu(
            MenuItem(toggle_win_label, self.toggle_window, default=True),
            MenuItem("Performance", self.toggle_performance),
            MenuItem("Move next to clock", self.snap),
            MenuItem("Always on top", self.toggle_top, checked=lambda _item: self.always_on_top),
            Menu.SEPARATOR,
            MenuItem("Launch at startup", self.toggle_startup, checked=lambda _item: startup_enabled()),
            Menu.SEPARATOR,
            MenuItem("Quit", self.quit),
        )


def _opt_int(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    if "--self-test" in sys.argv:
        self_test()
        return
    migrate_legacy()
    enable_dpi()
    if already_running():
        log("already running; signal sent to primary instance")
        return
    _require_pil()
    _psutil()

    try:
        import pystray
    except ImportError:
        _tell_user(
            "pystray is not installed.\n\n"
            "Open a command prompt and run:\n"
            "    python -m pip install pystray psutil pillow"
        )
        raise SystemExit(1)

    from speed_window import SpeedWindow
    from performance_window import PerformanceWindow

    state = AppState()
    state.load()
    log("starting NetTrafficTray")

    start_ipc_server(state)

    initial_icon = load_tray_icon()

    icon = pystray.Icon(
        APP_NAME,
        initial_icon,
        "NetTrafficTray",
        menu=state.build_pystray_menu(),
        action=lambda _icon, _item: state.toggle_window(),
    )
    state.icon = icon

    icon.run_detached()
    state.start_sampler()

    try:
        window = SpeedWindow(state)
        state.win = window

        perf_window = PerformanceWindow(state, window.root)
        state.perf_win = perf_window

        orig_pump = window._pump
        def combined_pump():
            perf_window.redraw()
            orig_pump()

        window._pump = combined_pump
        window.run()
    finally:
        state.running = False
        state.save()
        state.save_usage()
        state.save_app_paths()
        try:
            icon.stop()
        except Exception:
            pass
        log("stopped")


def self_test() -> None:
    samples = [0, 512, 1536, 12_000, 100 * 1024, 999 * 1024, 1.26 * 1024 ** 2]
    print("bytes/s        auto label        mbps label")
    for bps in samples:
        print(f"{bps:12.0f}   {format_speed(bps, 'auto'):<16}  {format_speed(bps, 'mbps')}")
    print("self-test ok")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log("fatal\n" + traceback.format_exc())
        _tell_user("NetTrafficTray error:\n\n" + traceback.format_exc()[-1200:])
        raise SystemExit(1)
