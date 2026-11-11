"""Сценарии без кода (RPA): список шагов «открыть / кликнуть / ввести / подождать / прокрутить / повторить».

Сценарий — документ в базе: {"id", "name", "steps": [{"type": ..., параметры}]}.
Блоки: «repeat» … «end» (повторить N раз) и «if» … «end» (выполнить, если элемент есть / нет). Блоки
можно вкладывать. Действия выполняются как у человека: мышь подводится к элементу, текст печатается
с паузами между символами.

В тексте шагов работают подстановки: {{login}}, {{password}}, {{totp}} — из «Пароли и 2FA» профиля
(первый аккаунт; {{password:Google}} — аккаунт с сервисом Google), {{name}}, {{id}} — профиль,
{{random:6}} — случайные цифры.
"""
import asyncio
import random
import re
import time
from typing import Callable, Optional

from . import db, settings, totp
from .config import DATA_DIR

# тип шага -> (название, [(поле, подпись, вид, по умолчанию)]); вид: text | int | float | bool | choice:a,b
STEP_TYPES: dict[str, tuple[str, list]] = {
    "open": ("Открыть адрес", [("url", "Адрес", "text", "https://")]),
    "new_tab": ("Новая вкладка", [("url", "Адрес (необязательно)", "text", "")]),
    "click": ("Кликнуть", [("selector", "Элемент", "text", ""), ("optional", "Пропустить, если нет", "bool", False)]),
    "type": ("Ввести текст", [("selector", "Поле", "text", ""), ("text", "Текст", "text", ""),
                              ("clear", "Очистить поле", "bool", True)]),
    "press": ("Нажать клавишу", [("key", "Клавиша", "text", "Enter")]),
    "wait": ("Подождать", [("min", "От, сек", "float", 1.0), ("max", "До, сек", "float", 3.0)]),
    "wait_for": ("Ждать элемент", [("selector", "Элемент", "text", ""), ("timeout", "Не дольше, сек", "int", 30)]),
    "scroll": ("Прокрутить", [("direction", "Куда", "choice:down,up", "down"), ("amount", "Пикселей", "int", 800),
                              ("times", "Раз", "int", 1)]),
    "screenshot": ("Скриншот", [("name", "Имя файла (необязательно)", "text", "")]),
    "repeat": ("Повторить N раз ↓", [("times", "Сколько раз", "int", 3)]),
    "if": ("Если элемент… ↓", [("selector", "Элемент", "text", ""), ("mode", "Условие", "choice:exists,missing", "exists")]),
    "end": ("Конец блока", []),
}
CHOICE_LABELS = {"down": "вниз", "up": "вверх", "exists": "есть на странице", "missing": "нет на странице"}
BLOCKS = ("repeat", "if")
SELECTOR_HINT = ("Элемент — CSS-селектор (#login, .btn, input[name=email]), текст кнопки (text=Войти) "
                 "или XPath (xpath=//button[1]).")
SHOTS_DIR = DATA_DIR / "screenshots"


def describe(step: dict) -> str:
    """Короткое описание шага для списка в редакторе."""
    t = step.get("type")
    title = STEP_TYPES.get(t, (t, []))[0]
    if t in ("open", "new_tab"):
        return f"{title}: {step.get('url') or '—'}"
    if t == "click":
        return f"{title}: {step.get('selector')}" + (" (если есть)" if step.get("optional") else "")
    if t == "type":
        return f"{title}: «{step.get('text', '')}» → {step.get('selector')}"
    if t == "press":
        return f"{title}: {step.get('key')}"
    if t == "wait":
        return f"{title} {step.get('min')}–{step.get('max')} с"
    if t == "wait_for":
        return f"{title}: {step.get('selector')} (до {step.get('timeout')} с)"
    if t == "scroll":
        return f"{title} {CHOICE_LABELS.get(step.get('direction'), '')} на {step.get('amount')} px × {step.get('times')}"
    if t == "screenshot":
        return title + (f": {step['name']}" if step.get("name") else "")
    if t == "repeat":
        return f"Повторить (раз: {step.get('times')})"
    if t == "if":
        return f"Если {step.get('selector')} {CHOICE_LABELS.get(step.get('mode'), '')}"
    return title


def default_step(t: str) -> dict:
    return {"type": t, **{k: d for k, _, _, d in STEP_TYPES[t][1]}}


