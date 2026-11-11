"""Управление запущенными профилями. Все браузеры живут в одном asyncio-loop в отдельном потоке,
поэтому GUI и API могут вызывать manager.run(coro) из любых потоков."""
import asyncio
import shutil
import threading
import time
from typing import Optional

from . import cookies as ck
from . import db, settings
from .config import PROFILES_DIR
from .engines.base import RunningSession
from .engines.camoufox_engine import CamoufoxEngine, make_camoufox_fingerprint
from .engines.chrome_version import engine_version, playwright_chromium
from .engines.chromium import ChromiumEngine
from .fingerprints.generator import generate
from .models import Profile, Proxy
from .proxy.checker import check_proxy, locale_for


class Manager:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.sessions: dict[int, RunningSession] = {}
        self._starting: set[int] = set()
        self._direct_geo = None  # (время, гео по прямому IP)
        self._engines = {"chromium": ChromiumEngine(), "camoufox": CamoufoxEngine()}
        playwright_chromium()  # прогрев кэша вне asyncio-цикла (sync API Playwright)
        from .sync import Synchronizer
        from .scheduler import Scheduler
        self.sync = Synchronizer(self)
        self.scheduler = Scheduler(self)  # стартует из run.py: работает, пока открыто приложение
        from .recorder import Recorder
        self.recorder = Recorder(self)
        threading.Thread(target=self.loop.run_forever, daemon=True, name="browser-loop").start()

    # ---- вызов из любого потока ----
    def run(self, coro, timeout: Optional[float] = 180):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    @property
    def chrome_version(self) -> str:
        """Версия браузера, который запускается сейчас (Chrome обновляется сам — читается каждый раз)."""
        return engine_version()

    def is_running(self, pid: int) -> bool:
        return pid in self.sessions

    # ---- профили ----
    def create_profile(self, p: Profile) -> Profile:
        self._prepare_fingerprint(p)
        return db.save_profile(p)

    def regenerate_fingerprint(self, p: Profile) -> Profile:
        p.fingerprint = {}
        self._prepare_fingerprint(p)
        return db.save_profile(p)

    @staticmethod
    def _prepare_fingerprint(p: Profile):
        if p.kind == "mobile":
            p.engine = "chromium"  # мобильные профили — только Chromium
            p.os = "android"
        elif p.os == "android":
            p.os = "windows"
        if p.engine == "camoufox" and p.kind == "mobile":
            p.engine = "chromium"
        if not p.fingerprint:
            p.fingerprint = generate(p.kind, p.os, p.device)
        if p.engine == "camoufox" and "camoufox" not in p.fingerprint:
            p.fingerprint["camoufox"] = make_camoufox_fingerprint(p.os)

    def delete_profile(self, pid: int):
        if pid in self.sessions:
            self.run(self.stop(pid))
        db.delete_profile(pid)
        shutil.rmtree(PROFILES_DIR / str(pid), ignore_errors=True)

    # ---- запуск/остановка ----
    async def _resolve_geo(self, p: Profile, proxy: Optional[Proxy]):
        if not p.auto_geo:
            return
        if proxy:
            if not proxy.timezone:
                await check_proxy(proxy, deep=False)
                db.save_proxy(proxy)
            src = proxy
        else:
            # без прокси сайты видят ваш настоящий IP — гео берём по нему (кэш 10 минут)
            if not self._direct_geo or time.time() - self._direct_geo[0] > 600:
                direct = Proxy(type="http", host="direct", port=0)
                await check_proxy(direct, direct=True, deep=False)
                self._direct_geo = (time.time(), direct)
            src = self._direct_geo[1]
        if src.timezone:
            p.timezone = src.timezone
            p.locale = locale_for(src.country)
            p.lat, p.lon = src.lat, src.lon
            db.save_profile(p)

    async def start(self, pid: int, headless: bool = False) -> RunningSession:
        if pid in self.sessions:
            return self.sessions[pid]
        if pid in self._starting:
            raise RuntimeError("Профиль уже запускается")
        p = db.get_profile(pid)
        if not p:
            raise KeyError(f"Профиль {pid} не найден")
        self._starting.add(pid)
        try:
            proxy = db.get_proxy(p.proxy_id)
            if proxy and proxy.change_ip_url and settings.get("change_ip_on_start"):
                await self._rotate(pid, proxy)
            await self._resolve_geo(p, proxy)
            udd = PROFILES_DIR / str(pid)
            udd.mkdir(parents=True, exist_ok=True)
            sess = await self._engines[p.engine].start(p, proxy, str(udd), headless=headless)
            sess.headless = headless
            self.sessions[pid] = sess
            sess.context.on("close", lambda *_: self.sessions.pop(pid, None))
            return sess
        finally:
            self._starting.discard(pid)

    async def _rotate(self, pid: int, proxy: Proxy):
        from .proxy.rotate import busy_with, change_ip
        others = busy_with(proxy.id, self.sessions, pid)
        if others:
            self.scheduler.say(f"#{pid}: IP прокси не меняю — им пользуются запущенные профили "
                               + ", ".join(f"#{o}" for o in others))
            return
        res = await change_ip(proxy, self.scheduler.say)
        self.scheduler.say(f"#{pid}: {res['message']}")
        if res.get("ok") and res.get("new") != res.get("old"):
            proxy.timezone = ""  # гео заново определится по новому IP

    async def stop(self, pid: int):
        sess = self.sessions.pop(pid, None)
        if sess:
            await sess.close()

    async def stop_all(self):
        for pid in list(self.sessions):
            await self.stop(pid)

    # ---- cookies ----
    async def _with_context(self, pid: int):
        if pid in self.sessions:
            return self.sessions[pid], False
        return await self.start(pid, headless=True), True

    async def get_cookies(self, pid: int) -> list[dict]:
        sess, temp = await self._with_context(pid)
        try:
            return await sess.context.cookies()
        finally:
            if temp:
                await self.stop(pid)

    async def set_cookies(self, pid: int, text: str) -> int:
        cookies = ck.parse_cookies(text)
        # сессионные cookies пропали бы при закрытии браузера -> делаем их постоянными (30 дней)
        for c in cookies:
            if "expires" not in c:
                c["expires"] = time.time() + 30 * 86400
        sess, temp = await self._with_context(pid)
        try:
            await sess.context.add_cookies(cookies)
            return len(cookies)
        finally:
            if temp:
                await self.stop(pid)


manager = Manager()
