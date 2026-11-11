"""Сценарии без кода (RPA): список шагов «открыть / кликнуть / ввести / подождать / прокрутить / повторить».

Сценарий — документ в базе: {"id", "name", "steps": [{"type": ..., параметры}]}.
Блоки: «repeat» … «end» (повторить N раз) и «if» … «end» (выполнить, если элемент есть / нет). Блоки
можно вкладывать. Действия выполняются как у человека: мышь подводится к элементу, текст печатается
с паузами между символами.

В тексте шагов работают подстановки: {{login}}, {{password}}, {{totp}} — из «Пароли и 2FA» профиля
(первый аккаунт; {{password:Google}} — аккаунт с сервисом Google), {{name}}, {{id}} — профиль,
{{random:6}} — случайные цифры, {{date}} — сегодняшняя дата, {{имя}} — колонка таблицы данных
сценария или переменная из шагов «Извлечь» / «Задать переменную».

Таблица данных (CSV) у сценария: строка на профиль — по колонке profile (название или номер профиля)
или по порядку запуска. Извлечённые значения сохраняются в data/rpa_results/<сценарий>_<время>.csv.
"""
import asyncio
import csv
import io
import random
import re
import time
from typing import Callable, Optional

from . import db, human, settings, totp
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
    "extract": ("Извлечь в переменную", [("selector", "Элемент", "text", ""),
                                          ("attr", "Что взять", "choice:text,value,href,src,title", "text"),
                                          ("var", "Имя переменной", "text", "result"),
                                          ("regex", "Регулярное выражение (необязательно)", "text", "")]),
    "set": ("Задать переменную", [("var", "Имя переменной", "text", ""), ("value", "Значение", "text", "")]),
    "captcha": ("Решить капчу", [("optional", "Пропустить, если капчи нет", "bool", True)]),
    "repeat": ("Повторить N раз ↓", [("times", "Сколько раз", "int", 3)]),
    "if": ("Если… ↓", [("mode", "Условие", "choice:exists,missing,url_contains,url_not_contains,text_contains,var_equals",
                        "exists"), ("selector", "Элемент (для «есть / нет»)", "text", ""),
                       ("value", "Значение (адрес, текст или имя=значение)", "text", "")]),
    "end": ("Конец блока", []),
}
CHOICE_LABELS = {"down": "вниз", "up": "вверх", "exists": "элемент есть на странице", "missing": "элемента нет на странице",
                 "url_contains": "адрес содержит", "url_not_contains": "адрес не содержит",
                 "text_contains": "на странице есть текст", "var_equals": "переменная равна (имя=значение)",
                 "text": "текст", "value": "значение поля", "href": "ссылку (href)", "src": "src", "title": "title"}
_SELECTOR_MODES = ("exists", "missing")
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
        mode = step.get("mode", "exists")
        if mode in _SELECTOR_MODES:
            return f"Если {step.get('selector')}: {CHOICE_LABELS.get(mode, '')}"
        return f"Если {CHOICE_LABELS.get(mode, mode)} «{step.get('value', '')}»"
    if t == "extract":
        return f"{title}: {{{{{step.get('var')}}}}} ← {CHOICE_LABELS.get(step.get('attr'), '')} {step.get('selector')}"
    if t == "set":
        return f"{title}: {{{{{step.get('var')}}}}} = «{step.get('value', '')}»"
    if t == "captcha":
        return title + (" (если есть)" if step.get("optional", True) else "")
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
        need_sel = s["type"] in ("click", "type", "wait_for", "extract") or \
            (s["type"] == "if" and s.get("mode", "exists") in _SELECTOR_MODES)
        if need_sel and not str(s.get("selector", "")).strip():
            return f"шаг {i}: не указан элемент"
        if s["type"] == "if" and s.get("mode", "exists") not in _SELECTOR_MODES and not str(s.get("value", "")).strip():
            return f"шаг {i}: не указано значение условия"
        if s["type"] in ("extract", "set") and not re.fullmatch(r"[\w-]+", str(s.get("var", "")).strip()):
            return f"шаг {i}: имя переменной — латиница, цифры, _"
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
def make_vars(pid: int, variables: Optional[dict] = None) -> Callable[[str], str]:
    """Функция подстановки. variables — колонки таблицы данных и извлечённые значения (меняется на ходу)."""
    p = db.get_profile(pid)
    variables = variables if variables is not None else {}
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
        if name == "date":
            return time.strftime(arg or "%Y-%m-%d")
        low = {k.lower(): v for k, v in variables.items()}
        key = m.group(1).strip().lower()
        if key in low:
            return str(low[key])
        raise ValueError(f"нет переменной {{{{{m.group(1).strip()}}}}} — добавьте колонку в таблицу данных или шаг «Извлечь»")

    return lambda text: re.sub(r"\{\{([^}]+)\}\}", repl, text or "")


