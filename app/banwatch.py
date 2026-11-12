"""Авто-метка «Бан» по признакам на странице.

Пока профиль запущен (с окном, без окна, в сценарии), после каждого перехода на странице проверяются
правила для её сайта:
  url     — адрес содержит строку (facebook.com | url | /checkpoint);
  text    — на странице есть текст («Your account has been disabled»), без учёта регистра;
  captcha — капча (reCAPTCHA, hCaptcha, Turnstile) на этом сайте N запусков подряд (facebook.com | captcha | 3).
Сработало правило — профилю ставится метка «Бан», причина пишется в заметку ban_reason, уходит
уведомление в Telegram (событие ban). Правила настраиваются: Настройки → Профили → «Признаки бана».

Страница читается в изолированном мире (Patchright) — сайт этих проверок не видит.
"""
import asyncio
import logging
import time
from typing import Optional
from urllib.parse import urlparse

from . import db, settings

log = logging.getLogger("claudeanty.banwatch")

KINDS = {"url": "адрес содержит", "text": "на странице текст", "captcha": "капча N запусков подряд"}

DEFAULT_RULES = [
    {"site": "facebook.com", "kind": "url", "value": "/checkpoint"},
    {"site": "facebook.com", "kind": "text", "value": "your account has been disabled"},
    {"site": "facebook.com", "kind": "text", "value": "ваш аккаунт отключен"},
    {"site": "facebook.com", "kind": "captcha", "value": "3"},
    {"site": "instagram.com", "kind": "url", "value": "/accounts/suspended"},
    {"site": "instagram.com", "kind": "url", "value": "/accounts/disabled"},
    {"site": "instagram.com", "kind": "captcha", "value": "3"},
    {"site": "google.com", "kind": "url", "value": "/signin/rejected"},
    {"site": "google.com", "kind": "url", "value": "/disabled/explanation"},
    {"site": "google.com", "kind": "text", "value": "your account has been disabled"},
    {"site": "google.com", "kind": "text", "value": "ваш аккаунт отключен"},
    {"site": "x.com", "kind": "url", "value": "/account/access"},
    {"site": "x.com", "kind": "text", "value": "your account is suspended"},
    {"site": "twitter.com", "kind": "url", "value": "/account/access"},
    {"site": "tiktok.com", "kind": "text", "value": "your account was banned"},
    {"site": "amazon.com", "kind": "text", "value": "your account has been deactivated"},
]

CHECK_DELAY = 2.0  # секунд после перехода: даём странице дорисоваться


def rules() -> list[dict]:
    r = settings.get("ban_rules")
    return DEFAULT_RULES if r is None else r


def format_rules(items: list[dict]) -> str:
    return "\n".join(f"{r['site']} | {r['kind']} | {r['value']}" for r in items)


def parse_rules(text: str) -> list[dict]:
    """Строки «сайт | url|text|captcha | значение». Пустые и начинающиеся с # пропускаются."""
    out = []
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"строка {n}: нужно «сайт | url, text или captcha | значение»")
        site, kind, value = parts
        kind = kind.lower()
        if kind not in KINDS:
            raise ValueError(f"строка {n}: вид «{kind}» — нужно url, text или captcha")
        if kind == "captcha" and not (value.isdigit() and int(value) >= 1):
            raise ValueError(f"строка {n}: для captcha значение — число запусков подряд (например, 3)")
        site = site.lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").strip("/")
        out.append({"site": site, "kind": kind, "value": value})
    return out


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def site_matches(host: str, site: str) -> bool:
    return bool(host) and (host == site or host.endswith("." + site))


def match(url: str, text: Optional[str], items: list[dict]) -> Optional[dict]:
    """Первое сработавшее правило url/text для страницы (text=None — текст не читали)."""
    host = host_of(url)
    low_url = url.lower()
    low_text = (text or "").lower()
    for r in items:
        if not site_matches(host, r["site"]):
            continue
        if r["kind"] == "url" and r["value"].lower() in low_url:
            return r
        if r["kind"] == "text" and text is not None and r["value"].lower() in low_text:
            return r
    return None


def describe(r: dict) -> str:
    if r["kind"] == "captcha":
        return f"{r['site']}: капча в {r['value']} запусках профиля подряд"
    return f"{r['site']}: {KINDS[r['kind']]} «{r['value']}»"


