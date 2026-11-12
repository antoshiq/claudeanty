"""Обновление самого claudeAnty.

Собранная версия (установщик): новая версия — релиз на GitHub (settings «update_repo», по умолчанию
antoshiq/claudeanty) с файлами claudeAnty-setup-<версия>.exe и claudeAnty-setup-<версия>.exe.sha256.
Установщик скачивается в папку данных, проверяется SHA-256 (без совпадения не запускается), запускается
тихо (/SILENT) и сам перезапускает программу; claudeAnty перед этим закрывается. Данные лежат отдельно
(%LOCALAPPDATA%\\claudeAnty\\data) и при обновлении не трогаются.

Запуск из исходников (git): «git pull --ff-only» + «pip install -r requirements.txt», затем перезапуск вручную.
Локальные изменения в коде git pull не затирает — при конфликте обновление просто не выполнится.
"""
import hashlib
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from . import __version__, settings
from .paths import APP_DIR, DATA_DIR, FROZEN

ASSET_RX = re.compile(r"claudeAnty-setup-.*\.exe$", re.I)
_cache: dict = {}
TTL = 6 * 3600


def _tuple(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums[:4]) or (0,)


def newer(a: str, b: str) -> bool:
    """a новее b?"""
    return _tuple(a) > _tuple(b)


def _flags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def mode() -> str:
    """installer — собранная версия, git — исходники с .git, source — исходники без git."""
    if FROZEN:
        return "installer"
    return "git" if (APP_DIR / ".git").exists() else "source"


def latest_release(force: bool = False) -> dict:
    """Последний релиз на GitHub: {"version", "notes", "url", "asset", "asset_url", "sha_url", "size"}."""
    hit = _cache.get("rel")
    if hit and not force and time.time() - hit[0] < TTL:
        return hit[1]
    repo = settings.get("update_repo")
    r = httpx.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=20,
                  headers={"Accept": "application/vnd.github+json", "User-Agent": "claudeAnty-updater"})
    if r.status_code == 404:
        raise RuntimeError(f"в репозитории {repo} нет релизов (или он закрытый — тогда обновляйте вручную)")
    r.raise_for_status()
    d = r.json()
    assets = {a["name"]: a for a in d.get("assets", [])}
    exe = next((a for n, a in assets.items() if ASSET_RX.search(n)), None)
    sha = assets.get(exe["name"] + ".sha256") if exe else None
    out = {"version": (d.get("tag_name") or "").lstrip("vV"), "notes": (d.get("body") or "")[:3000],
           "url": d.get("html_url", ""), "asset": exe["name"] if exe else "",
           "asset_url": exe["browser_download_url"] if exe else "", "size": exe["size"] if exe else 0,
           "sha_url": sha["browser_download_url"] if sha else ""}
    _cache["rel"] = (time.time(), out)
    return out


def check(force: bool = False) -> dict:
    """{"current", "latest", "available", "mode", "message", "release"}."""
    out = {"current": __version__, "latest": "", "available": False, "mode": mode(), "message": "", "release": {}}
    try:
        rel = latest_release(force)
    except Exception as e:  # noqa: BLE001
        out["message"] = f"Не удалось проверить обновления: {str(e).splitlines()[0][:200]}"
        return out
    out["latest"], out["release"] = rel["version"], rel
    out["available"] = newer(rel["version"], __version__)
    if not out["available"]:
        out["message"] = f"claudeAnty {__version__} — последняя версия."
    elif out["mode"] == "installer" and not rel["asset_url"]:
        out["message"] = f"Есть версия {rel['version']}, но в релизе нет установщика — скачайте вручную: {rel['url']}"
    else:
        out["message"] = f"Доступна версия {rel['version']} (у вас {__version__})."
    return out


