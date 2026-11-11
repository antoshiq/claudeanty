"""Слежение за версией движка.

Google Chrome обновляется сам (Google Update) — мы только сравниваем установленную версию с последней
стабильной и предупреждаем, если движок отстаёт: старая версия Chrome в UA выделяется на фоне обычных
пользователей. Chrome for Testing (Playwright) обновляется кнопкой: pip install -U playwright +
playwright install chromium.
"""
import subprocess
import sys
import time
from typing import Callable, Optional

import httpx

from .engines import chrome_version as cv

_PLATFORM = {"win32": "win64", "darwin": "mac"}.get(sys.platform, "linux")
_cache: dict[str, tuple[float, str]] = {}
TTL = 6 * 3600


def _cached(key: str, fn: Callable[[], str]) -> str:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def latest_chrome() -> str:
    """Последняя стабильная версия Chrome для этой ОС (официальный API Google)."""
    def fetch():
        r = httpx.get(f"https://versionhistory.googleapis.com/v1/chrome/platforms/{_PLATFORM}/channels/stable/versions",
                      params={"pageSize": 1}, timeout=15)
        return r.json()["versions"][0]["version"]
    return _cached("chrome", fetch)


def latest_playwright() -> str:
    def fetch():
        return httpx.get("https://pypi.org/pypi/playwright/json", timeout=15).json()["info"]["version"]
    return _cached("playwright", fetch)


def installed_playwright() -> str:
    from importlib.metadata import version
    try:
        return version("playwright")
    except Exception:  # noqa: BLE001
        return "?"


def _tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def status(force: bool = False) -> dict:
    """Сводка для настроек, строки состояния и API.

    level: ok | info | warn | error; message — понятный текст на русском.
    """
    if force:
        _cache.clear()
    kind = cv.engine_kind()
    chrome_exe = cv.find_chrome()
    out = {"engine": kind, "chrome_installed": bool(chrome_exe), "version": cv.engine_version(),
           "chrome_version": cv.version_of(chrome_exe) if chrome_exe else "", "latest": "", "pending": "",
           "playwright": installed_playwright(), "playwright_latest": "", "level": "ok", "message": ""}
    try:
        out["latest"] = latest_chrome()
    except Exception as e:  # noqa: BLE001
        out["level"], out["message"] = "error", f"Не удалось узнать последнюю версию Chrome: {e}"
        return out
    cur, latest = out["version"], out["latest"]
    behind = _tuple(latest)[0] - _tuple(cur)[0]

    if kind == "chrome":
        pend = cv.pending_update()
        out["pending"] = pend or ""
        if pend:
            out["level"] = "info"
            out["message"] = (f"Chrome скачал обновление {pend}. Оно установится, когда будут закрыты все окна "
                              f"Google Chrome (и ваши, и профилей). Сейчас профили запускаются на {cur}.")
        elif behind >= 2:
            out["level"] = "warn"
            out["message"] = (f"Chrome {cur} отстаёт от актуального {latest} на {behind} версии — такой UA выделяется. "
                              "Откройте «О браузере Chrome», чтобы он обновился.")
        elif _tuple(cur) < _tuple(latest):
            out["level"] = "info"
            out["message"] = (f"Доступна версия {latest}. Chrome обновится сам; разница в одну версию нормальна, "
                              "пока новая раскатывается на пользователей.")
        else:
            out["message"] = f"Google Chrome {cur} — актуальная версия."
        if not out["message"]:
            out["message"] = f"Google Chrome {cur}."
    else:
        try:
            out["playwright_latest"] = latest_playwright()
        except Exception:  # noqa: BLE001
            pass
        pw_old = out["playwright_latest"] and _tuple(out["playwright"]) < _tuple(out["playwright_latest"])
        note = "" if chrome_exe else " Google Chrome не установлен — используется Chrome for Testing."
        if behind >= 2 or pw_old:
            out["level"] = "warn" if behind >= 2 else "info"
            out["message"] = (f"Chrome for Testing {cur}, актуальный Chrome — {latest}."
                              + (f" Есть Playwright {out['playwright_latest']} (у вас {out['playwright']})." if pw_old else "")
                              + " Нажмите «Обновить движок»." + note)
        else:
            out["message"] = f"Chrome for Testing {cur} (актуальный Chrome — {latest})." + note
    return out


def update_chromium(log: Callable[[str], None] = lambda s: None) -> str:
    """Обновляет Playwright и его Chromium. Новая версия Playwright подхватится после перезапуска приложения."""
    before = cv.version_of(cv.playwright_chromium())
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    for args, title in (([sys.executable, "-m", "pip", "install", "-U", "playwright"], "pip install -U playwright"),
                        ([sys.executable, "-m", "playwright", "install", "chromium"], "playwright install chromium")):
        log(f"Обновление движка: {title}…")
        r = subprocess.run(args, capture_output=True, text=True, timeout=1800, creationflags=flags)
        if r.returncode != 0:
            raise RuntimeError(f"{title}: {(r.stderr or r.stdout).strip()[-400:]}")
    cv.playwright_chromium.cache_clear()
    _cache.clear()
    after = cv.version_of(cv.playwright_chromium())
    if after != before:
        return f"Chrome for Testing обновлён: {before} → {after}"
    return (f"Chrome for Testing {before} — новее для установленного Playwright нет. "
            "Если Playwright обновился, перезапустите claudeAnty и нажмите кнопку ещё раз.")


def open_chrome_update() -> Optional[str]:
    """Открывает «О браузере Chrome» — Chrome сразу проверяет и скачивает обновление."""
    exe = cv.find_chrome()
    if not exe:
        return None
    subprocess.Popen([str(exe), "chrome://settings/help"], close_fds=True)
    return str(exe)
