"""Версия Chrome в отпечатке = версия реального движка.

Если UA говорит «Chrome 129», а движок 151, сайт видит это по поддерживаемым JS-функциям.
Поэтому версия в UA и Client Hints подставляется при каждом запуске из установленного Chromium.
"""
import re
from functools import lru_cache
from pathlib import Path

_GREASE_CHARS = [" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_"]
_GREASE_VERSIONS = ["8", "99", "24"]
_ORDERS = [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]


@lru_cache(maxsize=1)
def engine_version() -> str:
    """Полная версия установленного Playwright Chromium, например 151.0.7922.34."""
    # Точный путь знает только Playwright (на диске могут быть несколько сборок Chromium).
    # Sync API нельзя вызывать внутри asyncio-цикла, поэтому manager прогревает кэш при старте приложения.
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = Path(p.chromium.executable_path)
    except Exception:  # noqa: BLE001
        exe = _find_exe()
    if not exe.exists():
        return "131.0.0.0"
    for f in exe.parent.iterdir():
        m = re.fullmatch(r"(\d+\.\d+\.\d+\.\d+)(\.manifest)?", f.name)
        if m:
            return m.group(1)
    return "131.0.0.0"


def _find_exe() -> Path:
    import os
    base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / "AppData/Local/ms-playwright")
    cands = sorted(base.glob("chromium-*/chrome-win*/chrome.exe")) + sorted(base.glob("chromium-*/chrome-linux*/chrome"))
    return cands[-1] if cands else Path("chrome")


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
