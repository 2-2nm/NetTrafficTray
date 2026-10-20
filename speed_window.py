from __future__ import annotations

import ctypes
import os
import queue
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nettraffictray import AppState

APP_NAME = "NetTrafficTray"

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9


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


def _trim(value: float, digits: int) -> str:
    if digits <= 0:
        return str(int(round(value)))
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


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


def work_area() -> tuple[int, int, int, int]:
    if os.name == "nt":
        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    return (0, 0, 1280, 720)


def snap_near_clock(width: int, height: int) -> tuple[int, int]:
    left, top, right, bottom = work_area()
    margin = 10
    if os.name == "nt":
        screen_w = int(ctypes.windll.user32.GetSystemMetrics(0))
        screen_h = int(ctypes.windll.user32.GetSystemMetrics(1))
    else:
        screen_w, screen_h = right, bottom
    if screen_h - bottom > 20:
        return right - width - 14, bottom - height - margin
    if top > 20:
        return right - width - 14, top + margin
    if left > 20:
        return left + margin, bottom - height - margin
    if screen_w - right > 20:
        return right - width - margin, bottom - height - margin
    return right - width - 14, bottom - height - margin


def virtual_screen() -> tuple[int, int, int, int]:
    if os.name != "nt":
        return (0, 0, 1920, 1080)
    user32 = ctypes.windll.user32
    return (
        int(user32.GetSystemMetrics(76)),
        int(user32.GetSystemMetrics(77)),
        int(user32.GetSystemMetrics(78)),
        int(user32.GetSystemMetrics(79)),
    )


def _pick_font(tkfont) -> str:
    try:
        families = set(tkfont.families())
    except Exception:
        return "TkFixedFont"
    for name in (
        "Cascadia Mono",
        "Consolas",
        "Segoe UI",
        "DejaVu Sans Mono",
        "Liberation Mono",
        "Courier New",
    ):
        if name in families:
            return name
    return "TkFixedFont"


def _round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    radius = max(0, min(radius, int((x2 - x1) / 2), int((y2 - y1) / 2)))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _sparkline(canvas, down, up, x, y, w, h) -> None:
    if w <= 2 or h <= 2:
        return
    peak = 1.0
    for seq in (down, up):
        if seq:
            peak = max(peak, max(seq))

    def line(seq, color: str) -> None:
        if not seq:
            return
        if len(seq) == 1:
            seq = [seq[0], seq[0]]
        coords = []
        last = len(seq) - 1
        for i, value in enumerate(seq):
            px = x + (i / last) * w
            py = y + h - (max(0.0, value) / peak) * (h - 1)
            coords.extend((px, py))
        canvas.create_line(*coords, fill=color, width=1.6, smooth=True)

    line(down, "#2EE6A6")
    line(up, "#FFB020")