def validate(steps: list[dict]) -> Optional[str]:
    depth = 0
    for i, s in enumerate(steps, 1):
        if s.get("type") not in STEP_TYPES:
            return f"шаг {i}: неизвестный тип"
        if s["type"] in BLOCKS:
            depth += 1
        elif s["type"] == "end":
            depth -= 1
            if depth < 0:
                return f"шаг {i}: «Конец блока» без начала"
        if s["type"] in ("click", "type", "wait_for", "if") and not str(s.get("selector", "")).strip():
            return f"шаг {i}: не указан элемент"
        if s["type"] == "open" and not str(s.get("url", "")).strip():
            return f"шаг {i}: не указан адрес"
    if depth:
        return "не хватает «Конец блока» для повтора или условия"
    return None


def _tree(steps: list[dict]) -> list:
    """Плоский список -> дерево: блок = (шаг, [вложенные])."""
    root: list = []
    stack = [root]
    for s in steps:
        if s["type"] in BLOCKS:
            node = (s, [])
            stack[-1].append(node)
            stack.append(node[1])
        elif s["type"] == "end":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append((s, None))
    return root


# ---------------- подстановки ----------------
def make_vars(pid: int) -> Callable[[str], str]:
    p = db.get_profile(pid)
    accs = db.list_accounts(pid)

    def acc_for(service: str):
        if service:
            for a in accs:
                if a.service.lower() == service.lower():
                    return a
            raise ValueError(f"в профиле нет аккаунта «{service}» (Пароли и 2FA)")
        if not accs:
            raise ValueError("в профиле нет аккаунтов (Пароли и 2FA)")
        return accs[0]

    def repl(m: re.Match) -> str:
        name, _, arg = m.group(1).strip().partition(":")
        name = name.lower()
        if name in ("login", "password", "totp"):
            a = acc_for(arg.strip())
            if name == "totp":
                if not a.totp:
                    raise ValueError(f"у аккаунта «{a.service or a.login}» нет секрета 2FA")
                left = totp.remaining(a.totp)
                # код сменится, пока печатаем, — берём следующий (серверы принимают соседнее окно)
                return totp.code(a.totp, time.time() + left) if left < 4 else totp.code(a.totp)
            return getattr(a, name)
        if name == "name":
            return p.name if p else ""
        if name == "id":
            return str(pid)
        if name == "random":
            n = int(arg or 6)
            return "".join(random.choice("0123456789") for _ in range(n))
        return m.group(0)

    return lambda text: re.sub(r"\{\{([^}]+)\}\}", repl, text or "")


# ---------------- выполнение ----------------
class Stop(Exception):
    pass


async def _human_move(page, loc):
    try:
        box = await loc.bounding_box(timeout=5000)
    except Exception:  # noqa: BLE001
        box = None
    if box:
        x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
        await page.mouse.move(x, y, steps=random.randint(8, 20))
        await asyncio.sleep(random.uniform(0.05, 0.25))


class Runner:
    def __init__(self, ctx, pid: int, log: Callable[[str], None]):
        self.ctx, self.pid, self.log = ctx, pid, log
        self.page = None
        self.sub = make_vars(pid)
        self.done = 0

    async def current(self):
        if self.page is None or self.page.is_closed():
            pages = [pg for pg in self.ctx.pages if not pg.is_closed()]
            self.page = pages[-1] if pages else await self.ctx.new_page()
        return self.page

    async def run(self, nodes: list):
        for step, children in nodes:
            t = step["type"]
            if t == "repeat":
                for _ in range(max(0, int(step.get("times", 1)))):
                    await self.run(children)
                continue
            if t == "if":
                page = await self.current()
                n = await page.locator(step["selector"]).count()
                if (n > 0) == (step.get("mode", "exists") == "exists"):
                    await self.run(children)
                continue
            await self.step(step)
            self.done += 1

    async def step(self, s: dict):
        t = s["type"]
        page = await self.current()
        if t == "open":
            url = self.sub(s["url"]).strip()
            await page.goto(url if "://" in url else "https://" + url, wait_until="domcontentloaded", timeout=60000)
        elif t == "new_tab":
            self.page = page = await self.ctx.new_page()
            if s.get("url"):
                url = self.sub(s["url"]).strip()
                await page.goto(url if "://" in url else "https://" + url, wait_until="domcontentloaded", timeout=60000)
        elif t == "click":
            loc = page.locator(s["selector"]).first
            try:
                await loc.wait_for(state="visible", timeout=15000 if not s.get("optional") else 3000)
            except Exception:  # noqa: BLE001
                if s.get("optional"):
                    return
                raise
            await _human_move(page, loc)
            await loc.click(delay=random.randint(40, 140))
        elif t == "type":
            loc = page.locator(s["selector"]).first
            await loc.wait_for(state="visible", timeout=15000)
            await _human_move(page, loc)
            await loc.click()
            if s.get("clear", True):
                await loc.fill("")
            await loc.press_sequentially(self.sub(s.get("text", "")), delay=random.randint(60, 160))
        elif t == "press":
            await page.keyboard.press(s.get("key") or "Enter")
        elif t == "wait":
            lo = float(s.get("min", 1))
            hi = float(s.get("max", lo))
            await asyncio.sleep(random.uniform(min(lo, hi), max(lo, hi)))
        elif t == "wait_for":
            await page.locator(s["selector"]).first.wait_for(state="visible", timeout=int(s.get("timeout", 30)) * 1000)
        elif t == "scroll":
            sign = -1 if s.get("direction") == "up" else 1
            for _ in range(max(1, int(s.get("times", 1)))):
                total, left = int(s.get("amount", 800)), int(s.get("amount", 800))
                while left > 0:  # прокрутка несколькими «щелчками колеса», как рукой
                    d = min(left, random.randint(90, 160))
                    await page.mouse.wheel(0, sign * d)
                    left -= d
                    await asyncio.sleep(random.uniform(0.03, 0.12))
                await asyncio.sleep(random.uniform(0.4, 1.2) if total else 0)
        elif t == "screenshot":
            SHOTS_DIR.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^\w.-]+", "_", self.sub(s.get("name") or "")) or "shot"
            path = SHOTS_DIR / f"{self.pid}_{name}_{time.strftime('%Y%m%d_%H%M%S')}.png"
            await page.screenshot(path=str(path))
            self.log(f"#{self.pid}: скриншот {path}")