# ---------------- выполнение ----------------
class Stop(Exception):
    pass


class Runner:
    def __init__(self, ctx, pid: int, log: Callable[[str], None], variables: Optional[dict] = None):
        self.ctx, self.pid, self.log = ctx, pid, log
        self.page = None
        self.vars: dict = dict(variables or {})
        self.extracted: dict = {}
        self.sub = make_vars(pid, self.vars)
        self.done = 0

    async def check(self, step: dict) -> bool:
        page = await self.current()
        mode = step.get("mode", "exists")
        val = self.sub(step.get("value", ""))
        if mode in _SELECTOR_MODES:
            n = await page.locator(step["selector"]).count()
            return (n > 0) == (mode == "exists")
        if mode == "url_contains":
            return val.lower() in page.url.lower()
        if mode == "url_not_contains":
            return val.lower() not in page.url.lower()
        if mode == "text_contains":
            return val.lower() in (await page.evaluate("document.body ? document.body.innerText : ''")).lower()
        if mode == "var_equals":
            name, _, want = val.partition("=")
            have = {k.lower(): v for k, v in self.vars.items()}.get(name.strip().lower(), "")
            return str(have).strip() == want.strip()
        return False

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
                if await self.check(step):
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
            await human.click(page, loc)
        elif t == "type":
            loc = page.locator(s["selector"]).first
            await loc.wait_for(state="visible", timeout=15000)
            await human.click(page, loc)
            if s.get("clear", True):
                await loc.fill("")
            await human.type_text(page, self.sub(s.get("text", "")))
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
                await human.scroll(page, sign * int(s.get("amount", 800)))
                await asyncio.sleep(random.uniform(0.4, 1.2))
        elif t == "extract":
            loc = page.locator(s["selector"]).first
            await loc.wait_for(state="attached", timeout=15000)
            attr = s.get("attr", "text")
            if attr == "text":
                val = (await loc.inner_text()).strip()
            elif attr == "value":
                val = await loc.input_value()
            else:
                val = await loc.get_attribute(attr) or ""
            if s.get("regex"):
                m = re.search(s["regex"], val)
                val = (m.group(1) if m and m.groups() else m.group(0)) if m else ""
            val = val.strip()
            self.vars[s["var"].strip()] = self.extracted[s["var"].strip()] = val
            self.log(f"#{self.pid}: {{{{{s['var']}}}}} = «{val[:80]}»")
        elif t == "set":
            self.vars[s["var"].strip()] = self.extracted[s["var"].strip()] = self.sub(s.get("value", ""))
        elif t == "captcha":
            from .captcha import solve_on_page
            res = await solve_on_page(page, lambda m: self.log(f"#{self.pid}: {m}"))
            if res.startswith("капчи на странице нет") and not s.get("optional", True):
                raise RuntimeError("капча не найдена на странице")
            self.log(f"#{self.pid}: {res}")
        elif t == "screenshot":
            SHOTS_DIR.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^\w.-]+", "_", self.sub(s.get("name") or "")) or "shot"
            path = SHOTS_DIR / f"{self.pid}_{name}_{time.strftime('%Y%m%d_%H%M%S')}.png"
            await page.screenshot(path=str(path))
            self.log(f"#{self.pid}: скриншот {path}")


