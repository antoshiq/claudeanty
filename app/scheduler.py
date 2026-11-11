"""Планировщик: «прогреть эти профили каждый день в 9:00», «запустить сценарий по будням в 10:30»,
«резервная копия раз в 6 часов». Работает, пока открыт claudeAnty (в цикле браузеров manager).

Задача — документ в базе (таблица tasks):
  {"id", "name", "enabled", "action", "scenario_id", "headless",
   "targets": {"mode": "ids|folder|tag|all", "ids": [...], "value": "..."},
   "schedule": {"kind": "daily|interval|once", "time": "09:00", "days": [0..6], "every": 60, "at": "2026-11-12 09:00"},
   "next_run", "last_run", "last_result"}

Пропущенные запуски (приложение было закрыто) не догоняются, кроме опоздания не больше чем на
GRACE минут — например, открыли claudeAnty в 9:05, а прогрев стоял на 9:00.
"""
import asyncio
import datetime as dt
import logging
import time
from collections import deque
from typing import Optional

from . import db

log = logging.getLogger("claudeanty.scheduler")

ACTIONS = {
    "warmup": "Прогреть cookies",
    "scenario": "Выполнить сценарий",
    "check": "Проверить отпечаток",
    "external": "Отчёт внешних детекторов",
    "start": "Запустить профили",
    "stop": "Остановить профили",
    "backup": "Резервная копия всех профилей",
}
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
GRACE = 30  # минут
TICK = 15  # секунд


def _hm(s: str) -> tuple[int, int]:
    h, _, m = (s or "09:00").partition(":")
    return max(0, min(23, int(h or 0))), max(0, min(59, int(m or 0)))


