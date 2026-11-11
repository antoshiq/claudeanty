"""Запись сценария действиями: пользователь работает в профиле, действия превращаются в шаги.

В страницы внедряется скрипт (через evaluate, без init-скрипта и без глобальных функций): он слушает
настоящие действия (isTrusted) и передаёт их в Python через console.debug с меткой — Playwright и так
получает сообщения консоли, а странице не остаётся видимых следов вроде window.__binding.
Скрипт заново внедряется после каждого перехода, пока запись идёт; после остановки — нет.

Секреты в сценарий не записываются: пароль → {{password}}, код 2FA → {{totp}}, логин аккаунта
профиля → {{login}}.
"""
import json
import time
from typing import Optional

from . import db

TAG = "__caRec:"
# Канал событий из страницы в Python:
#  * Patchright — скрипт работает в изолированном мире (DOM и события общие со страницей, JS-переменные — нет),
#    события копятся в очереди этого мира, Python забирает их опросом. Странице не видно ничего;
#  * обычный Playwright — через console.debug (Playwright и так получает консоль; глобальных переменных нет).
SEND_QUEUE = "(ev) => { ev.ts = Date.now(); (window.__caRecQ = window.__caRecQ || []).push(ev); }"
SEND_CONSOLE = "(ev) => { ev.ts = Date.now(); console.debug(TAG + JSON.stringify(ev)); }"
REC_JS = r"""(() => {
  if (window.top !== window) return;
  const send = SEND;
  const okId = (s) => /^[A-Za-z][\w-]*$/.test(s);
  const uniq = (s) => { try { return document.querySelectorAll(s).length === 1; } catch (e) { return false; } };
  const q = (v) => v.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const sel = (el) => {
    if (!(el instanceof Element)) return null;
    el = el.closest('a,button,input,select,textarea,label,summary,[role=button],[role=link],[role=tab],[role=menuitem],[onclick]') || el;
    const tag = el.tagName.toLowerCase();
    if (el.id && okId(el.id) && uniq('#' + el.id)) return '#' + el.id;
    for (const a of ['data-testid', 'data-test', 'data-qa', 'name', 'aria-label', 'placeholder', 'title']) {
      const v = el.getAttribute(a);
      if (v && v.length < 80) { const s = `${tag}[${a}="${q(v)}"]`; if (uniq(s)) return s; }
    }
    if (['a', 'button', 'label', 'summary'].includes(tag) || /button|link|tab|menuitem/.test(el.getAttribute('role') || '')) {
      const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
      if (t && t.length < 60) return `${tag}:has-text("${q(t)}")`;
    }
    const parts = []; let n = el;
    while (n && n.nodeType === 1 && parts.length < 7) {
      if (n.id && okId(n.id) && uniq('#' + n.id)) { parts.unshift('#' + n.id); break; }
      let p = n.tagName.toLowerCase();
      const sib = n.parentElement ? [...n.parentElement.children].filter(c => c.tagName === n.tagName) : [];
      if (sib.length > 1) p += `:nth-of-type(${sib.indexOf(n) + 1})`;
      parts.unshift(p); n = n.parentElement;
    }
    return parts.join(' > ');
  };
  const kind = (el) => {
    const t = (el.type || '').toLowerCase(), meta = `${el.name || ''} ${el.id || ''} ${el.autocomplete || ''}`.toLowerCase();
    if (t === 'password') return 'password';
    if (/one-time-code|otp|totp|2fa|mfa|pin|verification|code/.test(meta)) return 'otp';
    if (t === 'email' || /mail|login|user/.test(meta)) return 'login';
    return 'text';
  };
  const typing = new Set(['text', 'email', 'password', 'search', 'tel', 'url', 'number', '']);
  const isField = (el) => el && ((el.tagName === 'INPUT' && typing.has((el.type || '').toLowerCase())) || el.tagName === 'TEXTAREA' || el.isContentEditable);
  // значение пароля не передаём совсем (console могут перехватывать скрипты сайта), для кода — только «похож на код»
  const typed = (el) => { if (!isField(el) || el.isContentEditable) return; const k = kind(el);
    const v = k === 'password' ? '' : (k === 'otp' && /^\d{6,8}$/.test(el.value.replace(/\s/g, '')) ? '#otp' : el.value);
    send({ t: 'type', sel: sel(el), value: v, kind: k }); };
  // клик фиксируем уже на mousedown: при клике по ссылке переход начинается сразу после mouseup,
  // и событие click могло бы не успеть дойти до Python (документ выгружается)
  let downAt = null;
  addEventListener('mousedown', e => {
    if (!e.isTrusted || e.button !== 0 || isField(e.target)) return;
    downAt = e.target;
    if (isField(document.activeElement)) typed(document.activeElement);  // сначала значение поля, потом клик
    send({ t: 'click', sel: sel(e.target), blank: !!(e.target.closest && e.target.closest('a[target=_blank]')) });
  }, true);
  addEventListener('change', e => { if (e.isTrusted) typed(e.target); }, true);
  addEventListener('keydown', e => {
    if (!e.isTrusted) return;
    if (['Enter', 'Tab', 'Escape'].includes(e.key)) { typed(e.target); send({ t: 'press', key: e.key }); }
  }, true);
  let dy = 0, timer = null;
  addEventListener('wheel', e => {
    if (!e.isTrusted) return;
    dy += e.deltaY; clearTimeout(timer);
    timer = setTimeout(() => { if (Math.abs(dy) >= 100) send({ t: 'scroll', dy: Math.round(dy) }); dy = 0; }, 700);
  }, { capture: true, passive: true });
})();"""


