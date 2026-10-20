from __future__ import annotations

import base64
import ctypes
import io
import os
import queue
import threading
from ctypes import wintypes

_SHGFI_ICON = 0x100


class _ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class _BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", wintypes.LPVOID),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


_api = None


def _win32():
    global _api
    if _api is not None:
        return _api
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    shell32.SHGetFileInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(_SHFILEINFOW),
        wintypes.UINT,
        wintypes.UINT,
    ]
    shell32.SHGetFileInfoW.restype = ctypes.c_size_t

    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(_ICONINFO)]
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int

    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetObjectW.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(_BITMAPINFO),
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL

    _api = (user32, gdi32, shell32)
    return _api


def _read_bits(hdc, hbitmap, width: int, height: int):
    _, gdi32, _ = _win32()
    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    buffer = ctypes.create_string_buffer(width * height * 4)
    copied = gdi32.GetDIBits(hdc, hbitmap, 0, height, buffer, ctypes.byref(info), 0)
    if copied == 0:
        return None
    return buffer.raw


def _hicon_to_image(hicon):
    from PIL import Image

    user32, gdi32, _ = _win32()
    info = _ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(info)):
        return None
    hdc = user32.GetDC(None)
    try:
        if not info.hbmColor:
            return None
        bitmap = _BITMAP()
        if not gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bitmap), ctypes.byref(bitmap)):
            return None
        width, height = int(bitmap.bmWidth), int(bitmap.bmHeight)
        if width <= 0 or height <= 0:
            return None
        color = _read_bits(hdc, info.hbmColor, width, height)
        if color is None:
            return None
        image = Image.frombytes("RGBA", (width, height), color, "raw", "BGRA")
        if image.getchannel("A").getextrema()[1] == 0 and info.hbmMask:
            mask = _read_bits(hdc, info.hbmMask, width, height)
            if mask is not None:
                mask_image = Image.frombytes("RGBA", (width, height), mask, "raw", "BGRA").convert("L")
                image.putalpha(mask_image.point(lambda v: 0 if v > 127 else 255))
            else:
                image.putalpha(255)
        return image
    finally:
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)
        user32.ReleaseDC(None, hdc)


def extract_icon(path: str, size: int):
    if os.name != "nt" or not path:
        return None
    from PIL import Image

    user32, _, shell32 = _win32()
    file_info = _SHFILEINFOW()
    found = shell32.SHGetFileInfoW(path, 0, ctypes.byref(file_info), ctypes.sizeof(file_info), _SHGFI_ICON)
    if not found or not file_info.hIcon:
        return None
    try:
        image = _hicon_to_image(file_info.hIcon)
    finally:
        user32.DestroyIcon(file_info.hIcon)
    if image is None:
        return None
    return image.resize((size, size), Image.LANCZOS)


def _icon_png(path: str, size: int) -> bytes:
    try:
        image = extract_icon(path, size)
        if image is None:
            return b""
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return b""


class AppIconProvider:
    def __init__(self, master, state, size: int) -> None:
        import tkinter as tk

        self._tk = tk
        self._master = master
        self._state = state
        self._size = max(8, int(size))
        self._images: dict = {}
        self._failed: dict = {}
        self._queued: set = set()
        self._ready: dict = {}
        self._lock = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()
        self._thread = None

    def get(self, name: str):
        photo = self._images.get(name)
        if photo is not None:
            return photo
        path = self._state.app_path(name)
        if not path:
            return None
        with self._lock:
            result = self._ready.pop(name, None)
        if result is not None:
            self._queued.discard(name)
            data, used_path = result
            if data:
                try:
                    photo = self._tk.PhotoImage(master=self._master, data=base64.b64encode(data))
                except Exception:
                    photo = None
                if photo is not None:
                    self._images[name] = photo
                    return photo
            self._failed[name] = used_path
            return None
        if name in self._queued or self._failed.get(name) == path:
            return None
        self._queued.add(name)
        self._start_worker()
        self._jobs.put((name, path))
        return None

    def _start_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._work, name="nettraffic-icons", daemon=True)
        self._thread.start()

    def _work(self) -> None:
        if os.name == "nt":
            try:
                ctypes.WinDLL("ole32").CoInitializeEx(None, 2)
            except Exception:
                pass
        while True:
            name, path = self._jobs.get()
            data = _icon_png(path, self._size)
            with self._lock:
                self._ready[name] = (data, path)
