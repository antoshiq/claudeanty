r"""Где лежат программа и данные — при запуске из исходников и в собранной версии (PyInstaller).

Исходники: данные в <репозиторий>/data, как раньше.
Собранная версия (claudeAnty.exe): если рядом с exe есть папка data — портативный режим, данные там;
иначе %LOCALAPPDATA%\claudeAnty\data — установщик ставит программу отдельно от данных, поэтому
обновление и удаление программы данные не трогают.
Переменная окружения CLAUDEANTY_DATA задаёт папку данных явно.
"""
import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
# ресурсы пакета (json, js): в сборке — распакованная папка _internal, иначе — исходники
PKG_DIR = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else PKG_DIR.parent


def _data_dir() -> Path:
    env = os.environ.get("CLAUDEANTY_DATA")
    if env:
        return Path(env).expanduser().resolve()
    if not FROZEN:
        return APP_DIR / "data"
    if (APP_DIR / "data").is_dir():
        return APP_DIR / "data"
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "claudeAnty" / "data"


DATA_DIR = _data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
