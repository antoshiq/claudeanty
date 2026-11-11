"""Какой браузер запускать для Chromium-профилей и какая у него версия.

Два варианта движка (настройка «chrome_engine»):
  * «chrome»   — установленный Google Chrome: сам обновляется, есть Widevine (DRM) и все кодеки,
                 ровно тот браузер, что у обычных людей. Используется по умолчанию, если установлен;
  * «chromium» — Chrome for Testing из Playwright (`playwright install chromium`).

Версия Chrome в отпечатке = версия реального движка: если UA говорит «Chrome 129», а движок 151,
сайт видит это по поддерживаемым JS-функциям. Поэтому версия в UA и Client Hints подставляется
при каждом запуске из того браузера, который реально запускается. Chrome обновляется сам, поэтому
версия перечитывается при изменении файла chrome.exe (кэш по времени изменения).
"""
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

_GREASE_CHARS = [" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_"]
_GREASE_VERSIONS = ["8", "99", "24"]
_ORDERS = [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]
_FALLBACK = "131.0.0.0"


# ---------------- поиск браузеров ----------------
def find_chrome() -> Optional[Path]:
    """Путь к установленному Google Chrome (stable) или None."""
    cands: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                        cands.append(Path(winreg.QueryValueEx(k, "")[0]))
                except OSError:
                    pass
        except ImportError:
            pass
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands.append(Path(base) / "Google/Chrome/Application/chrome.exe")
    elif sys.platform == "darwin":
        cands.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    else:
        cands += [Path("/opt/google/chrome/chrome"), Path("/usr/bin/google-chrome-stable"), Path("/usr/bin/google-chrome")]
    for c in cands:
        if c.is_file():
            return c
    return None


@lru_cache(maxsize=1)
def playwright_chromium() -> Path:
    """chrome.exe из Playwright. Точный путь знает только Playwright (на диске может быть несколько сборок).
    Sync API нельзя вызывать внутри asyncio-цикла, поэтому manager прогревает кэш при старте приложения."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return Path(p.chromium.executable_path)
    except Exception:  # noqa: BLE001
        base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / "AppData/Local/ms-playwright")
        cands = sorted(base.glob("chromium-*/chrome-win*/chrome.exe")) + sorted(base.glob("chromium-*/chrome-linux*/chrome"))
        return cands[-1] if cands else Path("chrome")


def engine_kind() -> str:
    """Фактический движок: «chrome», если выбран и установлен, иначе «chromium»."""
    from .. import settings
    want = settings.get("chrome_engine")
    if want == "chrome" and find_chrome():
        return "chrome"
    return "chromium"


def executable() -> Optional[str]:
    """Путь для launch(executable_path=…); None — браузер Playwright по умолчанию."""
    if engine_kind() == "chrome":
        return str(find_chrome())
    return None


# ---------------- версия ----------------
def _file_version(exe: Path) -> Optional[str]:
    """Версия из ресурсов exe-файла (Windows). У Chrome рядом может лежать и новая версия,
    ожидающая перезапуска (new_chrome.exe) — запускается всё равно chrome.exe со своей версией."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        ver = ctypes.WinDLL("version")
        size = ver.GetFileVersionInfoSizeW(str(exe), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(exe), 0, size, buf):
            return None
        ptr, ln = ctypes.c_void_p(), wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
            return None

        class VS(ctypes.Structure):
            _fields_ = [("sig", wintypes.DWORD), ("struc", wintypes.DWORD), ("ms", wintypes.DWORD), ("ls", wintypes.DWORD)]
        v = ctypes.cast(ptr, ctypes.POINTER(VS)).contents
        return f"{v.ms >> 16}.{v.ms & 0xFFFF}.{v.ls >> 16}.{v.ls & 0xFFFF}"
    except Exception:  # noqa: BLE001
        return None


def _dir_version(exe: Path) -> Optional[str]:
    """Запасной способ: папка/файл с номером версии рядом с exe (Chrome for Testing, Chrome)."""
    found = []
    try:
        for f in exe.parent.iterdir():
            m = re.fullmatch(r"(\d+\.\d+\.\d+\.\d+)(\.manifest)?", f.name)
            if m:
                found.append(m.group(1))
    except OSError:
        return None
    if not found:
        return None
    return min(found, key=lambda v: tuple(int(x) for x in v.split(".")))  # старшая может ждать перезапуска


@lru_cache(maxsize=8)
def _version_of(exe: str, mtime: float) -> str:
    p = Path(exe)
    return _file_version(p) or _dir_version(p) or _FALLBACK


def version_of(exe: Optional[Path]) -> str:
    if not exe or not exe.exists():
        return _FALLBACK
    return _version_of(str(exe), exe.stat().st_mtime)


def engine_exe() -> Path:
    return find_chrome() if engine_kind() == "chrome" else playwright_chromium()


def engine_version() -> str:
    """Полная версия браузера, который будет запущен, например 156.0.8078.81."""
    return version_of(engine_exe())


def pending_update() -> Optional[str]:
    """Chrome скачал обновление, но ждёт перезапуска (рядом лежит new_chrome.exe) — вернёт новую версию."""
    exe = find_chrome()
    if not exe or not (exe.parent / "new_chrome.exe").exists():
        return None
    cur = version_of(exe)
    newer = [d.name for d in exe.parent.iterdir() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", d.name) and d.name != cur]
    return max(newer, key=lambda v: tuple(int(x) for x in v.split("."))) if newer else "?"


# ---------------- UA / Client Hints ----------------
def brands(major: int, brand: str = "Google Chrome") -> tuple[list[dict], str]:
    """Список brands как у настоящего Chrome этой версии (алгоритм GREASE из Chromium)."""
    seed = major
    grease = f"Not{_GREASE_CHARS[seed % 11]}A{_GREASE_CHARS[(seed + 1) % 11]}Brand"
    gver = _GREASE_VERSIONS[seed % 3]
    order = _ORDERS[seed % 6]
    out: list = [None, None, None]
    out[order[0]] = {"brand": grease, "version": gver}
    out[order[1]] = {"brand": "Chromium", "version": str(major)}
    out[order[2]] = {"brand": brand, "version": str(major)}
    return out, grease


def apply_version(fp: dict, full: str | None = None) -> dict:
    """Переписывает версию в UA и Client Hints отпечатка под реальный движок."""
    full = full or engine_version()
    major = int(full.split(".")[0])
    fp = dict(fp)
    fp["user_agent"] = re.sub(r"Chrome/[\d.]+", f"Chrome/{major}.0.0.0", fp["user_agent"])
    ch = dict(fp["ch"])
    ch["brands"], grease = brands(major)
    ch["full_version"] = full
    ch["full_version_list"] = [{"brand": b["brand"], "version": f"{b['version']}.0.0.0" if b["brand"] == grease else full}
                               for b in ch["brands"]]
    fp["ch"] = ch
    return fp
