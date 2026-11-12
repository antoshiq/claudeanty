"""Настройки приложения (data/settings.json). Сохраняются сразу при изменении.

Ключи и токены (SECRET_KEYS) хранятся в файле зашифрованными ключом Windows (DPAPI, как пароли аккаунтов):
«dpapi:…». Старые настройки в открытом виде шифруются при первом запуске. Файл, перенесённый на другой ПК,
откроется, но ключи там будут пустыми — их нужно ввести заново.
"""
import json
import logging

from . import crypto
from .hostos import host_os

SECRET_KEYS = {"tg_token", "captcha_key", "sms_key", "api_token", "backup_password"}
_log = logging.getLogger("claudeanty.settings")

from .paths import DATA_DIR as _DATA_DIR

_PATH = _DATA_DIR / "settings.json"

DEFAULTS = {
    "api_enabled": True,
    "api_port": 3001,
    "api_token": "",  # ключ API: если задан, запросы без X-API-Key / ?token= отклоняются
    "default_kind": "desktop",
    "default_engine": "chromium",
    "default_os": host_os(),  # ОС этого ПК: профиль на «чужой» ОС заметнее
    "default_device": "",
    "default_auto_geo": True,
    "stop_on_exit": True,
    "confirm_delete": True,
    "warn_no_proxy": True,  # спрашивать перед запуском профиля без прокси (из интерфейса)
    "onboarding_done": False,  # подсказка «Начало работы» над таблицей скрыта
    "check_proxy_on_add": True,
    "refresh_interval": 2,
    "accent": "blue",
    "theme": "dark",
    "embed_browser": False,
    "collapsed_sections": [],  # свёрнутые разделы боковой панели
    "arrange_last": {},  # последние параметры «Разложить окна»: mode, monitor, cols, gap
    "chrome_engine": "chrome",  # chrome — установленный Google Chrome, chromium — Chrome for Testing (Playwright)
    "update_check": True,
    "self_update_check": True,  # раз в сутки проверять новую версию claudeAnty (selfupdate.py)
    "update_repo": "antoshiq/claudeanty",  # GitHub-репозиторий с релизами (установщик + .sha256)
    "self_update_checked": 0,
    "self_update_latest": "",
    "self_update_skip": "",  # «пропустить эту версию»
    "engine_watch": True,  # после обновления движка проверять профили-образцы (enginewatch.py)
    "engine_watch_profiles": [],  # образцы; пусто — до двух автоматически (ПК и телефон)
    "engine_seen_version": "",  # последняя версия движка, с которой работали
    "engine_watch_last": {},  # результат последней автопроверки
    "automation_driver": "patchright",  # patchright (без следов CDP Runtime) | playwright  # проверять, не отстаёт ли версия движка
    "warmup_sites": [],  # пусто = встроенный список
    "warmup_count": 8,
    "warmup_dwell": 12,
    "backup_keep": 10,  # сколько последних резервных копий хранить
    "backup_secrets": False,  # класть ли пароли и 2FA в резервные копии
    "backup_encryption": "dpapi",  # dpapi — ключом Windows этого ПК | password — паролем | none — без шифрования
    "backup_password": "",  # пароль резервных копий (для режима password; в settings.json — зашифрован)
    "proxy_check_on_start": True,  # проверять прокси перед запуском профиля: мёртвый — не запускать
    "proxy_check_cache": 120,  # секунд: недавно проверенный прокси перед запуском не проверяется снова
    "proxy_geo_change": "warn",  # прокси сменил страну: warn — запустить и предупредить, block — не запускать
    "change_ip_on_start": True,  # менять IP мобильного прокси (если есть ссылка) перед запуском профиля
    "change_ip_cooldown": 60,  # не чаще, секунд
    "change_ip_wait": 45,  # сколько ждать, пока прокси выйдет в сеть с новым IP
    "rpa_parallel": 3,  # сколько профилей сценарий выполняет одновременно
    "captcha_provider": "2captcha",  # 2captcha | capsolver
    "captcha_key": "",
    "tg_token": "",  # бот Telegram для уведомлений
    "tg_chat": "",
    "tg_events": ["task_error", "scenario_error", "check_fail", "health_worse", "ban", "engine", "engine_update"],
    "ban_watch": True,  # ставить метку «Бан» по признакам на страницах (правила ниже)
    "ban_rules": None,  # None — встроенные правила (banwatch.DEFAULT_RULES), иначе список {"site","kind","value"}
    "sms_provider": "5sim",  # 5sim | sms-activate (и совместимые с его API сервисы)
    "sms_key": "",
    "sms_activate_url": "https://api.sms-activate.ae/stubs/handler_api.php",
}

_data: dict = {}  # как в файле (секреты — зашифрованные)
_plain: dict = {}  # расшифрованные секреты в памяти


def _is_token(v) -> bool:
    return isinstance(v, str) and v.startswith(("dpapi:", "plain:"))


def load():
    global _data
    try:
        _data = json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _data = {}
    _plain.clear()
    migrate = False
    for k in SECRET_KEYS:
        v = _data.get(k)
        if not v:
            continue
        if _is_token(v):
            try:
                _plain[k] = crypto.decrypt(v)
            except Exception:  # noqa: BLE001 — settings.json с другого ПК / другой учётной записи
                _log.warning("настройка %s не расшифровалась (файл с другого ПК?) — нужно ввести заново", k)
                _plain[k] = ""
        else:  # старый файл: ключ в открытом виде — зашифровать
            _plain[k] = str(v)
            migrate = True
    if migrate:
        for k, v in _plain.items():
            if v and not _is_token(_data.get(k)):
                _data[k] = crypto.encrypt(v)
        try:
            save()
        except OSError:
            pass


def save():
    _PATH.parent.mkdir(exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


def get(key: str):
    if key in SECRET_KEYS:
        return _plain.get(key, DEFAULTS[key]) if key in _data else DEFAULTS[key]
    return _data.get(key, DEFAULTS[key])


def set(key: str, value):  # noqa: A001
    if key in SECRET_KEYS:
        _plain[key] = value or ""
        _data[key] = crypto.encrypt(value) if value else ""
    else:
        _data[key] = value
    save()


load()
