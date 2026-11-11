"""Синхронизатор: действия в ведущем профиле повторяются в ведомых.

В ведущем профиле скрипт ловит только настоящие действия пользователя (isTrusted): клики, клавиши,
прокрутку — и передаёт их в Python через expose_binding. Для каждого ведомого профиля есть очередь,
действия воспроизводятся через Playwright (это тоже «настоящие» события браузера).
Координаты масштабируются под размер окна ведомого (можно синхронизировать ПК и телефон).
"""
import asyncio
import logging

log = logging.getLogger("claudeanty.sync")

SYNC_JS = r"""(() => {
  if (window.top !== window || window.__caSyncOn) return;
  window.__caSyncOn = true;
  const send = (ev) => { try { if (window.__caSync) window.__caSync(ev); } catch (e) {} };
  addEventListener('click', e => { if (e.isTrusted) send({ t: 'click', x: e.clientX, y: e.clientY, w: innerWidth, h: innerHeight }); }, true);
  addEventListener('keydown', e => { if (e.isTrusted) send({ t: 'key', key: e.key, ctrl: e.ctrlKey, alt: e.altKey, meta: e.metaKey, shift: e.shiftKey }); }, true);
  addEventListener('wheel', e => { if (e.isTrusted) send({ t: 'wheel', dx: e.deltaX, dy: e.deltaY, x: e.clientX, y: e.clientY, w: innerWidth, h: innerHeight }); }, { capture: true, passive: true });
})();"""

_SKIP_KEYS = {"Dead", "Unidentified", "Process", "Shift", "Control", "Alt", "Meta", "CapsLock", "NumLock"}


class Synchronizer:
    def __init__(self, manager):
        self.m = manager
        self.leader: int | None = None
        self.followers: list[int] = []
        self.active = False
        self.replayed = 0
        self.errors = 0
        self._bound: dict[int, object] = {}  # pid -> context, где уже есть binding
        self._queues: dict[int, asyncio.Queue] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    async def start(self, leader: int, followers: list[int]):
        await self.stop()
        for pid in [leader, *followers]:
            if not self.m.is_running(pid):
                await self.m.start(pid)
        ctx = self.m.sessions[leader].context
        if self._bound.get(leader) is not ctx:
            await ctx.expose_binding("__caSync", self._on_event)
            await ctx.add_init_script(SYNC_JS)
            self._bound[leader] = ctx
        for page in ctx.pages:
            try:
                await page.evaluate(SYNC_JS)
            except Exception:  # noqa: BLE001
                pass
        self.leader, self.followers = leader, [f for f in followers if f != leader]
        self.replayed = self.errors = 0
        for f in self.followers:
            q: asyncio.Queue = asyncio.Queue()
            self._queues[f] = q
            self._tasks[f] = asyncio.ensure_future(self._worker(f, q))
        self.active = True

    async def stop(self):
        self.active = False
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()
        self._queues.clear()

    def status(self) -> dict:
        return {"active": self.active, "leader": self.leader, "followers": self.followers,
                "replayed": self.replayed, "errors": self.errors}

    async def _on_event(self, source, ev):
        if not self.active or self.leader is None:
            return
        sess = self.m.sessions.get(self.leader)
        if not sess or source.get("context") is not sess.context:
            return
        try:
            idx = sess.context.pages.index(source["page"])
        except ValueError:
            idx = 0
        for q in self._queues.values():
            q.put_nowait((idx, ev))

    @staticmethod
    def _page(ctx, idx):
        pages = [p for p in ctx.pages if not p.is_closed()]
        if not pages:
            return None
        return pages[idx] if idx < len(pages) else pages[-1]

    async def _worker(self, pid: int, q: asyncio.Queue):
        while True:
            idx, ev = await q.get()
            sess = self.m.sessions.get(pid)
            if not sess:
                continue
            page = self._page(sess.context, idx)
            if not page:
                continue
            try:
                await self._replay(page, ev)
                self.replayed += 1
            except Exception as e:  # noqa: BLE001
                self.errors += 1
                log.debug("sync replay %s: %s", pid, e)

    @staticmethod
    async def _replay(page, ev):
        t = ev["t"]
        if t in ("click", "wheel"):
            # Координаты ведущего — в CSS-пикселях его окна. Переводим в долю окна и обратно в CSS-пиксели
            # окна ведомого (innerWidth/innerHeight). У мобильного Chrome без meta viewport это 980px,
            # а не ширина экрана, — именно в этих координатах Playwright и кликает.
            iw, ih = await page.evaluate("[innerWidth, innerHeight]")
            kx, ky = iw / max(1, ev["w"]), ih / max(1, ev["h"])
            x, y = ev["x"] * kx, ev["y"] * ky
            if t == "click":
                await page.mouse.click(x, y)
            else:
                await page.mouse.move(x, y)
                await page.mouse.wheel(ev["dx"] * kx, ev["dy"] * ky)
        elif t == "key":
            key = ev["key"]
            if key in _SKIP_KEYS:
                return
            key = "Space" if key == " " else key
            mods = [m for m, on in (("Control", ev["ctrl"]), ("Alt", ev["alt"]), ("Meta", ev["meta"])) if on]
            if ev["shift"] and len(key) > 1:
                mods.append("Shift")
            await page.keyboard.press("+".join(mods + [key]))

    async def open_url(self, url: str, pids: list[int]):
        if "://" not in url:
            url = "https://" + url

        async def go(pid):
            sess = self.m.sessions.get(pid)
            page = self._page(sess.context, 0) if sess else None
            if page:
                try:
                    await page.goto(url, wait_until="commit", timeout=30000)
                except Exception:  # noqa: BLE001
                    pass

        await asyncio.gather(*(go(p) for p in pids))