async def run_scenario(manager, scenario: dict, pid: int, headless: bool = False, close_after: bool = True,
                       log: Callable[[str], None] = lambda s: None) -> dict:
    err = validate(scenario.get("steps", []))
    if err:
        raise ValueError(f"Сценарий «{scenario.get('name')}»: {err}")
    was_running = manager.is_running(pid)
    sess = manager.sessions[pid] if was_running else await manager.start(pid, headless=headless)
    runner = Runner(sess.context, pid, log)
    t0 = time.time()
    try:
        await runner.run(_tree(scenario["steps"]))
        return {"pid": pid, "ok": True, "steps": runner.done, "seconds": round(time.time() - t0, 1)}
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0][:300]
        return {"pid": pid, "ok": False, "steps": runner.done, "error": msg, "seconds": round(time.time() - t0, 1)}
    finally:
        if close_after and not was_running:
            await manager.stop(pid)


class Jobs:
    """Запущенные сценарии (чтобы их можно было остановить из интерфейса)."""

    def __init__(self):
        self.running: dict[int, dict] = {}  # job id -> {"name", "pids", "task", "done", "total"}
        self._next = 1

    async def run_many(self, manager, scenario_id: int, pids: list[int], headless: bool = False,
                       close_after: bool = True, log: Callable[[str], None] = lambda s: None) -> list[dict]:
        sc = db.get_doc("scenarios", scenario_id)
        if not sc:
            raise KeyError(f"сценарий {scenario_id} не найден")
        jid = self._next
        self._next += 1
        job = {"id": jid, "name": sc["name"], "pids": pids, "done": 0, "total": len(pids), "tasks": set()}
        self.running[jid] = job
        sem = asyncio.Semaphore(max(1, int(settings.get("rpa_parallel"))))

        async def one(pid):
            async with sem:
                log(f"Сценарий «{sc['name']}» → профиль #{pid}…")
                r = await run_scenario(manager, sc, pid, headless, close_after, log)
                job["done"] += 1
                log(f"Сценарий «{sc['name']}» #{pid}: " + (f"готово, шагов {r['steps']}, {r['seconds']} с" if r["ok"]
                    else f"ошибка на шаге {r['steps'] + 1}: {r['error']}"))
                return r

        try:
            tasks = [asyncio.ensure_future(one(pid)) for pid in pids]
            job["tasks"] = set(tasks)
            res = await asyncio.gather(*tasks, return_exceptions=True)
            return [r if isinstance(r, dict) else {"pid": pid, "ok": False, "error": "остановлено" if
                    isinstance(r, asyncio.CancelledError) else str(r)} for pid, r in zip(pids, res)]
        finally:
            self.running.pop(jid, None)

    def stop_all(self):
        for job in list(self.running.values()):
            for t in job["tasks"]:
                t.cancel()


jobs = Jobs()