def _download(url: str, dst: Path, progress: Callable[[str], None], size: int = 0) -> None:
    tmp = dst.with_suffix(dst.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60,
                      headers={"User-Agent": "claudeAnty-updater"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or size or 0)
        got, last = 0, 0.0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                got += len(chunk)
                if total and time.time() - last > 1:
                    last = time.time()
                    progress(f"Загрузка обновления: {got * 100 // total}% ({got / 1e6:.1f} из {total / 1e6:.1f} МБ)")
    tmp.replace(dst)


def download_installer(rel: dict, progress: Callable[[str], None] = lambda s: None) -> Path:
    """Скачать установщик и проверить SHA-256. -> путь к проверенному exe."""
    if not rel.get("asset_url"):
        raise RuntimeError("в релизе нет установщика")
    if not rel.get("sha_url"):
        raise RuntimeError(f"в релизе нет файла {rel['asset']}.sha256 — без проверки установщик не запускается")
    d = DATA_DIR / "updates"
    d.mkdir(exist_ok=True)
    for old in d.glob("claudeAnty-setup-*"):
        try:
            old.unlink()
        except OSError:
            pass
    exe = d / rel["asset"]
    expected = httpx.get(rel["sha_url"], follow_redirects=True, timeout=20,
                         headers={"User-Agent": "claudeAnty-updater"}).text.split()[0].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError("файл .sha256 в релизе неверного формата")
    _download(rel["asset_url"], exe, progress, rel.get("size", 0))
    h = hashlib.sha256()
    with open(exe, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != expected:
        exe.unlink(missing_ok=True)
        raise RuntimeError("контрольная сумма установщика не совпала — файл повреждён или подменён, не запускаю")
    return exe


def run_installer(exe: Path) -> None:
    """Запускает установщик тихо; он закроет claudeAnty, если тот ещё открыт (CloseApplications), а в конце
    запустит новую версию (секция [Run] в installer.iss). Вызывающий должен сразу завершить приложение."""
    subprocess.Popen([str(exe), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                      f"/LOG={DATA_DIR / 'updates' / 'install.log'}"],
                     close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def update_source(progress: Callable[[str], None] = lambda s: None) -> str:
    """Исходники: git pull --ff-only и зависимости. -> текст результата."""
    if mode() != "git":
        raise RuntimeError("claudeAnty запущен не из git-репозитория — скачайте новую версию вручную")

    def run(args: list[str], title: str) -> str:
        progress(f"Обновление: {title}…")
        r = subprocess.run(args, cwd=APP_DIR, capture_output=True, text=True, timeout=1800, creationflags=_flags())
        if r.returncode != 0:
            raise RuntimeError(f"{title}: {(r.stderr or r.stdout).strip()[-400:]}")
        return r.stdout.strip()

    before = run(["git", "rev-parse", "--short", "HEAD"], "git rev-parse")
    out = run(["git", "pull", "--ff-only"], "git pull --ff-only")
    after = run(["git", "rev-parse", "--short", "HEAD"], "git rev-parse")
    if before == after:
        return "Обновлений в репозитории нет." + (f" ({out.splitlines()[-1]})" if out else "")
    run([sys.executable, "-m", "pip", "install", "-r", str(APP_DIR / "requirements.txt")], "pip install -r requirements.txt")
    return f"Код обновлён {before} → {after}. Перезапустите claudeAnty, чтобы новая версия заработала."


def should_check() -> bool:
    return bool(settings.get("self_update_check")) and time.time() - float(settings.get("self_update_checked") or 0) > 86400


def remember(res: dict) -> None:
    """Запомнить результат автоматической проверки (раз в сутки)."""
    settings.set("self_update_checked", time.time())
    if res.get("latest"):
        settings.set("self_update_latest", res["latest"])


def skipped(version: str) -> bool:
    return bool(version) and settings.get("self_update_skip") == version


def pending_notice() -> Optional[str]:
    """Текст для строки состояния, если при прошлой проверке нашлась новая версия."""
    v = settings.get("self_update_latest") or ""
    return f"Доступна версия {v}" if v and newer(v, __version__) and not skipped(v) else None