class SpeedWindow:
    BASE_W = 260
    BASE_H = 92

    def __init__(self, state: AppState) -> None:
        import tkinter as tk
        from tkinter import font as tkfont

        self.state = state
        self.tk = tk
        self.commands: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)

        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass
        self.scale = self._scale()
        self.width = int(self.BASE_W * self.scale)
        self.height = int(self.BASE_H * self.scale)
        self.root.geometry(f"{self.width}x{self.height}")
        self.root.configure(bg="#0C0F14")
        try:
            self.root.attributes("-alpha", 0.96)
        except tk.TclError:
            pass
        try:
            self.root.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        self.apply_top()

        family = _pick_font(tkfont)
        px = lambda size: -max(8, int(round(size * self.scale)))

        self.font_speed = tkfont.Font(family=family, size=px(16), weight="bold")
        self.font_unit = tkfont.Font(family=family, size=px(12), weight="bold")
        self.font_small = tkfont.Font(family=family, size=px(9))
        self.font_arrow = tkfont.Font(family=family, size=px(13), weight="bold")

        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            highlightthickness=0,
            bd=0,
            bg="#0C0F14",
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.configure(cursor="fleur")

        self._drag = None
        self._moved = False
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._popup)
        self.root.bind("<Button-3>", self._popup)
        self.root.bind("<Unmap>", self._on_unmap)
        self.menu = self._build_menu()
        self._taskbar_hidden = False
        self._closed = False
        if state.window_x is None or state.window_y is None:
            self.root.after(0, self.snap)
        else:
            self._move_to(state.window_x, state.window_y)

    def _scale(self) -> float:
        try:
            dpi = float(self.root.winfo_fpixels("1i"))
        except Exception:
            dpi = 96.0
        return min(2.5, max(1.0, dpi / 96.0))

    def run(self) -> None:
        self.apply_visibility()
        self.root.after(60, self._pump)
        self.root.mainloop()

    def ui(self, fn) -> None:
        self.commands.put(fn)

    def _pump(self) -> None:
        if self._closed:
            return
        if not self._taskbar_hidden:
            self._taskbar_hidden = True
            self._hide_from_taskbar()
        try:
            while True:
                fn = self.commands.get_nowait()
                try:
                    fn()
                except Exception:
                    log("window command failed\n" + traceback.format_exc())
                if self._closed:
                    return
        except queue.Empty:
            pass
        self._ensure_visible()
        try:
            self._redraw()
        except Exception:
            log("redraw failed\n" + traceback.format_exc())
        if not self._closed:
            self.root.after(200, self._pump)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.root.quit()
        except Exception:
            pass

    def _hwnd(self) -> int:
        hwnd = self.root.winfo_id()
        parent = ctypes.windll.user32.GetParent(hwnd)
        return parent or hwnd

    def _reassert_top(self) -> None:
        if os.name != "nt" or not self.state.always_on_top:
            return
        style = ctypes.windll.user32.GetWindowLongW(self._hwnd(), GWL_EXSTYLE)
        if not (style & WS_EX_TOPMOST):
            self.root.attributes("-topmost", False)
            self.root.attributes("-topmost", True)

    def _ensure_visible(self) -> None:
        if self._closed or not self.state.show_window:
            return
        try:
            if str(self.root.state()) != "normal":
                self.root.deiconify()
            if os.name == "nt":
                user32 = ctypes.windll.user32
                hwnd = self._hwnd()
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_RESTORE)
                elif not user32.IsWindowVisible(hwnd):
                    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                self._reassert_top()
        except Exception:
            log("ensure visible failed\n" + traceback.format_exc())

    def _on_unmap(self, event) -> None:
        if event.widget is not self.root or self._closed or not self.state.show_window:
            return
        try:
            self.root.after(50, self._ensure_visible)
        except Exception:
            pass

    def apply_visibility(self) -> None:
        if self.state.show_window:
            self.root.deiconify()
            self.apply_top()
            try:
                self.root.lift()
            except Exception:
                pass
        else:
            self.root.withdraw()

    def show_and_raise(self) -> None:
        self.state.set_show_window(True)
        self.apply_visibility()
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            if not self.state.always_on_top:
                self.root.after(300, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:
            pass

    def apply_top(self) -> None:
        try:
            self.root.attributes("-topmost", bool(self.state.always_on_top))
        except Exception:
            pass

    def snap(self) -> None:
        self.state.set_show_window(True)
        self.apply_visibility()
        x, y = snap_near_clock(self.width, self.height)
        self._move_to(x, y)
        self.state.window_x = self.root.winfo_x()
        self.state.window_y = self.root.winfo_y()
        self.state.save()

    def _move_to(self, x: int, y: int) -> None:
        vx, vy, vw, vh = virtual_screen()
        if os.name != "nt":
            vx, vy = 0, 0
            vw = max(vw, self.root.winfo_screenwidth())
            vh = max(vh, self.root.winfo_screenheight())
        x = max(vx, min(int(x), vx + vw - self.width))
        y = max(vy, min(int(y), vy + vh - self.height))
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def _hide_from_taskbar(self) -> None:
        if os.name != "nt":
            return
        try:
            hwnd = self._hwnd()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            ctypes.windll.user32.SetWindowPos(
                hwnd, None, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0004 | 0x0020
            )
        except Exception:
            log("taskbar style failed\n" + traceback.format_exc())

    def _press(self, event) -> None:
        self._drag = (event.x, event.y)
        self._moved = False

    def _motion(self, event) -> None:
        if self._drag is None:
            return
        if abs(event.x - self._drag[0]) + abs(event.y - self._drag[1]) > 3:
            self._moved = True
        x = self.root.winfo_x() + event.x - self._drag[0]
        y = self.root.winfo_y() + event.y - self._drag[1]
        self.root.geometry(f"+{x}+{y}")

    def _release(self, _event) -> None:
        if self._moved:
            self.state.window_x = self.root.winfo_x()
            self.state.window_y = self.root.winfo_y()
            self.state.save()
        self._drag = None

    def _build_menu(self):
        tk = self.tk
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg="#161C28",
            fg="#E7EDF6",
            activebackground="#243044",
            activeforeground="#FFFFFF",
            bd=0,
        )
        menu.add_command(label="Performance", command=self.state.toggle_performance)
        menu.add_command(label="Hide speed window", command=self.state.toggle_window)
        menu.add_command(label="Move next to clock", command=self.state.snap)
        menu.add_command(label="Always on top", command=self.state.toggle_top)
        menu.add_separator()
        menu.add_command(label="Quit", command=self.state.quit)
        return menu

    def show_popup_menu(self, x: int, y: int) -> None:
        label = "Hide speed window" if self.state.show_window else "Show speed window"
        top_mark = "✓  " if self.state.always_on_top else "     "
        try:
            self.menu.entryconfig(1, label=label)
            self.menu.entryconfig(3, label=f"{top_mark}Always on top")
        except Exception:
            pass
        try:
            self.menu.tk_popup(x, y)
        finally:
            self.menu.grab_release()

    def _popup(self, event) -> None:
        self.show_popup_menu(event.x_root, event.y_root)

    def _redraw(self) -> None:
        snap = self.state.snapshot()
        c = self.canvas
        c.delete("all")
        w, h = self.width, self.height
        s = self.scale
        radius = int(12 * s)
        _round_rect(c, 1, 1, w - 2, h - 2, radius, fill="#10141C", outline="#2A3548")

        down_n, down_u = speed_parts(snap["down"], snap["unit"])
        up_n, up_u = speed_parts(snap["up"], snap["unit"])
        left = int(14 * s)

        c.create_text(left, int(22 * s), text="↓", fill="#2EE6A6", font=self.font_arrow, anchor="w")
        c.create_text(left, int(50 * s), text="↑", fill="#FFB020", font=self.font_arrow, anchor="w")

        num_x = left + int(16 * s)
        down_id = c.create_text(
            num_x, int(22 * s), text=down_n, fill="#FFFFFF", font=self.font_speed, anchor="w"
        )
        up_id = c.create_text(
            num_x, int(50 * s), text=up_n, fill="#FFFFFF", font=self.font_speed, anchor="w"
        )

        down_box = c.bbox(down_id) or (num_x, 0, num_x + int(48 * s), 0)
        up_box = c.bbox(up_id) or (num_x, 0, num_x + int(48 * s), 0)
        gap = int(6 * s)

        c.create_text(
            down_box[2] + gap, int(22 * s), text=down_u, fill="#CDE0F5", font=self.font_unit, anchor="w"
        )
        c.create_text(
            up_box[2] + gap, int(50 * s), text=up_u, fill="#CDE0F5", font=self.font_unit, anchor="w"
        )

        session = (
            f"{format_bytes(snap['session_down'])} down"
            f"    {format_bytes(snap['session_up'])} up"
        )
        sid = c.create_text(
            left, int(76 * s), text=session, fill="#9CA3AF", font=self.font_small, anchor="w"
        )
        sbox = c.bbox(sid) or (left, 0, left, 0)
        if snap["missing"]:
            nic = "offline"
        elif snap["adapter"]:
            nic = snap["adapter"]
        else:
            nic = ""
        if nic:
            probe = c.create_text(-400, -400, text=nic, font=self.font_small, anchor="w")
            pbox = c.bbox(probe)
            c.delete(probe)
            room = (w - int(12 * s)) - (sbox[2] + int(12 * s))
            if pbox and (pbox[2] - pbox[0]) <= room:
                c.create_text(
                    w - int(12 * s),
                    int(76 * s),
                    text=nic,
                    fill="#6B7280",
                    font=self.font_small,
                    anchor="e",
                )

        spark_x = int(180 * s)
        spark_y = int(14 * s)
        spark_w = w - spark_x - int(12 * s)
        spark_h = int(46 * s)
        _round_rect(
            c,
            spark_x,
            spark_y,
            spark_x + spark_w,
            spark_y + spark_h,
            max(4, int(6 * s)),
            fill="#161C28",
            outline="",
        )
        _sparkline(
            c,
            [row[0] for row in snap["history"]],
            [row[1] for row in snap["history"]],
            spark_x + int(4 * s),
            spark_y + int(5 * s),
            spark_w - int(8 * s),
            spark_h - int(10 * s),
        )
