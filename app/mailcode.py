"""Коды из почты для сценариев: шаг «Ждать письмо» читает ящик по IMAP и достаёт код подтверждения.

Ящик — аккаунт из «Пароли и 2FA» профиля: логин — адрес почты, пароль — пароль приложения
(Gmail, Яндекс, Mail.ru, Outlook требуют именно его, обычный пароль по IMAP не пустят). Сервер IMAP
определяется по домену адреса или задаётся в аккаунте («IMAP-сервер»).

Письмо подходит, если пришло после начала сценария, отправитель и тема содержат заданные строки,
а в теме или тексте нашлось регулярное выражение (по умолчанию — первое число из 4–8 цифр).
Одно и то же письмо в одном прогоне сценария дважды не используется.
"""
import asyncio
import datetime as dt
import email
import html
import imaplib
import re
import time
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Callable, Optional

KNOWN = {
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "yandex.ru": "imap.yandex.ru", "ya.ru": "imap.yandex.ru", "yandex.com": "imap.yandex.com",
    "mail.ru": "imap.mail.ru", "bk.ru": "imap.mail.ru", "inbox.ru": "imap.mail.ru", "list.ru": "imap.mail.ru",
    "internet.ru": "imap.mail.ru", "rambler.ru": "imap.rambler.ru",
    "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com", "msn.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com", "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com",
    "gmx.com": "imap.gmx.com", "gmx.net": "imap.gmx.net", "gmx.de": "imap.gmx.net", "web.de": "imap.web.de",
    "aol.com": "imap.aol.com", "zoho.com": "imap.zoho.com",
    "firstmail.ltd": "imap.firstmail.ltd", "onet.pl": "imap.poczta.onet.pl", "wp.pl": "imap.wp.pl",
}
DEFAULT_REGEX = r"(?<![\d#])(\d{4,8})(?!\d)"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SKEW = 90  # секунд: часы почтового сервера и ПК могут расходиться


def imap_server(login: str, override: str = "") -> tuple[str, int]:
    target = (override or "").strip()
    if not target:
        domain = login.rpartition("@")[2].lower().strip()
        if not domain:
            raise ValueError("логин аккаунта почты — не адрес почты; укажите IMAP-сервер в аккаунте")
        target = KNOWN.get(domain, f"imap.{domain}")
    host, _, port = target.partition(":")
    return host.strip(), int(port or 993)


def _decode(value) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001
        return str(value or "")


def _html_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    return html.unescape(re.sub(r"<[^>]+>", " ", s))


def message_parts(msg) -> tuple[str, str, str]:
    """-> (от кого, тема, текст). Текст — text/plain, а если его нет — text/html без тегов."""
    plain, rich = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AttributeError):
            continue
        (rich if part.get_content_subtype() == "html" else plain).append(text)
    body = "\n".join(plain) if plain else _html_text("\n".join(rich))
    return _decode(msg.get("From")), _decode(msg.get("Subject")), body


def find_code(subject: str, body: str, regex: str = "") -> Optional[str]:
    rx = re.compile(regex or DEFAULT_REGEX)
    for text in (subject, body):
        m = rx.search(text or "")
        if m:
            return (m.group(1) if m.groups() else m.group(0)).strip()
    return None


def matches(msg, since: float, sender: str, subject: str) -> bool:
    try:
        sent = parsedate_to_datetime(msg.get("Date")).timestamp()
        if sent < since - SKEW:
            return False
    except (TypeError, ValueError):
        pass
    frm, subj, _ = message_parts(msg)
    if sender and sender.lower() not in frm.lower():
        return False
    return not subject or subject.lower() in subj.lower()


def check_mailbox(login: str, password: str, server: str, since: float, sender: str = "", subject: str = "",
                  regex: str = "", folders: str = "INBOX", seen: Optional[set] = None,
                  connect: Callable = imaplib.IMAP4_SSL) -> Optional[tuple[str, str]]:
    """Один проход по ящику. -> (код, id письма) или None. Синхронно (imaplib) — вызывать в потоке."""
    seen = seen if seen is not None else set()
    host, port = imap_server(login, server)
    day = dt.date.fromtimestamp(since - SKEW - 86400)  # SINCE — по дате без времени, берём с запасом
    crit = ["SINCE", f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"]
    if sender and sender.isascii():
        crit += ["FROM", f'"{sender}"']
    box = connect(host, port)
    try:
        box.login(login, password)
        for folder in [f.strip() for f in (folders or "INBOX").split(",") if f.strip()]:
            typ, _ = box.select(f'"{folder}"' if " " in folder else folder, readonly=True)
            if typ != "OK":
                continue
            typ, data = box.uid("SEARCH", None, *crit)
            if typ != "OK" or not data or not data[0]:
                continue
            for uid in reversed(data[0].split()[-25:]):  # новые первыми
                key = f"{folder}:{uid.decode()}"
                if key in seen:
                    continue
                typ, msg_data = box.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((x[1] for x in msg_data or [] if isinstance(x, tuple)), None)
                if typ != "OK" or not raw:
                    continue
                msg = email.message_from_bytes(raw)
                if not matches(msg, since, sender, subject):
                    continue
                _, subj, body = message_parts(msg)
                code = find_code(subj, body, regex)
                if code:
                    seen.add(key)
                    return code, key
        return None
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass


async def wait_code(login: str, password: str, server: str, since: float, sender: str = "", subject: str = "",
                    regex: str = "", folders: str = "INBOX", timeout: float = 120, seen: Optional[set] = None,
                    poll: float = 6) -> str:
    deadline = time.time() + timeout
    first_error = None
    while True:
        try:
            res = await asyncio.to_thread(check_mailbox, login, password, server, since, sender, subject, regex,
                                          folders, seen)
            if res:
                return res[0]
        except imaplib.IMAP4.error as e:
            msg = str(e)
            if "auth" in msg.lower() or "login" in msg.lower() or "credentials" in msg.lower():
                raise RuntimeError(f"почта {login}: не пускает по IMAP ({msg[:120]}). Нужен пароль приложения "
                                   "и включённый IMAP в настройках ящика") from e
            first_error = first_error or msg
        except OSError as e:
            first_error = first_error or str(e)
            if time.time() >= deadline:
                raise RuntimeError(f"почта {login}: сервер IMAP недоступен — {e}") from e
        left = deadline - time.time()
        if left <= 0:
            raise RuntimeError(f"письмо не пришло за {int(timeout)} с" + (f" (ошибка IMAP: {first_error[:120]})"
                                                                          if first_error else ""))
        await asyncio.sleep(min(poll, left))
