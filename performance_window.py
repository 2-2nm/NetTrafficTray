from __future__ import annotations

import calendar
import datetime
import math
import time
import traceback
import zlib
from functools import lru_cache
from typing import TYPE_CHECKING

from app_icons import AppIconProvider

if TYPE_CHECKING:
    from nettraffictray import AppState


FONT_DISPLAY = (
    ("Bahnschrift SemiBold", "normal"),
    ("Bahnschrift", "bold"),
    ("Segoe UI Variable Display", "bold"),
    ("Segoe UI Semibold", "normal"),
    ("Segoe UI", "bold"),
    ("Inter", "bold"),
    ("Helvetica Neue", "bold"),
    ("Arial", "bold"),
)
FONT_TEXT = (
    ("Bahnschrift", "normal"),
    ("Segoe UI Variable Text", "normal"),
    ("Segoe UI", "normal"),
    ("Inter", "normal"),
    ("Helvetica Neue", "normal"),
    ("Arial", "normal"),
)

FONT_MONO = (
    ("Cascadia Mono", "normal"),
    ("Consolas", "normal"),
    ("DejaVu Sans Mono", "normal"),
    ("Liberation Mono", "normal"),
    ("Courier New", "normal"),
)

MIN_CHART_SCALE = 64 * 1024


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


def _unit_scale(bps: float, mode: str) -> float:
    bps = max(0.0, float(bps))
    if mode == "mbps":
        bits = bps * 8.0
        if bits >= 1_000_000_000:
            return 1_000_000_000 / 8.0
        if bits >= 1_000_000:
            return 1_000_000 / 8.0
        return 1_000 / 8.0
    if mode == "kbs":
        return 1024.0
    if mode == "mbs":
        return 1024.0 ** 2
    for scale in (1024 ** 4, 1024 ** 3, 1024 ** 2, 1024):
        if bps >= scale:
            return float(scale)
    return 1.0


def _nice_ceiling(value: float, mode: str) -> float:
    value = max(float(value), MIN_CHART_SCALE)
    unit = _unit_scale(value, mode)
    v = value / unit
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 3, 4, 5, 6, 8, 10):
        if v <= m * mag * 1.0000001:
            return m * mag * unit
    return 10 * mag * unit


@lru_cache(maxsize=2048)
def _mix(a: str, b: str, t: float) -> str:
    a = a.lstrip("#")
    b = b.lstrip("#")
    t = max(0.0, min(1.0, t))
    out = []
    for i in (0, 2, 4):
        ca = int(a[i:i + 2], 16)
        cb = int(b[i:i + 2], 16)
        out.append(int(round(ca + (cb - ca) * t)))
    return "#{:02X}{:02X}{:02X}".format(*out)


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


def _catmull(points, steps: int = 4):
    n = len(points)
    if n < 3:
        return list(points)
    out = []
    for i in range(n - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < n else points[i + 1]
        for k in range(steps):
            t = k / steps
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                2 * p1[0]
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                2 * p1[1]
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))
    out.append(points[-1])
    return out


def _resolve_font(families, options) -> tuple[str, str]:
    for family, weight in options:
        if family in families:
            return family, weight
    return options[-1]


def _fit_text(font, text: str, max_w: float) -> str:
    if max_w <= 0:
        return ""
    if font.measure(text) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid].rstrip() + "…") <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + "…") if lo > 0 else "…"