async def run_scenario(manager, scenario: dict, pid: int, headless: bool = False, close_after: bool = True,
                       log: Callable[[str], None] = lambda s: None, variables: Optional[dict] = None) -> dict:
    err = validate(scenario.get("steps", []))
    if err:
        raise ValueError(f"Сценарий «{scenario.get('name')}»: {err}")
    was_running = manager.is_running(pid)
    sess = manager.sessions[pid] if was_running else await manager.start(pid, headless=headless)
    runner = Runner(sess.context, pid, log, variables)
    t0 = time.time()
    try:
        await runner.run(_tree(scenario["steps"]))
        return {"pid": pid, "ok": True, "steps": runner.done, "seconds": round(time.time() - t0, 1),
                "vars": runner.extracted}
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0][:300]
        return {"pid": pid, "ok": False, "steps": runner.done, "error": msg, "seconds": round(time.time() - t0, 1),
                "vars": runner.extracted}
    finally:
        if close_after and not was_running:
            await manager.stop(pid)


RESULTS_DIR = DATA_DIR / "rpa_results"


def parse_data(text: str) -> list[dict]:
    """CSV (разделитель , ; или табуляция) -> список строк-словарей."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items() if k} for r in
            csv.DictReader(io.StringIO(text), dialect=dialect)]


def rows_for(pids: list[int], data: list[dict]) -> dict[int, dict]:
    """Строка данных для каждого профиля: по колонке profile/id (номер или название), иначе по порядку."""
    if not data:
        return {}
    key = next((k for k in data[0] if k.lower() in ("profile", "id", "профиль")), None)
    out: dict[int, dict] = {}
    if key:
        names = {pid: (db.get_profile(pid).name if db.get_profile(pid) else "") for pid in pids}
        for row in data:
            ref = str(row.get(key, "")).strip().lstrip("#")
            for pid in pids:
                if ref == str(pid) or (ref and ref == names[pid]):
                    out[pid] = row
        return out
    return {pid: data[i] for i, pid in enumerate(pids) if i < len(data)}


def save_results(sc: dict, results: list[dict]) -> Optional[str]:
    names = sorted({k for r in results for k in (r.get("vars") or {})})
    if not names:
        return None
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w-]+", "_", sc.get("name", "scenario"))[:40]
    path = RESULTS_DIR / f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["profile", "name", "ok", "error", *names])
        for r in results:
            p = db.get_profile(r["pid"])
            w.writerow([r["pid"], p.name if p else "", "да" if r.get("ok") else "нет", r.get("error", ""),
                        *[(r.get("vars") or {}).get(n, "") for n in names]])
    return str(path)


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
        data = rows_for(pids, parse_data(sc.get("data", "")))

        async def one(pid):
            async with sem:
                log(f"Сценарий «{sc['name']}» → профиль #{pid}…")
                r = await run_scenario(manager, sc, pid, headless, close_after, log, data.get(pid))
                job["done"] += 1
                if not r["ok"]:
                    from .notify import notify
                    p = db.get_profile(pid)
                    notify("scenario_error", f"«{sc['name']}», профиль #{pid} {p.name if p else ''}: "
                                             f"шаг {r['steps'] + 1} — {r.get('error', '')}")
                log(f"Сценарий «{sc['name']}» #{pid}: " + (f"готово, шагов {r['steps']}, {r['seconds']} с" if r["ok"]
                    else f"ошибка на шаге {r['steps'] + 1}: {r['error']}"))
                return r

        try:
            tasks = [asyncio.ensure_future(one(pid)) for pid in pids]
            job["tasks"] = set(tasks)
            res = await asyncio.gather(*tasks, return_exceptions=True)
            res = [r if isinstance(r, dict) else {"pid": pid, "ok": False, "error": "остановлено" if
                   isinstance(r, asyncio.CancelledError) else str(r)} for pid, r in zip(pids, res)]
            path = save_results(sc, res)
            if path:
                log(f"Сценарий «{sc['name']}»: извлечённые данные → {path}")
            return res
        finally:
            self.running.pop(jid, None)

    def stop_all(self):
        for job in list(self.running.values()):
            for t in job["tasks"]:
                t.cancel()


jobs = Jobs()
