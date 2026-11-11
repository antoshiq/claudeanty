"""Уведомления в Telegram: ошибки планировщика и сценариев, проблемы проверок, бан профиля, устаревший движок.

Бот создаётся у @BotFather (токен), chat id определяется кнопкой в настройках после того, как вы
написали боту любое сообщение. Отправка идёт в фоновом потоке и никогда не мешает работе: если Telegram
недоступен, уведомление просто теряется (в лог пишется предупреждение).
"""
import logging
import threading

import httpx

from . import settings

log = logging.getLogger("claudeanty.notify")

EVENTS = {
    "task_error": "Ошибка задачи планировщика",
    "task_done": "Задача планировщика выполнена",
    "scenario_error": "Сценарий не прошёл на профиле",
    "check_fail": "Проверка отпечатка или детекторы нашли проблему",
    "ban": "Профилю поставлена метка «Бан»",
    "engine": "Движок Chrome устарел",
}
API = "https://api.telegram.org/bot{token}/{method}"


def enabled(event: str) -> bool:
    return bool(settings.get("tg_token") and settings.get("tg_chat") and event in (settings.get("tg_events") or []))


def _send(text: str, token: str, chat: str) -> dict:
    r = httpx.post(API.format(token=token, method="sendMessage"), timeout=20,
                   json={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True})
    return r.json()


def notify(event: str, text: str):
    """Отправить, если событие включено. Не блокирует вызывающий поток."""
    if not enabled(event):
        return
    token, chat = settings.get("tg_token"), settings.get("tg_chat")

    def run():
        try:
            res = _send(f"claudeAnty · {EVENTS.get(event, event)}\n{text}", token, chat)
            if not res.get("ok"):
                log.warning("telegram: %s", res.get("description"))
        except Exception as e:  # noqa: BLE001
            log.warning("telegram: %s", e)

    threading.Thread(target=run, daemon=True, name="tg-notify").start()


def send_test() -> str:
    res = _send("claudeAnty: тестовое уведомление ✓", settings.get("tg_token"), settings.get("tg_chat"))
    if not res.get("ok"):
        raise RuntimeError(res.get("description") or "Telegram не принял сообщение")
    return "сообщение отправлено"


def detect_chat(token: str) -> str:
    """chat id из последнего сообщения боту (напишите боту /start, затем нажмите кнопку)."""
    r = httpx.get(API.format(token=token, method="getUpdates"), timeout=20).json()
    if not r.get("ok"):
        raise RuntimeError(r.get("description") or "неверный токен")
    for upd in reversed(r.get("result", [])):
        msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            return str(chat["id"])
    raise RuntimeError("боту ещё никто не писал — отправьте ему /start и нажмите кнопку снова")