def mark_ban(manager, pid: int, reason: str) -> bool:
    p = db.get_profile(pid)
    if not p or p.status == "ban":
        return False
    old = p.status
    p.status = "ban"
    p.ban_reason = f"{time.strftime('%Y-%m-%d %H:%M')} — {reason}"
    db.save_profile(p)
    from . import banstats
    banstats.status_changed(p, old, "ban", "auto", reason)
    from . import activity
    activity.log(pid, "ban", f"метка «Бан» автоматически — {reason}")
    manager.scheduler.say(f"#{pid} {p.name}: метка «Бан» — {reason}")
    from .notify import notify
    notify("ban", f"Профиль #{pid} {p.name}" + (f" (папка {p.folder})" if p.folder else "") + f"\nПризнак: {reason}")
    return True


def update_streaks(streaks: dict, visited: set, captcha: set, items: list[dict]) -> tuple[dict, Optional[dict]]:
    """Счётчики «капча N запусков подряд» после сессии. -> (новые счётчики, сработавшее правило)."""
    streaks = dict(streaks or {})
    hit = None
    for r in items:
        if r["kind"] != "captcha":
            continue
        site = r["site"]
        if not any(site_matches(h, site) for h in visited):
            continue  # на сайт в этот запуск не заходили — счётчик не трогаем
        seen = any(site_matches(h, site) for h in captcha)
        streaks[site] = streaks.get(site, 0) + 1 if seen else 0
        if seen and streaks[site] >= int(r["value"]) and not hit:
            hit = r
    return streaks, hit


class Watch:
    """Наблюдение за одной сессией профиля: признаки бана и сайты для журнала активности."""

    def __init__(self, manager, pid: int, ctx):
        self.m, self.pid, self.ctx = manager, pid, ctx
        self.t0 = time.time()
        self.hosts: dict[str, int] = {}  # все сайты сессии -> сколько переходов (журнал активности)
        self.visited: set[str] = set()
        self.captcha: set[str] = set()
        self.pending: dict = {}  # страница -> отложенная проверка
        self.done = False
        ctx.on("page", self._page)
        for pg in ctx.pages:
            self._page(pg)
        ctx.on("close", lambda *_: self.finish())

    def _page(self, page):
        page.on("framenavigated", lambda fr, page=page: fr == page.main_frame and self._schedule(page))
        page.on("load", lambda *_, page=page: self._schedule(page, count=False))

    def _schedule(self, page, count: bool = True):
        host = host_of(page.url)
        if count and host and not host.endswith((".test", "localhost")) and page.url.startswith("http"):
            self.hosts[host] = self.hosts.get(host, 0) + 1
        old = self.pending.pop(page, None)
        if old:
            old.cancel()
        self.pending[page] = asyncio.ensure_future(self._check(page))

    async def _check(self, page):
        try:
            await asyncio.sleep(CHECK_DELAY)
            if self.done or page.is_closed() or not settings.get("ban_watch"):
                return
            url = page.url
            host = host_of(url)
            items = [r for r in rules() if site_matches(host, r["site"])]
            if not items:
                return
            self.visited.add(host)
            need_text = any(r["kind"] == "text" for r in items)
            text = None
            if need_text:
                text = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 60000) : ''")
            hit = match(url, text, items)
            if any(r["kind"] == "captcha" for r in items):
                from .captcha import DETECT_JS
                if await page.evaluate(DETECT_JS):
                    self.captcha.add(host)
            if hit:
                mark_ban(self.m, self.pid, describe(hit))
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 — страница закрылась, переход посреди проверки и т.п.
            log.debug("banwatch #%s: %s", self.pid, e)
        finally:
            if self.pending.get(page) is asyncio.current_task():
                self.pending.pop(page, None)

    def finish(self):
        if self.done:
            return
        self.done = True
        for t in self.pending.values():
            t.cancel()
        from . import activity
        secs = time.time() - self.t0
        top = sorted(self.hosts.items(), key=lambda kv: -kv[1])
        activity.log(self.pid, "stop", f"работал {activity.duration(secs)}" +
                     (f", сайты: {', '.join(h.removeprefix('www.') for h, _ in top[:6])}" +
                      (f" и ещё {len(top) - 6}" if len(top) > 6 else "") if top else ""),
                     {"seconds": round(secs), "hosts": dict(top[:50])})
        if not self.visited or not settings.get("ban_watch"):
            return
        p = db.get_profile(self.pid)
        if not p:
            return
        streaks, hit = update_streaks(p.captcha_streak, self.visited, self.captcha, rules())
        if streaks != p.captcha_streak:
            p.captcha_streak = streaks
            db.save_profile(p)
        if hit:
            mark_ban(self.m, self.pid, describe(hit))


def watch(manager, pid: int, sess) -> Optional[Watch]:
    try:
        return Watch(manager, pid, sess.context)
    except Exception as e:  # noqa: BLE001 — наблюдение не должно мешать запуску
        log.warning("banwatch #%s: %s", pid, e)
        return None
