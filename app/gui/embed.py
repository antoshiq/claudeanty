"""Встраивание окна браузера (Chromium / Camoufox) во вкладку приложения. Только Windows.

Это тот же браузер с теми же отпечатками: его окно находится по процессу профиля
и «переселяется» внутрь виджета через QWindow.fromWinId + createWindowContainer.
"""
import ctypes
import os
import re
import sys
import time
from ctypes import wintypes as wt
from typing import Optional

import psutil
from PySide6.QtCore import Qt
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

SUPPORTED = sys.platform == "win32"
_CLASSES = ("Chrome_WidgetWin_1", "MozillaWindowClass")

if SUPPORTED:
    user32 = ctypes.windll.user32
    _EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    GWL_STYLE = -16
    WS_CHILD, WS_POPUP, WS_CAPTION, WS_THICKFRAME = 0x40000000, 0x80000000, 0x00C00000, 0x00040000
    WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00010000, 0x00080000
    SWP_FRAMECHANGED, SWP_NOZORDER, SWP_SHOWWINDOW = 0x0020, 0x0004, 0x0040


def profile_pids(user_data_dir: str) -> set[int]:
    """PID всех процессов браузера, запущенных с этой папкой профиля."""
    key = os.path.normcase(os.path.abspath(user_data_dir))
    pattern = re.compile(re.escape(key) + r'(?=$|[\s"\\])')
    out = set()
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = os.path.normcase(" ".join(p.info["cmdline"] or []))
        except Exception:  # noqa: BLE001
            continue
        if pattern.search(cmd):
            out.add(p.info["pid"])
    return out


def find_window(pids: set[int]) -> Optional[int]:
    """Главное видимое окно браузера, принадлежащее одному из pids."""
    found: list[tuple[int, int]] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value in _CLASSES:
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > 200 * 200:
                found.append((area, hwnd))
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return max(found)[1] if found else None


def wait_window(user_data_dir: str, timeout: float = 20.0) -> Optional[int]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = find_window(profile_pids(user_data_dir))
        if hwnd:
            time.sleep(0.4)  # даём окну дорисоваться
            return hwnd
        time.sleep(0.3)
    return None


_detached: list = []  # держим ссылки на открепленные QWindow, чтобы Qt их не уничтожил


class BrowserTab(QWidget):
    """Вкладка с встроенным окном браузера."""

    def __init__(self, profile, hwnd: int):
        super().__init__()
        self.setObjectName("browserTab")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.profile_id = profile.id
        self.mobile = profile.kind == "mobile"
        self.hwnd = hwnd
        self.orig_style = user32.GetWindowLongW(hwnd, GWL_STYLE)

        self.qwin = QWindow.fromWinId(hwnd)
        self.container = QWidget.createWindowContainer(self.qwin, self)
        self.container.setFocusPolicy(Qt.StrongFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        if self.mobile:
            # телефон: окно шириной с устройство по центру вкладки; высота — сколько помещается
            # (окно браузера нативное, Qt его не обрезает, поэтому выходить за вкладку ему нельзя)
            vp = profile.fingerprint.get("viewport", {"width": 400, "height": 800})
            self.container.setFixedWidth(vp["width"] + 16)
            self.container.setMaximumHeight(vp["height"] + 110)
            self.container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            outer.setContentsMargins(0, 8, 0, 4)
            row = QHBoxLayout()
            row.addStretch()
            row.addWidget(self.container)
            row.addStretch()
            outer.addLayout(row, 1)
            hint = QLabel(f"{profile.name} · окно размера устройства")
            hint.setObjectName("browserHint")
            hint.setAlignment(Qt.AlignCenter)
            outer.addWidget(hint)
        else:
            outer.addWidget(self.container)

    def alive(self) -> bool:
        return bool(user32.IsWindow(self.hwnd))

    def detach(self):
        """Вернуть окно браузера в обычное отдельное окно."""
        if not self.alive():
            return
        try:
            self.qwin.setParent(None)
            _detached.append(self.qwin)
        except Exception:  # noqa: BLE001
            pass
        user32.SetParent(self.hwnd, None)
        style = (self.orig_style | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX) & ~WS_CHILD
        user32.SetWindowLongW(self.hwnd, GWL_STYLE, style)
        w, h = (420, 900) if self.mobile else (1280, 820)
        user32.SetWindowPos(self.hwnd, None, 120, 80, w, h, SWP_FRAMECHANGED | SWP_NOZORDER | SWP_SHOWWINDOW)
        user32.SetForegroundWindow(self.hwnd)
