"""Настройки приложения (data/settings.json). Сохраняются сразу при изменении."""
import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "data" / "settings.json"

DEFAULTS = {
    "api_enabled": True,
    "api_port": 3001,
    "default_kind": "desktop",
    "default_engine": "chromium",
    "default_os": "windows",
    "default_device": "",
    "default_auto_geo": True,
    "stop_on_exit": True,
    "confirm_delete": True,
    "check_proxy_on_add": True,
    "refresh_interval": 2,
    "accent": "blue",
    "theme": "dark",
    "embed_browser": False,
    "warmup_sites": [],  # пусто = встроенный список
    "warmup_count": 8,
    "warmup_dwell": 12,
}

_data: dict = {}


def load():
    global _data
    try:
        _data = json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _data = {}


def save():
    _PATH.parent.mkdir(exist_ok=True)
    _PATH.write_text(json.dumps(_data, ensure_ascii=False, indent=2), encoding="utf-8")


def get(key: str):
    return _data.get(key, DEFAULTS[key])


def set(key: str, value):  # noqa: A001
    _data[key] = value
    save()


load()
