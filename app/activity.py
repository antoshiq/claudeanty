"""Журнал активности профиля: запуски и остановки (сколько работал, на каких сайтах был), сценарии,
прогрев и планы прогрева, проверки, прокси, бан. Помогает разбирать баны: что делали с профилем перед ним.

Хранится в таблице activity; записи старше KEEP_DAYS удаляются.
"""
import time
from typing import Optional

from . import db

KINDS = {
    "start": "Запуск", "stop": "Остановка", "scenario": "Сценарий", "warmup": "Прогрев", "plan": "План прогрева",
    "check": "Проверка", "proxy": "Прокси", "ban": "Метка", "edit": "Изменение",
}
KEEP_DAYS = 180
_last_cleanup = 0.0


def log(pid: int, kind: str, text: str, data: Optional[dict] = None) -> None:
    global _last_cleanup
    try:
        db.add_activity(pid, time.time(), kind, text, data or {})
        if time.time() - _last_cleanup > 3600:
            _last_cleanup = time.time()
            db.cleanup_activity(time.time() - KEEP_DAYS * 86400)
    except Exception:  # noqa: BLE001 — журнал никогда не мешает основной работе
        pass


def duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s} с"
    if s < 3600:
        return f"{s // 60} мин {s % 60} с"
    return f"{s // 3600} ч {s % 3600 // 60} мин"


def summary(pid: int) -> str:
    """Строка для панели «Отпечаток»: последний запуск и сколько всего работал за 7 дней."""
    week = db.list_activity(pid, since=time.time() - 7 * 86400)
    starts = [r for r in week if r["kind"] == "start"]
    total = sum((r["data"] or {}).get("seconds", 0) for r in week if r["kind"] == "stop")
    last = db.list_activity(pid, limit=1)
    if not last:
        return ""
    when = time.strftime("%d.%m %H:%M", time.localtime(last[0]["ts"]))
    return f"запусков за 7 дней: {len(starts)}, в работе {duration(total)}; последнее: {when} — {last[0]['text'][:80]}"