class Recorder:
    def __init__(self, manager):
        self.m = manager
        self.pid: Optional[int] = None
        self.steps: list[dict] = []
        self.active = False
        self._token = ""
        self._last_ts = 0.0
        self._last_action = 0.0
        self._logins: set[str] = set()
        self._handlers: list = []
        self._pages: set = set()

    # ---- управление ----
    @staticmethod
    def _isolated() -> bool:
        from .engines.chromium import driver_name
        return driver_name() == "patchright"

    async def _poll(self):
        """Забирает события из изолированного мира каждой страницы (режим Patchright)."""
        import asyncio
        while self.active:
            for page in list(self._pages):
                if page.is_closed():
                    self._pages.discard(page)
                    continue
                try:
                    evs = await page.evaluate("(window.__caRecQ || []).splice(0)")  # изолированный мир
                except Exception:  # noqa: BLE001 — страница перезагружается
                    continue
                for ev in evs or []:
                    self._event(ev)
            await asyncio.sleep(0.12)

    async def _flush(self, page):
        try:
            for ev in await page.evaluate("(window.__caRecQ || []).splice(0)") or []:
                self._event(ev)
        except Exception:  # noqa: BLE001
            pass

    async def start(self, pid: int):
        await self.stop()
        if not self.m.is_running(pid):
            await self.m.start(pid)
        sess = self.m.sessions[pid]
        self.pid, self.steps, self.active = pid, [], True
        self._pages = set()
        self._token = f"r{int(time.time() * 1000)}"
        self._last_ts = time.time()
        self._logins = {a.login for a in db.list_accounts(pid) if a.login}
        ctx = sess.context
        on_page = lambda page: self._attach(page, new=True)  # noqa: E731
        ctx.on("page", on_page)
        self._handlers = [(ctx, "page", on_page)]
        pages = [p for p in ctx.pages if not p.is_closed()]
        for p in pages:
            self._attach(p, new=False)
        if pages and pages[-1].url.startswith("http"):
            self.steps.append({"type": "open", "url": pages[-1].url})
        for p in pages:
            await self._inject(p)
        if self._isolated():
            import asyncio
            self._poller = asyncio.ensure_future(self._poll())

    async def stop(self) -> list[dict]:
        if self.active and self._isolated():
            for page in list(getattr(self, "_pages", ())):  # последние события до остановки
                try:
                    for ev in await page.evaluate("(window.__caRecQ || []).splice(0)") or []:
                        self._event(ev)
                except Exception:  # noqa: BLE001
                    pass
        self.active = False
        for obj, ev, fn in self._handlers:
            try:
                obj.remove_listener(ev, fn)
            except Exception:  # noqa: BLE001
                pass
        self._handlers = []
        return list(self.steps)

    def status(self) -> dict:
        return {"active": self.active, "pid": self.pid, "steps": len(self.steps)}

    # ---- страницы ----
    def _attach(self, page, new: bool):
        def on_console(msg):
            if self.active and msg.text.startswith(TAG):
                try:
                    self._event(json.loads(msg.text[len(TAG):]))
                except Exception:  # noqa: BLE001
                    pass

        def on_nav(frame):
            if self.active and frame == page.main_frame:
                self._navigated(frame.url)

        def on_dom(_):  # новый документ — скрипт заново (один раз на документ, без флагов на странице)
            if self.active:
                import asyncio
                asyncio.ensure_future(self._inject(page))

        page.on("console", on_console)
        page.on("framenavigated", on_nav)
        page.on("domcontentloaded", on_dom)

        def on_request(req):  # переход начался — забрать события, пока старый документ ещё не выгружен
            if self.active and self._isolated() and req.is_navigation_request() and req.frame == page.main_frame:
                import asyncio
                asyncio.ensure_future(self._flush(page))

        page.on("request", on_request)
        self._handlers.append((page, "request", on_request))
        self._handlers += [(page, "console", on_console), (page, "framenavigated", on_nav), (page, "domcontentloaded", on_dom)]
        if new and self.active:
            if self.steps and self.steps[-1].get("type") == "click" and time.time() - self._last_action < 2:
                self.steps.pop()  # клик открыл вкладку — повторим это шагом «Новая вкладка»
            self.steps.append({"type": "new_tab", "url": ""})
            self._last_action = time.time()

    async def _inject(self, page):
        try:
            if self._isolated():
                self._pages.add(page)
                await page.evaluate(REC_JS.replace("SEND", SEND_QUEUE))  # Patchright: изолированный мир
            else:
                await page.evaluate(REC_JS.replace("SEND", SEND_CONSOLE).replace("TAG", json.dumps(TAG)))
        except Exception:  # noqa: BLE001 — страница ещё грузится или закрыта
            pass

    def _navigated(self, url: str):
        if not url.startswith("http"):
            return
        if self.steps and self.steps[-1].get("type") == "new_tab" and not self.steps[-1].get("url"):
            self.steps[-1]["url"] = url
            return
        last = self.steps[-1].get("type") if self.steps else ""
        if last in ("click", "press") and time.time() - self._last_action < 4:
            return  # переход вызван кликом или Enter — его повторит сам шаг
        self._gap()
        self.steps.append({"type": "open", "url": url})
        self._last_action = time.time()

    # ---- события ----
    def _gap(self):
        now = time.time()
        gap = now - self._last_ts
        if self.steps and gap > 2.5:
            g = min(gap, 30)
            self.steps.append({"type": "wait", "min": round(g * 0.7, 1), "max": round(g * 1.1, 1)})
        self._last_ts = now

    def _event(self, ev: dict):
        t = ev.get("t")
        if t == "type":
            value = ev.get("value", "")
            kind = ev.get("kind")
            if kind == "password":
                value = "{{password}}"
            elif value == "#otp":
                value = "{{totp}}"
            elif value in self._logins:
                value = "{{login}}"
            last = self.steps[-1] if self.steps else {}
            if last.get("type") == "type" and last.get("selector") == ev.get("sel"):
                last["text"] = value  # то же поле — оставляем последнее значение
                return
            if any(s.get("type") == "type" and s.get("selector") == ev.get("sel") and s.get("text") == value
                   for s in self.steps[-3:]):
                return  # повтор того же значения (change после потери фокуса)
            self._gap()
            self.steps.append({"type": "type", "selector": ev.get("sel"), "text": value, "clear": True})
        elif t == "click":
            self._gap()
            self.steps.append({"type": "click", "selector": ev.get("sel"), "optional": False})
        elif t == "press":
            self._gap()
            self.steps.append({"type": "press", "key": ev.get("key", "Enter")})
        elif t == "scroll":
            dy = int(ev.get("dy", 0))
            last = self.steps[-1] if self.steps else {}
            if last.get("type") == "scroll" and (last["direction"] == "down") == (dy > 0) and time.time() - self._last_ts < 3:
                last["amount"] += abs(dy)
            else:
                self._gap()
                self.steps.append({"type": "scroll", "direction": "down" if dy > 0 else "up", "amount": abs(dy), "times": 1})
        else:
            return
        self._last_action = time.time()
        self._last_ts = time.time()