class PerformanceWindow:
    BASE_W = 1200
    BASE_H = 800

    C_KEY = "#010203"
    C_BG = "#0B0D12"
    C_SURFACE = "#12151D"
    C_SURFACE_2 = "#181C26"
    C_TRACK = "#0D1016"
    C_BAR_TRACK = "#1B202A"
    C_SCROLL_TRACK = "#141820"
    C_SCROLL_THUMB = "#566880"
    C_RAISED = "#232838"
    C_BORDER = "#222836"
    C_BORDER_STRONG = "#303749"

    C_TEXT = "#EEF1F8"
    C_TEXT_2 = "#A3ACBF"
    C_TEXT_3 = "#69728A"

    C_DOWN = "#34D399"
    C_UP = "#FBBF24"
    C_ACCENT = "#818CF8"
    C_DANGER = "#F0525C"

    AVATAR_HUES = (
        "#818CF8", "#34D399", "#FBBF24", "#F472B6",
        "#38BDF8", "#FB923C", "#A78BFA", "#2DD4BF",
    )

    PERIODS = (
        ("today", "Today"),
        ("weekly", "7 days"),
        ("monthly", "30 days"),
        ("halfyear", "6 months"),
        ("yearly", "1 year"),
    )
    PERIOD_TITLE = {
        "today": "Today",
        "weekly": "Last 7 days",
        "monthly": "Last 30 days",
        "halfyear": "Last 6 months",
        "yearly": "Last year",
    }
    WEEKDAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")

    def __init__(self, state: AppState, parent_root) -> None:
        import tkinter as tk
        from tkinter import font as tkfont

        self.state = state
        self.tk = tk
        self.active_tab = "today"
        self.sel_date: datetime.date | None = None
        self.cal_open = False
        self.cal_month = (datetime.date.today().year, datetime.date.today().month)
        self._cal_anchor = None
        self.scroll_row = 0
        self.max_scroll = 0

        self._hits: list[tuple[float, float, float, float, str]] = []
        self._hover: str | None = None
        self._pressed: str | None = None
        self._mouse: tuple[int, int] | None = None
        self._cursor = "arrow"
        self._pending = False
        self._wheel_acc = 0.0
        self._usage_cache = None
        self._drag = None
        self._moved = False

        self.top = tk.Toplevel(parent_root)
        self.top.title("Network Performance")
        self.top.overrideredirect(True)
        self.top.protocol("WM_DELETE_WINDOW", self._hide)

        self.scale = self._scale()
        self.width = int(self.BASE_W * self.scale)
        self.height = int(self.BASE_H * self.scale)
        self._center_on_screen()

        canvas_bg = self.C_KEY
        self.top.configure(bg=self.C_KEY)
        try:
            self.top.attributes("-transparentcolor", self.C_KEY)
        except tk.TclError:
            canvas_bg = self.C_BG
            self.top.configure(bg=self.C_BG)
        try:
            self.top.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        self.apply_top()

        try:
            families = set(tkfont.families())
        except Exception:
            families = set()
        disp_family, disp_weight = _resolve_font(families, FONT_DISPLAY)
        text_family, text_weight = _resolve_font(families, FONT_TEXT)
        mono_family, _mono_weight = _resolve_font(families, FONT_MONO)
        s = self.scale

        def px(size: float) -> int:
            return -max(9, int(round(size * s)))

        def display(size: float):
            return tkfont.Font(family=disp_family, size=px(size), weight=disp_weight)

        def text(size: float):
            return tkfont.Font(family=text_family, size=px(size), weight=text_weight)

        def mono(size: float, weight: str = "normal"):
            return tkfont.Font(family=mono_family, size=px(size), weight=weight)

        self.font_family = disp_family
        self.f_title = display(17)
        self.f_hero = display(34)
        self.f_hero_unit = text(14)
        self.f_stat = display(23)
        self.f_stat_unit = text(12)
        self.f_caption = display(10)
        self.f_tab = display(11)
        self.f_apps_title = display(15)
        self.f_app = display(13)
        self.f_label = text(12)
        self.f_label_b = display(12)
        self.f_num = text(12)
        self.f_small = text(10)
        self.f_mono = mono(13)
        self.f_mono_b = mono(13, "bold")
        self.f_mono_s = mono(11, "bold")

        self.canvas = tk.Canvas(
            self.top,
            width=self.width,
            height=self.height,
            highlightthickness=0,
            bd=0,
            bg=canvas_bg,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.configure(cursor="arrow")
        self.icons = AppIconProvider(self.top, state, self._S(22))

        self.top.bind("<MouseWheel>", self._on_mousewheel)
        self.top.bind("<Button-4>", self._on_mousewheel)
        self.top.bind("<Button-5>", self._on_mousewheel)
        self.top.bind("<Escape>", self._escape)

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Leave>", self._on_leave)

        self.top.withdraw()

    def _scale(self) -> float:
        try:
            dpi = float(self.top.winfo_fpixels("1i"))
        except Exception:
            dpi = 96.0
        s = min(2.0, max(1.0, dpi / 96.0))
        try:
            fit = min(
                (self.top.winfo_screenwidth() - 40) / self.BASE_W,
                (self.top.winfo_screenheight() - 80) / self.BASE_H,
            )
            s = min(s, fit)
        except Exception:
            pass
        return max(0.7, s)

    def _S(self, value: float) -> int:
        return int(round(value * self.scale))

    def _center_on_screen(self) -> None:
        try:
            screen_w = self.top.winfo_screenwidth()
            screen_h = self.top.winfo_screenheight()
            x = max(0, (screen_w - self.width) // 2)
            y = max(0, (screen_h - self.height) // 2)
            self.top.geometry(f"{self.width}x{self.height}+{x}+{y}")
        except Exception:
            self.top.geometry(f"{self.width}x{self.height}+100+100")

    def apply_visibility(self) -> None:
        if self.state.show_perf_window:
            self.top.deiconify()
            self.apply_top()
            self.redraw(force=True)
            try:
                self.top.lift()
                self.top.focus_force()
            except Exception:
                pass
        else:
            self.top.withdraw()

    def apply_top(self) -> None:
        try:
            self.top.attributes("-topmost", bool(self.state.always_on_top))
        except Exception:
            pass

    def _month_bounds(self, data_days):
        today = datetime.date.today()
        earliest = min(data_days) if data_days else today.isoformat()
        return (int(earliest[:4]), int(earliest[5:7])), (today.year, today.month)

    def _escape(self, _event=None) -> None:
        if self.cal_open:
            self.cal_open = False
            self.redraw(force=True)
        else:
            self._hide()

    def _hide(self) -> None:
        self.cal_open = False
        self.state.show_perf_window = False
        self.apply_visibility()

    def _hit_at(self, x, y):
        for x1, y1, x2, y2, key in self._hits:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _schedule(self) -> None:
        if self._pending:
            return
        self._pending = True
        try:
            self.top.after(33, self._flush)
        except Exception:
            self._pending = False

    def _flush(self) -> None:
        self._pending = False
        self.redraw()

    def _on_hover(self, event) -> None:
        self._mouse = (event.x, event.y)
        key = self._hit_at(event.x, event.y)
        self._hover = key
        cursor = "hand2" if key and key != "cal:blank" else "arrow"
        if cursor != self._cursor:
            self._cursor = cursor
            try:
                self.canvas.configure(cursor=cursor)
            except Exception:
                pass
        self._schedule()

    def _on_leave(self, _event) -> None:
        self._mouse = None
        self._hover = None
        self._schedule()

    def _on_mousewheel(self, event) -> None:
        if self.max_scroll <= 0:
            return
        num = getattr(event, "num", 0)
        if num == 4:
            delta = 1.0
        elif num == 5:
            delta = -1.0
        else:
            delta = float(getattr(event, "delta", 0)) / 120.0
        self._wheel_acc += delta
        steps = int(self._wheel_acc)
        if steps == 0:
            return
        self._wheel_acc -= steps
        self.scroll_row = max(0, min(self.max_scroll, self.scroll_row - steps))
        self.redraw()

    def _press(self, event) -> None:
        key = self._hit_at(event.x, event.y)
        if key:
            self._pressed = key
            self._drag = None
            return
        self._pressed = None
        self._drag = (event.x, event.y)
        self._moved = False

    def _motion(self, event) -> None:
        if self._drag is None:
            return
        if abs(event.x - self._drag[0]) + abs(event.y - self._drag[1]) > 3:
            self._moved = True
        x = self.top.winfo_x() + event.x - self._drag[0]
        y = self.top.winfo_y() + event.y - self._drag[1]
        self.top.geometry(f"+{x}+{y}")

    def _release(self, event) -> None:
        key, self._pressed = self._pressed, None
        if key is not None:
            if self._hit_at(event.x, event.y) == key:
                self._activate(key)
            return
        if self._moved:
            self.state.perf_x = self.top.winfo_x()
            self.state.perf_y = self.top.winfo_y()
            self.state.save()
        elif self.cal_open:
            self.cal_open = False
            self.redraw(force=True)
        self._drag = None

    def _activate(self, key: str) -> None:
        if key == "close":
            self._hide()
        elif key.startswith("tab:"):
            tab = key[4:]
            self.cal_open = False
            if tab != self.active_tab:
                self.active_tab = tab
                self.scroll_row = 0
                self._usage_cache = None
            self.redraw(force=True)
        elif key == "cal:toggle":
            self.cal_open = not self.cal_open
            if self.cal_open:
                base = self.sel_date or datetime.date.today()
                self.cal_month = (base.year, base.month)
            self.redraw(force=True)
        elif key in ("cal:prev", "cal:next"):
            year, month = self.cal_month
            first, last = self._month_bounds(self.state.usage_dates())
            if key == "cal:prev" and (year, month) <= first:
                return
            if key == "cal:next" and (year, month) >= last:
                return
            month += -1 if key == "cal:prev" else 1
            if month < 1:
                year, month = year - 1, 12
            elif month > 12:
                year, month = year + 1, 1
            self.cal_month = (year, month)
            self.redraw(force=True)
        elif key.startswith("cal:day:"):
            try:
                picked = datetime.date.fromisoformat(key[8:])
            except ValueError:
                return
            self.sel_date = picked
            self.active_tab = "date"
            self.cal_open = False
            self.scroll_row = 0
            self._usage_cache = None
            self.redraw(force=True)

    def redraw(self, force: bool = False) -> None:
        if not self.state.show_perf_window:
            return
        try:
            self._draw(force)
        except Exception:
            try:
                traceback.print_exc()
            except Exception:
                pass

    def _period_usage(self, force: bool):
        key = (self.active_tab, self.sel_date)
        now = time.monotonic()
        cached = self._usage_cache
        if not force and cached and cached[0] == key and now - cached[1] < 0.5:
            return cached[2]
        result = self.state.get_period_usage(self.active_tab, self.sel_date)
        self._usage_cache = (key, now, result)
        return result

    def _draw(self, force: bool) -> None:
        c = self.canvas
        S = self._S
        w, h = self.width, self.height
        c.delete("all")
        self._hits = []

        snap = self.state.snapshot()
        usage = self._period_usage(force)

        pad = S(24)
        _round_rect(c, 1, 1, w - 1, h - 1, S(18), fill=self.C_BG, outline=self.C_BORDER)

        self._draw_header(snap, pad)

        top = S(72)
        row_h = S(312)
        gap = S(16)
        live_right = pad + S(756)
        self._draw_live_card(snap, pad, top, live_right, top + row_h)
        self._draw_usage_card(snap, usage, live_right + gap, top, w - pad, top + row_h)
        self._draw_apps_card(usage, pad, top + row_h + gap, w - pad, h - pad)
        self._draw_calendar()

    def _card(self, x1, y1, x2, y2, fill=None, outline=None, radius=14) -> None:
        _round_rect(
            self.canvas, x1, y1, x2, y2, self._S(radius),
            fill=fill or self.C_SURFACE, outline=outline or self.C_BORDER,
        )

    def _num_unit(self, x, y_bottom, num, unit, f_num, f_unit, color, unit_color) -> float:
        c = self.canvas
        c.create_text(x, y_bottom, text=num, fill=color, font=f_num, anchor="sw")
        end = x + f_num.measure(num)
        if unit:
            dn = f_num.metrics("descent")
            du = f_unit.metrics("descent")
            ux = end + self._S(6)
            c.create_text(ux, y_bottom - dn + du, text=unit, fill=unit_color, font=f_unit, anchor="sw")
            end = ux + f_unit.measure(unit)
        return end

    def _area(self, pts, top_y, base_y, color, a_top, a_bottom, bands=12) -> None:
        if len(pts) < 2 or base_y <= top_y:
            return
        c = self.canvas
        ys = [int(round(top_y + (base_y - top_y) * i / bands)) for i in range(bands + 1)]
        min_y = min(p[1] for p in pts)
        for k in range(bands):
            y0, y1 = ys[k], ys[k + 1]
            if y1 <= y0 or min_y >= y1:
                continue
            alpha = a_top + (a_bottom - a_top) * ((k + 0.5) / bands)
            col = _mix(self.C_SURFACE, color, alpha)
            top_edge = [(x, min(max(y, y0), y1)) for x, y in pts]
            coords = []
            for x, y in top_edge:
                coords.extend((x, y))
            coords.extend((pts[-1][0], y1, pts[0][0], y1))
            c.create_polygon(coords, fill=col, outline="")

    def _draw_header(self, snap, pad) -> None:
        c = self.canvas
        S = self._S
        w = self.width

        lx, ly, ls = pad, S(15), S(34)
        _round_rect(
            c, lx, ly, lx + ls, ly + ls, S(10),
            fill=_mix(self.C_BG, self.C_ACCENT, 0.16),
            outline=_mix(self.C_BG, self.C_ACCENT, 0.38),
        )
        cx, cy = lx + ls / 2, ly + ls / 2
        c.create_line(cx - S(5), cy - S(8), cx - S(5), cy + S(3), fill=self.C_DOWN, width=max(2, S(2)))
        c.create_polygon(
            cx - S(9), cy + S(1), cx - S(1), cy + S(1), cx - S(5), cy + S(7),
            fill=self.C_DOWN, outline="",
        )
        c.create_line(cx + S(5), cy + S(8), cx + S(5), cy - S(3), fill=self.C_UP, width=max(2, S(2)))
        c.create_polygon(
            cx + S(1), cy - S(1), cx + S(9), cy - S(1), cx + S(5), cy - S(7),
            fill=self.C_UP, outline="",
        )

        tx = lx + ls + S(14)
        c.create_text(tx, S(24), text="Network Performance", fill=self.C_TEXT, font=self.f_title, anchor="w")

        adapter = snap.get("adapter")
        if adapter is not None and snap.get("missing"):
            sub, sub_col = f"{adapter} (offline)", self.C_UP
        else:
            sub, sub_col = "Live traffic & usage history", self.C_TEXT_3
        sub = _fit_text(self.f_small, sub, S(300))
        c.create_text(tx, S(43), text=sub, fill=sub_col, font=self.f_small, anchor="w")

        by1, bs = S(15), S(34)
        bx2 = w - pad
        bx1 = bx2 - bs
        hot = self._hover == "close"
        _round_rect(
            c, bx1, by1, bx2, by1 + bs, S(10),
            fill=self.C_DANGER if hot else self.C_SURFACE,
            outline=self.C_DANGER if hot else self.C_BORDER,
        )
        mx, my, r = (bx1 + bx2) / 2, by1 + bs / 2, S(5)
        xc = "#FFFFFF" if hot else self.C_TEXT_2
        c.create_line(mx - r, my - r, mx + r, my + r, fill=xc, width=max(2, S(2)), capstyle="round")
        c.create_line(mx - r, my + r, mx + r, my - r, fill=xc, width=max(2, S(2)), capstyle="round")
        self._hits.append((bx1, by1, bx2, by1 + bs, "close"))

        seg_h, inner = S(34), S(4)
        segments = [(key, label, S(88)) for key, label in self.PERIODS] + [("date", "", S(136))]
        total_w = sum(width for _k, _l, width in segments) + 2 * inner
        sx2 = bx1 - S(16)
        sx1 = sx2 - total_w
        sy1 = S(15)
        _round_rect(c, sx1, sy1, sx2, sy1 + seg_h, S(10), fill=self.C_BG, outline=self.C_BORDER)
        cursor_x = sx1 + inner
        for key, label, seg_w in segments:
            x1 = cursor_x
            x2 = x1 + seg_w
            cursor_x = x2
            y1 = sy1 + inner
            y2 = sy1 + seg_h - inner
            is_date = key == "date"
            hit_key = "cal:toggle" if is_date else f"tab:{key}"
            active = self.active_tab == key
            hovered = self._hover == hit_key or (is_date and self.cal_open)
            if active:
                _round_rect(
                    c, x1, y1, x2, y2, S(7),
                    fill=_mix(self.C_SURFACE, self.C_ACCENT, 0.32),
                    outline=_mix(self.C_SURFACE, self.C_ACCENT, 0.55),
                )
                col = "#FFFFFF"
            else:
                if hovered:
                    _round_rect(c, x1, y1, x2, y2, S(7), fill=self.C_SURFACE_2, outline="")
                col = self.C_TEXT if hovered else self.C_TEXT_2
            mid_y = (y1 + y2) / 2
            if is_date:
                if self.active_tab == "date" and self.sel_date is not None:
                    d = self.sel_date
                    label = f"{d.day} {d.strftime('%b %Y')}"
                else:
                    label = "Pick date"
                icon = S(13)
                gap = S(8)
                group = icon + gap + self.f_tab.measure(label)
                gx = (x1 + x2) / 2 - group / 2
                self._calendar_icon(gx + icon / 2, mid_y, icon, col)
                c.create_text(gx + icon + gap, mid_y, text=label, fill=col, font=self.f_tab, anchor="w")
            else:
                c.create_text((x1 + x2) / 2, mid_y, text=label, fill=col, font=self.f_tab, anchor="center")
            self._hits.append((x1, y1, x2, y2, hit_key))
        self._cal_anchor = (sx2, sy1 + seg_h + S(8))

    def _calendar_icon(self, cx, cy, size, color) -> None:
        c = self.canvas
        S = self._S
        half = size / 2
        width = max(1, S(1))
        _round_rect(c, cx - half, cy - half + S(2), cx + half, cy + half, S(3), fill="", outline=color, width=width)
        c.create_line(cx - half, cy - half + S(6), cx + half, cy - half + S(6), fill=color, width=width)
        for off in (S(3.5), size - S(3.5)):
            x = cx - half + off
            c.create_line(x, cy - half, x, cy - half + S(4), fill=color, width=width, capstyle="round")

    def _draw_calendar(self) -> None:
        anchor = self._cal_anchor
        if not self.cal_open or anchor is None:
            return
        c = self.canvas
        S = self._S
        today = datetime.date.today()
        year, month = self.cal_month
        weeks = calendar.Calendar(0).monthdayscalendar(year, month)
        data_days = self.state.usage_dates()
        first, last = self._month_bounds(data_days)

        cell = S(36)
        pad = S(16)
        head_h = S(52)
        week_h = S(22)
        width = cell * 7 + pad * 2
        height = head_h + week_h + cell * len(weeks) + pad
        x2, y1 = anchor
        x1 = x2 - width
        y2 = y1 + height

        _round_rect(c, x1 + S(3), y1 + S(6), x2 + S(3), y2 + S(6), S(14), fill="#05070A", outline="")
        _round_rect(c, x1, y1, x2, y2, S(14), fill=self.C_SURFACE, outline=self.C_BORDER_STRONG)

        hits = []
        btn = S(28)
        by = y1 + (head_h - btn) / 2
        can_prev = (year, month) > first
        can_next = (year, month) < last
        for key, bx, enabled, direction in (
            ("cal:prev", x1 + pad, can_prev, -1),
            ("cal:next", x2 - pad - btn, can_next, 1),
        ):
            hot = enabled and self._hover == key
            if hot:
                _round_rect(c, bx, by, bx + btn, by + btn, S(8), fill=self.C_SURFACE_2, outline="")
            col = self.C_TEXT if hot else self.C_TEXT_2 if enabled else self.C_BORDER_STRONG
            mx, my = bx + btn / 2, by + btn / 2
            c.create_line(
                mx - direction * S(3), my - S(5), mx + direction * S(3), my, mx - direction * S(3), my + S(5),
                fill=col, width=max(2, S(2)), capstyle="round", joinstyle="round",
            )
            if enabled:
                hits.append((bx, by, bx + btn, by + btn, key))

        title = datetime.date(year, month, 1).strftime("%B %Y")
        c.create_text((x1 + x2) / 2, y1 + head_h / 2, text=title, fill=self.C_TEXT, font=self.f_label_b, anchor="center")

        wy = y1 + head_h + week_h / 2
        for i, name in enumerate(self.WEEKDAYS):
            c.create_text(x1 + pad + cell * i + cell / 2, wy, text=name, fill=self.C_TEXT_3, font=self.f_small, anchor="center")

        radius = S(15)
        grid_top = y1 + head_h + week_h
        for r, week in enumerate(weeks):
            for i, day in enumerate(week):
                if day == 0:
                    continue
                d = datetime.date(year, month, day)
                iso = d.isoformat()
                cx = x1 + pad + cell * i + cell / 2
                cy = grid_top + cell * r + cell / 2
                future = d > today
                has_data = iso in data_days
                selected = self.active_tab == "date" and self.sel_date == d
                hovered = not future and self._hover == f"cal:day:{iso}"
                if selected:
                    c.create_oval(
                        cx - radius, cy - radius, cx + radius, cy + radius,
                        fill=_mix(self.C_SURFACE, self.C_ACCENT, 0.45),
                        outline=_mix(self.C_SURFACE, self.C_ACCENT, 0.7),
                    )
                    col = "#FFFFFF"
                else:
                    if hovered:
                        c.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill=self.C_RAISED, outline="")
                    elif d == today:
                        c.create_oval(
                            cx - radius, cy - radius, cx + radius, cy + radius,
                            fill="", outline=_mix(self.C_SURFACE, self.C_ACCENT, 0.6),
                        )
                    if future:
                        col = self.C_BORDER_STRONG
                    elif has_data:
                        col = self.C_TEXT
                    else:
                        col = self.C_TEXT_3
                c.create_text(cx, cy - S(1), text=str(day), fill=col, font=self.f_num, anchor="center")
                if has_data and not selected:
                    dot = S(2)
                    c.create_oval(cx - dot, cy + S(9) - dot, cx + dot, cy + S(9) + dot, fill=self.C_DOWN, outline="")
                if not future:
                    hits.append((cx - cell / 2, cy - cell / 2, cx + cell / 2, cy + cell / 2, f"cal:day:{iso}"))

        hits.append((x1, y1, x2, y2, "cal:blank"))
        self._hits = hits + self._hits

    def _draw_live_card(self, snap, x1, y1, x2, y2) -> None:
        c = self.canvas
        S = self._S
        self._card(x1, y1, x2, y2)
        pad = S(24)
        ix = x1 + pad
        unit = snap["unit"]
        hist = snap["history"]

        cap_y = y1 + S(28)
        c.create_text(ix, cap_y, text="LIVE TRAFFIC", fill=self.C_TEXT_2, font=self.f_caption, anchor="w")
        chip_x = ix + self.f_caption.measure("LIVE TRAFFIC") + S(12)
        chip_w, chip_h = S(58), S(20)
        _round_rect(
            c, chip_x, cap_y - chip_h / 2, chip_x + chip_w, cap_y + chip_h / 2, S(10),
            fill=_mix(self.C_SURFACE, self.C_DOWN, 0.12),
            outline=_mix(self.C_SURFACE, self.C_DOWN, 0.30),
        )
        phase = (time.time() % 1.6) / 1.6
        dcx = chip_x + S(13)
        halo = S(3) + S(4) * phase
        c.create_oval(
            dcx - halo, cap_y - halo, dcx + halo, cap_y + halo,
            fill=_mix(self.C_SURFACE, self.C_DOWN, 0.32 * (1 - phase)), outline="",
        )
        c.create_oval(dcx - S(3), cap_y - S(3), dcx + S(3), cap_y + S(3), fill=self.C_DOWN, outline="")
        c.create_text(chip_x + S(24), cap_y, text="LIVE", fill=self.C_DOWN, font=self.f_caption, anchor="w")

        peak = max((max(d, u) for d, u in hist), default=0.0)
        pk_val = format_speed(peak, unit)
        wl = self.f_small.measure("Peak")
        wv = self.f_label_b.measure(pk_val)
        cw = S(14) + wl + S(8) + wv + S(14)
        cx2 = x2 - pad
        cx1 = cx2 - cw
        _round_rect(
            c, cx1, cap_y - S(12), cx2, cap_y + S(12), S(12),
            fill=self.C_SURFACE_2, outline=self.C_BORDER,
        )
        c.create_text(cx1 + S(14), cap_y, text="Peak", fill=self.C_TEXT_3, font=self.f_small, anchor="w")
        c.create_text(cx2 - S(14), cap_y, text=pk_val, fill=self.C_TEXT, font=self.f_label_b, anchor="e")

        conn = snap.get("connection") or ""
        if conn:
            rx = x2 - pad
            conn = _fit_text(self.f_label, conn, S(320))
            c.create_text(rx, y1 + S(68), text=conn, fill=self.C_TEXT_2, font=self.f_label, anchor="e")
            ip = snap.get("ip") or ""
            if ip:
                c.create_text(rx, y1 + S(92), text=ip, fill=self.C_TEXT, font=self.f_mono_b, anchor="e")

        for label, value, color, bx in (
            ("Download", snap["down"], self.C_DOWN, ix),
            ("Upload", snap["up"], self.C_UP, ix + S(250)),
        ):
            ly = y1 + S(62)
            c.create_oval(bx, ly - S(4), bx + S(8), ly + S(4), fill=color, outline="")
            c.create_text(bx + S(16), ly, text=label, fill=self.C_TEXT_2, font=self.f_label, anchor="w")
            num, u = speed_parts(value, unit)
            self._num_unit(bx, y1 + S(108), num, u, self.f_hero, self.f_hero_unit, self.C_TEXT, self.C_TEXT_2)

        gx = x1 + S(24 + 68)
        gw = (x2 - S(24)) - gx
        gy = y1 + S(128)
        gh = (y2 - S(42)) - gy

        nice = _nice_ceiling(peak, unit)

        for i in range(5):
            yy = gy + gh * i / 4
            if i == 4:
                c.create_line(gx, yy, gx + gw, yy, fill=self.C_BORDER_STRONG)
            else:
                c.create_line(gx, yy, gx + gw, yy, fill=self.C_BORDER, dash=(2, 5))
        c.create_text(gx - S(12), gy, text=format_speed(nice, unit), fill=self.C_TEXT_3, font=self.f_small, anchor="e")
        c.create_text(gx - S(12), gy + gh / 2, text=format_speed(nice / 2, unit), fill=self.C_TEXT_3, font=self.f_small, anchor="e")
        c.create_text(gx - S(12), gy + gh, text="0", fill=self.C_TEXT_3, font=self.f_small, anchor="e")

        maxlen = getattr(getattr(self.state, "history", None), "maxlen", None) or 48
        cap = max(2, len(hist), int(maxlen))
        interval = float(getattr(self.state, "interval", 1.0) or 1.0)
        span = int(round((cap - 1) * interval))
        c.create_text(gx, gy + gh + S(18), text=f"{span}s ago", fill=self.C_TEXT_3, font=self.f_small, anchor="w")
        c.create_text(gx + gw, gy + gh + S(18), text="now", fill=self.C_TEXT_3, font=self.f_small, anchor="e")

        n = len(hist)
        if n < 2:
            c.create_text(
                gx + gw / 2, gy + gh / 2, text="Collecting data…",
                fill=self.C_TEXT_3, font=self.f_label, anchor="center",
            )
            return

        step_x = gw / (cap - 1)
        xs = [gx + gw - (n - 1 - i) * step_x for i in range(n)]

        def curve(idx):
            raw = [
                (xs[i], gy + gh - min(max(float(hist[i][idx]), 0.0) / nice, 1.0) * gh)
                for i in range(n)
            ]
            return [(x, min(max(y, gy), gy + gh)) for x, y in _catmull(raw, 4)]

        down_curve = curve(0)
        up_curve = curve(1)

        self._area(up_curve, gy, gy + gh, self.C_UP, 0.16, 0.0)
        self._area(down_curve, gy, gy + gh, self.C_DOWN, 0.30, 0.0)

        for pts, color in ((up_curve, self.C_UP), (down_curve, self.C_DOWN)):
            flat = [v for p in pts for v in p]
            c.create_line(
                *flat, fill=_mix(self.C_SURFACE, color, 0.22),
                width=max(5, S(6)), capstyle="round", joinstyle="round",
            )
            c.create_line(
                *flat, fill=color, width=max(2, S(2.2)),
                capstyle="round", joinstyle="round",
            )

        for pts, color in ((up_curve, self.C_UP), (down_curve, self.C_DOWN)):
            ex, ey = pts[-1]
            r = S(4.5)
            c.create_oval(ex - r, ey - r, ex + r, ey + r, fill=color, outline=self.C_SURFACE, width=max(2, S(2)))

        self._draw_chart_tooltip(hist, xs, step_x, gx, gy, gw, gh, nice, unit, interval)

    def _draw_chart_tooltip(self, hist, xs, step_x, gx, gy, gw, gh, nice, unit, interval) -> None:
        if not self._mouse:
            return
        c = self.canvas
        S = self._S
        mx, my = self._mouse
        n = len(hist)
        if not (xs[0] - step_x <= mx <= gx + gw + S(6) and gy - S(10) <= my <= gy + gh + S(10)):
            return
        idx = int(round((mx - (gx + gw)) / step_x)) + (n - 1)
        idx = max(0, min(n - 1, idx))
        px = xs[idx]
        down, up = float(hist[idx][0]), float(hist[idx][1])

        c.create_line(px, gy, px, gy + gh, fill=self.C_BORDER_STRONG, dash=(3, 3))
        for val, color in ((up, self.C_UP), (down, self.C_DOWN)):
            py = gy + gh - min(max(val, 0.0) / nice, 1.0) * gh
            r = S(4)
            c.create_oval(px - r, py - r, px + r, py + r, fill=color, outline=self.C_SURFACE, width=max(2, S(2)))

        age = (n - 1 - idx) * interval
        when = "now" if age <= 0 else f"{age:g}s ago"
        rows = (
            (None, when),
            (self.C_DOWN, format_speed(down, unit)),
            (self.C_UP, format_speed(up, unit)),
        )
        text_w = max(
            self.f_small.measure(when),
            self.f_label_b.measure(rows[1][1]) + S(16),
            self.f_label_b.measure(rows[2][1]) + S(16),
        )
        bw = text_w + S(28)
        bh = S(74)
        bx = px + S(14)
        if bx + bw > gx + gw + S(10):
            bx = px - S(14) - bw
        by = gy + S(4)
        _round_rect(c, bx, by, bx + bw, by + bh, S(10), fill=self.C_RAISED, outline=self.C_BORDER_STRONG)
        c.create_text(bx + S(14), by + S(15), text=when, fill=self.C_TEXT_3, font=self.f_small, anchor="w")
        for i, (color, label) in enumerate(rows[1:]):
            ry = by + S(36) + i * S(20)
            c.create_oval(bx + S(14), ry - S(3), bx + S(20), ry + S(3), fill=color, outline="")
            c.create_text(bx + S(30), ry, text=label, fill=self.C_TEXT, font=self.f_label_b, anchor="w")

    def _draw_usage_card(self, snap, usage, x1, y1, x2, y2) -> None:
        c = self.canvas
        S = self._S
        self._card(x1, y1, x2, y2)
        tot_down, tot_up, _apps = usage
        tot_down = int(tot_down)
        tot_up = int(tot_up)
        total = tot_down + tot_up
        pad = S(24)
        ix0, ix1 = x1 + pad, x2 - pad
        iw = ix1 - ix0

        cap_y = y1 + S(28)
        today = datetime.date.today()
        picked = self.sel_date if self.active_tab == "date" else None

        def short(d, with_year=False):
            base = f"{d.day} {d.strftime('%b')}"
            return f"{base} {d.year}" if with_year else base

        if picked is not None:
            title = short(picked, True)
            rng = picked.strftime("%A")
        else:
            title = self.PERIOD_TITLE.get(self.active_tab, "Today")
            days = self.state.PERIOD_DAYS.get(self.active_tab, 1)
            if days == 1:
                rng = short(today)
            else:
                start = today - datetime.timedelta(days=days - 1)
                with_year = start.year != today.year
                rng = f"{short(start, with_year)} – {short(today, with_year)}"
        caption = f"USAGE · {title.upper()}"
        c.create_text(ix0, cap_y, text=caption, fill=self.C_TEXT_2, font=self.f_caption, anchor="w")
        rng = _fit_text(self.f_small, rng, iw - self.f_caption.measure(caption) - S(16))
        c.create_text(ix1, cap_y, text=rng, fill=self.C_TEXT_3, font=self.f_small, anchor="e")

        num, unit = format_bytes(total).split(" ", 1)
        c.create_text(ix0, y1 + S(62), text="Bandwidth used", fill=self.C_TEXT_2, font=self.f_label, anchor="w")
        self._num_unit(ix0, y1 + S(112), num, unit, self.f_hero, self.f_hero_unit, self.C_TEXT, self.C_TEXT_2)

        bar_y, bar_h = y1 + S(128), S(10)
        _round_rect(c, ix0, bar_y, ix1, bar_y + bar_h, S(5), fill=self.C_TRACK, outline="")
        if total > 0:
            _round_rect(c, ix0, bar_y, ix1, bar_y + bar_h, S(5), fill=self.C_UP, outline="")
            dw = max(S(10), iw * tot_down / total) if tot_down > 0 else 0
            if tot_up == 0:
                dw = iw
            if dw > 0:
                _round_rect(c, ix0, bar_y, ix0 + min(dw, iw), bar_y + bar_h, S(5), fill=self.C_DOWN, outline="")

        ty, th = y1 + S(156), S(84)
        tgap = S(12)
        tw = (iw - tgap) / 2
        for i, (label, value, color) in enumerate(
            (("Download", tot_down, self.C_DOWN), ("Upload", tot_up, self.C_UP))
        ):
            tx = ix0 + i * (tw + tgap)
            _round_rect(c, tx, ty, tx + tw, ty + th, S(10), fill=self.C_SURFACE_2, outline=self.C_BORDER)
            c.create_oval(tx + S(14), ty + S(17), tx + S(22), ty + S(25), fill=color, outline="")
            c.create_text(tx + S(30), ty + S(21), text=label, fill=self.C_TEXT_2, font=self.f_label, anchor="w")
            pct = int(round(100.0 * value / total)) if total else 0
            c.create_text(tx + tw - S(14), ty + S(21), text=f"{pct}%", fill=self.C_TEXT_3, font=self.f_small, anchor="e")
            n2, u2 = format_bytes(value).split(" ", 1)
            self._num_unit(tx + S(14), ty + th - S(14), n2, u2, self.f_stat, self.f_stat_unit, self.C_TEXT, self.C_TEXT_2)

        div_y = y1 + S(258)
        c.create_line(ix0, div_y, ix1, div_y, fill=self.C_BORDER)
        sy = y1 + S(282)
        c.create_text(ix0, sy, text="This session", fill=self.C_TEXT_2, font=self.f_label, anchor="w")
        cursor = ix1
        for value, color in (
            (snap.get("session_up", 0) or 0, self.C_UP),
            (snap.get("session_down", 0) or 0, self.C_DOWN),
        ):
            txt = format_bytes(value)
            c.create_text(cursor, sy, text=txt, fill=self.C_TEXT, font=self.f_label_b, anchor="e")
            dx = cursor - self.f_label_b.measure(txt) - S(12)
            c.create_oval(dx - S(3), sy - S(3), dx + S(3), sy + S(3), fill=color, outline="")
            cursor = dx - S(22)

    def _draw_apps_card(self, usage, x1, y1, x2, y2) -> None:
        c = self.canvas
        S = self._S
        self._card(x1, y1, x2, y2, radius=16)
        tot_down, tot_up, apps = usage
        tot_down = int(tot_down)
        tot_up = int(tot_up)
        pad = S(22)
        inner_l = x1 + pad

        title_y = y1 + S(26)
        c.create_text(inner_l, title_y, text="Applications", fill=self.C_TEXT, font=self.f_apps_title, anchor="w")
        count_x = inner_l + self.f_apps_title.measure("Applications") + S(10)
        c.create_text(count_x, title_y + S(1), text=str(len(apps)), fill=self.C_TEXT_3, font=self.f_mono_s, anchor="w")

        sum_y = y1 + S(56)
        total_txt = format_bytes(tot_down + tot_up)
        c.create_text(inner_l, sum_y, text=total_txt, fill=self.C_TEXT, font=self.f_mono_b, anchor="w")
        acc = inner_l + self.f_mono_b.measure(total_txt) + S(16)
        down_txt = "↓  " + format_bytes(tot_down)
        c.create_text(acc, sum_y, text=down_txt, fill=self.C_DOWN, font=self.f_mono, anchor="w")
        acc += self.f_mono.measure(down_txt) + S(14)
        c.create_text(acc, sum_y, text="↑  " + format_bytes(tot_up), fill=self.C_UP, font=self.f_mono, anchor="w")

        head_y = y1 + S(88)
        list_top = head_y + S(20)
        list_bottom = y2 - S(14)
        item_h = S(40)
        rows_visible = max(1, int((list_bottom - list_top) // item_h))
        self.max_scroll = max(0, len(apps) - rows_visible)
        self.scroll_row = max(0, min(self.scroll_row, self.max_scroll))
        scrolling = self.max_scroll > 0

        inner_r = x2 - pad - (S(12) if scrolling else 0)
        up_r = inner_r
        down_r = up_r - S(116)
        icon_size = S(22)
        icon_cx = inner_l + S(36) + icon_size / 2
        name_x = inner_l + S(36) + icon_size + S(10)
        name_w = max(S(120), min(S(280), int((inner_r - inner_l) * 0.30)))
        bar_l = name_x + name_w + S(12)
        bar_r = down_r - S(124)
        bar_w = bar_r - bar_l

        c.create_text(name_x, head_y, text="APP", fill=self.C_TEXT_3, font=self.f_caption, anchor="w")
        if bar_w > S(24):
            c.create_text(bar_l, head_y, text="SHARE", fill=self.C_TEXT_3, font=self.f_caption, anchor="w")
        c.create_text(down_r, head_y, text="DOWN", fill=self.C_TEXT_3, font=self.f_caption, anchor="e")
        c.create_text(up_r, head_y, text="UP", fill=self.C_TEXT_3, font=self.f_caption, anchor="e")
        c.create_line(inner_l, head_y + S(12), inner_r, head_y + S(12), fill=self.C_BORDER)

        if not apps:
            cx = (x1 + x2) / 2
            cy = (list_top + list_bottom) / 2
            c.create_text(cx, cy - S(8), text="No traffic in this period", fill=self.C_TEXT_2, font=self.f_label_b, anchor="center")
            c.create_text(cx, cy + S(14), text="Usage shows up after something transfers.", fill=self.C_TEXT_3, font=self.f_small, anchor="center")
            return

        max_total = max(1, apps[0][3])
        first = self.scroll_row
        last = min(len(apps), first + rows_visible)
        mouse = self._mouse
        for i in range(first, last):
            name, d_bytes, u_bytes, t_bytes = apps[i]
            ry = list_top + (i - first) * item_h
            cy = ry + item_h / 2

            if mouse and ry <= mouse[1] < ry + item_h and x1 <= mouse[0] <= x2:
                _round_rect(
                    c, x1 + S(10), ry + S(2), x2 - S(10), ry + item_h - S(2), S(8),
                    fill=self.C_SURFACE_2, outline="",
                )

            c.create_text(inner_l + S(2), cy, text=f"{i + 1:02d}", fill=self.C_TEXT_3, font=self.f_mono_s, anchor="w")

            photo = self.icons.get(name)
            if photo is not None:
                c.create_image(icon_cx, cy, image=photo, anchor="center")
            else:
                hue = "#94A3B8" if name.lower().startswith("system") else self.AVATAR_HUES[
                    zlib.crc32(name.lower().encode("utf-8", "ignore")) % len(self.AVATAR_HUES)
                ]
                half = icon_size / 2
                _round_rect(
                    c, icon_cx - half, cy - half, icon_cx + half, cy + half, S(7),
                    fill=_mix(self.C_SURFACE, hue, 0.16), outline=_mix(self.C_SURFACE, hue, 0.42),
                )
                letter = next((ch for ch in name if ch.isalnum()), "•").upper()
                c.create_text(icon_cx, cy, text=letter, fill=hue, font=self.f_caption, anchor="center")

            c.create_text(
                name_x, cy, text=_fit_text(self.f_app, name, name_w),
                fill=self.C_TEXT, font=self.f_app, anchor="w",
            )

            if bar_w > S(24):
                bh = S(4)
                by = cy - bh / 2
                _round_rect(c, bar_l, by, bar_r, by + bh, S(2), fill=self.C_BAR_TRACK, outline="")
                fw = bar_w * (t_bytes / max_total)
                if fw > S(2) and t_bytes > 0:
                    mint_w = fw * (d_bytes / t_bytes)
                    _round_rect(c, bar_l, by, bar_l + fw, by + bh, S(2), fill=self.C_UP, outline="")
                    if mint_w > 0:
                        mint_w = max(mint_w, S(2))
                        _round_rect(c, bar_l, by, bar_l + mint_w, by + bh, S(2), fill=self.C_DOWN, outline="")
                        if fw - mint_w > S(1):
                            patch = min(S(2), mint_w)
                            c.create_rectangle(
                                bar_l + mint_w - patch, by, bar_l + mint_w, by + bh,
                                fill=self.C_DOWN, outline="",
                            )

            c.create_text(down_r, cy, text=format_bytes(d_bytes), fill=self.C_DOWN, font=self.f_mono, anchor="e")
            c.create_text(up_r, cy, text=format_bytes(u_bytes), fill=self.C_UP, font=self.f_mono, anchor="e")

        if scrolling:
            track_x2 = x2 - S(10)
            track_x1 = track_x2 - S(5)
            track_h = rows_visible * item_h
            _round_rect(c, track_x1, list_top, track_x2, list_top + track_h, S(2), fill=self.C_SCROLL_TRACK, outline="")
            thumb_h = max(S(28), track_h * rows_visible / len(apps))
            thumb_y = list_top + (track_h - thumb_h) * (self.scroll_row / self.max_scroll)
            _round_rect(c, track_x1, thumb_y, track_x2, thumb_y + thumb_h, S(2), fill=self.C_SCROLL_THUMB, outline="")