def next_run(task: dict, after: float) -> Optional[float]:
    """Ближайший запуск строго позже момента after (epoch); None — больше не запускать."""
    sch = task.get("schedule") or {}
    kind = sch.get("kind", "daily")
    if kind == "interval":
        every = max(1, int(sch.get("every", 60))) * 60
        last = task.get("last_run") or 0
        return max(after + 1, last + every) if last else after + 1
    if kind == "once":
        try:
            t = dt.datetime.strptime(sch.get("at", ""), "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            return None
        return t if t > after and not task.get("last_run") else None
    h, m = _hm(sch.get("time"))
    days = set(sch.get("days") or range(7))
    base = dt.datetime.fromtimestamp(after)
    for add in range(8):
        d = (base + dt.timedelta(days=add)).replace(hour=h, minute=m, second=0, microsecond=0)
        if d.timestamp() > after and d.weekday() in days:
            return d.timestamp()
    return None


def describe_schedule(task: dict) -> str:
    sch = task.get("schedule") or {}
    kind = sch.get("kind", "daily")
    if kind == "interval":
        every = int(sch.get("every", 60))
        return f"каждые {every // 60} ч {every % 60} мин".replace(" 0 мин", "") if every >= 60 else f"каждые {every} мин"
    if kind == "once":
        return f"однократно {sch.get('at', '?')}"
    days = sorted(set(sch.get("days") or range(7)))
    when = "каждый день" if len(days) == 7 else "по будням" if days == [0, 1, 2, 3, 4] else \
        ", ".join(WEEKDAYS[d] for d in days)
    return f"{when} в {sch.get('time', '09:00')}"


def resolve_targets(task: dict) -> list[int]:
    t = task.get("targets") or {}
    mode = t.get("mode", "ids")
    profiles = db.list_profiles()
    if mode == "all":
        return [p.id for p in profiles]
    if mode == "folder":
        return [p.id for p in profiles if p.folder == t.get("value")]
    if mode == "tag":
        return [p.id for p in profiles if t.get("value") in p.tags]
    alive = {p.id for p in profiles}
    return [i for i in t.get("ids", []) if i in alive]


def describe_targets(task: dict) -> str:
    t = task.get("targets") or {}
    mode = t.get("mode", "ids")
    if task.get("action") == "backup":
        return "все профили"
    if mode == "all":
        return "все профили"
    if mode == "folder":
        return f"папка «{t.get('value')}»"
    if mode == "tag":
        return f"тег «{t.get('value')}»"
    return f"профилей: {len(resolve_targets(task))}"


class Scheduler:
    def __init__(self, manager):
        self.m = manager
        self.events: deque = deque(maxlen=500)  # (номер, текст) — GUI забирает новые строки
        self._seq = 0
        self.running: set[int] = set()
        self._task: Optional[asyncio.Task] = None

    def say(self, text: str):
        self._seq += 1
        self.events.append((self._seq, text))
        log.info(text)

    # ---- жизненный цикл ----
    def start(self):
        """Вызывается из любого потока: запускает цикл в loop менеджера."""
        def go():
            if not self._task:
                self._task = asyncio.ensure_future(self._loop())
        self.m.loop.call_soon_threadsafe(go)

    async def _loop(self):
        now = time.time()
        for t in db.list_docs("tasks"):  # при запуске: давно пропущенное не догоняем, недавнее (≤ GRACE) — да
            nr = t.get("next_run")
            if t.get("enabled", True) and (not nr or nr < now - GRACE * 60):
                t["next_run"] = next_run(t, now)
                db.save_doc("tasks", t)
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick")
            await asyncio.sleep(TICK)

    async def tick(self):
        now = time.time()
        for t in db.list_docs("tasks"):
            if not t.get("enabled", True) or t["id"] in self.running:
                continue
            nr = t.get("next_run")
            if nr is None:
                nr = next_run(t, now)
                t["next_run"] = nr
                db.save_doc("tasks", t)
            if nr and nr <= now:
                asyncio.ensure_future(self.run_task(t["id"]))

    # ---- выполнение ----
    async def run_task(self, tid: int, manual: bool = False) -> str:
        t = db.get_doc("tasks", tid)
        if not t or tid in self.running:
            return "уже выполняется" if t else "задача не найдена"
        self.running.add(tid)
        started = time.time()
        if not manual:  # сразу назначаем следующий запуск, чтобы не стартовать дважды
            t["next_run"] = next_run({**t, "last_run": started}, started)
            db.save_doc("tasks", t)
        self.say(f"Планировщик: «{t['name']}» — {ACTIONS.get(t['action'], t['action'])}…")
        try:
            result = await self._do(t)
        except Exception as e:  # noqa: BLE001
            result = f"ошибка: {str(e).splitlines()[0][:200]}"
        finally:
            self.running.discard(tid)
        t = db.get_doc("tasks", tid) or t
        t["last_run"] = started
        t["last_result"] = result
        if (t.get("schedule") or {}).get("kind") == "once" and not manual:
            t["enabled"] = False
        t["next_run"] = next_run(t, time.time()) if t.get("enabled", True) else None
        db.save_doc("tasks", t)
        self.say(f"Планировщик: «{t['name']}» — {result}")
        return result

    async def _do(self, t: dict) -> str:
        action = t["action"]
        if action == "backup":
            from .backup import backup_all
            r = await backup_all(self.m, progress=self.say)
            return f"копия {r['size'] / 1e6:.1f} МБ, профилей {r['profiles']}"
        pids = resolve_targets(t)
        if not pids:
            return "нет профилей для задачи"
        if action == "scenario":
            from .rpa import jobs
            res = await jobs.run_many(self.m, int(t.get("scenario_id") or 0), pids, headless=bool(t.get("headless", True)),
                                      log=self.say)
            ok = sum(1 for r in res if r.get("ok"))
            return f"сценарий выполнен: {ok} из {len(res)}" + (f", ошибок {len(res) - ok}" if ok < len(res) else "")
        if action == "warmup":
            from .warmup import warmup
            res = await asyncio.gather(*(warmup(self.m, pid, progress=self.say) for pid in pids), return_exceptions=True)
            ok = sum(1 for r in res if isinstance(r, dict))
            return f"прогрето {ok} из {len(pids)}"
        if action == "check":
            from .check import run_check
            fails = 0
            for pid in pids:
                r = await run_check(self.m, pid)
                fails += 1 if r["fail"] else 0
            return f"проверено {len(pids)}, с проблемами {fails}"
        if action == "external":
            from .external_check import run_external
            fails = 0
            for pid in pids:
                r = await run_external(self.m, pid, progress=self.say)
                fails += 1 if r["fail"] else 0
            return f"отчёты готовы: {len(pids)}, с ✗: {fails}"
        if action == "start":
            headless = bool(t.get("headless", False))
            for pid in pids:
                if not self.m.is_running(pid):
                    await self.m.start(pid, headless=headless)
            return f"запущено {len(pids)}"
        if action == "stop":
            for pid in pids:
                await self.m.stop(pid)
            return f"остановлено {len(pids)}"
        return f"неизвестное действие {action}"
