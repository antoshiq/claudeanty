"""Расстановка окон профилей: сетка или каскад на выбранном мониторе. Только Windows.

Окна двигаются средствами Windows (SetWindowPos) по процессу профиля — в браузер ничего не отправляется,
поэтому для сайта это обычное изменение размера окна пользователем. Работает для Chrome и Camoufox.

Окно ПК-профиля не делается больше его «экрана» из отпечатка (иначе innerWidth > screen.width — явная
подделка): если ячейка больше, окно занимает размер экрана профиля и встаёт в её левый верхний угол.
Телефонный профиль получает ширину устройства (его страница всё равно рисуется в размер устройства).
Встроенные во вкладки приложения окна не трогаются.
"""
import ctypes
import math
import sys
from ctypes import wintypes as wt
from typing import Optional

from . import db
from .config import PROFILES_DIR

SUPPORTED = sys.platform == "win32"
SW_RESTORE = 9
SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0004, 0x0010, 0x0040
DWMWA_EXTENDED_FRAME_BOUNDS = 9


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def monitors() -> list[dict]:
    """Мониторы: [{"index", "primary", "left", "top", "width", "height"}] — рабочая область (без панели задач),
    в физических пикселях."""
    if not SUPPORTED:
        return []
    user32 = ctypes.windll.user32
    out: list[dict] = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(hmon, _hdc, _rect, _data):
        mi = _MonitorInfo()
        mi.cbSize = ctypes.sizeof(_MonitorInfo)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            w = mi.rcWork
            out.append({"primary": bool(mi.dwFlags & 1), "left": w.left, "top": w.top,
                        "width": w.right - w.left, "height": w.bottom - w.top})
        return True

    user32.EnumDisplayMonitors(None, None, proc(cb), 0)
    out.sort(key=lambda m: (not m["primary"], m["left"], m["top"]))
    for i, m in enumerate(out):
        m["index"] = i
    return out


def grid(n: int, width: int, height: int, cols: int = 0) -> tuple[int, int]:
    """Колонки и строки для n окон: ячейки как можно ближе к пропорциям 16:10."""
    if n <= 0:
        return 0, 0
    if cols <= 0:
        best = None
        for c in range(1, n + 1):
            r = math.ceil(n / c)
            cw, ch = width / c, height / r
            score = abs(math.log((cw / ch) / 1.6)) + 0.15 * (c * r - n)  # пустые ячейки — небольшой штраф
            if best is None or score < best[0]:
                best = (score, c)
        cols = best[1]
    cols = max(1, min(cols, n))
    return cols, math.ceil(n / cols)


def _frame_margins(hwnd) -> tuple[int, int, int, int]:
    """Невидимые рамки окна Windows 10/11 (тень для изменения размера): на сколько GetWindowRect больше видимого."""
    user32 = ctypes.windll.user32
    r, f = wt.RECT(), wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(f), ctypes.sizeof(f))
    except Exception:  # noqa: BLE001
        return 0, 0, 0, 0
    if f.right <= f.left:
        return 0, 0, 0, 0
    return f.left - r.left, f.top - r.top, r.right - f.right, r.bottom - f.bottom


def _place(hwnd, x: int, y: int, w: int, h: int):
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    ml, mt, mr, mb = _frame_margins(hwnd)
    user32.SetWindowPos(hwnd, None, x - ml, y - mt, w + ml + mr, h + mt + mb,
                        SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)


def profile_window(pid: int) -> Optional[int]:
    from .gui.embed import find_window, profile_pids
    return find_window(profile_pids(str(PROFILES_DIR / str(pid))))


def arrange(pids: list[int], mode: str = "grid", monitor: int = 0, cols: int = 0, gap: int = 0,
            skip: Optional[set[int]] = None, wait: float = 0) -> dict:
    """Разложить окна профилей pids (в этом порядке) на мониторе. mode: grid | cascade.
    wait — сколько секунд ждать окна только что запущенных профилей.
    -> {"placed": [pid], "missing": [pid] (окно не найдено / без окна), "cols", "rows", "cell": [w, h]}."""
    import time
    if not SUPPORTED:
        raise RuntimeError("расстановка окон работает только в Windows")
    mons = monitors()
    if not mons:
        raise RuntimeError("не удалось получить список мониторов")
    mon = mons[monitor] if 0 <= monitor < len(mons) else mons[0]
    skip = skip or set()
    todo = [pid for pid in pids if pid not in skip]
    found: dict[int, int] = {}
    deadline = time.time() + wait
    while True:
        for pid in todo:
            if pid not in found and (hwnd := profile_window(pid)):
                found[pid] = hwnd
        if len(found) == len(todo) or time.time() >= deadline:
            break
        time.sleep(0.5)
    wins = [(pid, found[pid]) for pid in todo if pid in found]
    missing = [pid for pid in todo if pid not in found]
    res = {"placed": [], "missing": missing, "cols": 0, "rows": 0, "cell": [0, 0], "monitor": mon["index"]}
    if not wins:
        return res
    X, Y, W, H = mon["left"], mon["top"], mon["width"], mon["height"]
    if mode == "cascade":
        step = 32
        w = max(640, min(int(W * 0.7), W - step * (len(wins) - 1)))
        h = max(480, min(int(H * 0.75), H - step * (len(wins) - 1)))
        for i, (pid, hwnd) in enumerate(wins):
            off = (i * step) % max(step, min(W - w, H - h) or step)
            cw, ch = _fit(pid, w, h, _scale(hwnd))
            _place(hwnd, X + off, Y + off, cw, ch)
            res["placed"].append(pid)
        res["cell"] = [w, h]
        return res
    c, r = grid(len(wins), W, H, cols)
    cw, ch = (W - gap * (c - 1)) // c, (H - gap * (r - 1)) // r
    for i, (pid, hwnd) in enumerate(wins):
        col, row = i % c, i // c
        w, h = _fit(pid, cw, ch, _scale(hwnd))
        _place(hwnd, X + col * (cw + gap), Y + row * (ch + gap), w, h)
        res["placed"].append(pid)
    res.update(cols=c, rows=r, cell=[cw, ch])
    return res


def _scale(hwnd) -> float:
    """Масштаб Windows для окна (125% -> 1.25): размеры отпечатка — в CSS-пикселях, окна — в физических."""
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForWindow(hwnd) / 96)
    except Exception:  # noqa: BLE001
        return 1.0


def _fit(pid: int, w: int, h: int, scale: float = 1.0) -> tuple[int, int]:
    """Размер окна в ячейке w×h с учётом «экрана» профиля из отпечатка."""
    p = db.get_profile(pid)
    fp = (p.fingerprint if p else {}) or {}
    if p and p.kind == "mobile":
        vp = fp.get("viewport") or {"width": 400, "height": 800}
        return min(w, int((vp["width"] + 16) * scale)), min(h, int((vp["height"] + 110) * scale))
    sc = fp.get("screen") or {}
    sw, sh = int((sc.get("width") or 100000) * scale), int((sc.get("height") or 100000) * scale)
    return min(w, sw), min(h, sh)
