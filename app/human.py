"""Поведение как у человека: движение мыши, клики, печать, прокрутка.

Антифрод (поведенческая биометрия) смотрит не на отдельные события, а на их динамику: прямые линии
мыши с постоянной скоростью, клик точно в центр, ровные паузы между клавишами и прокрутка ровными
шагами — признаки автоматизации. Здесь:
  * мышь — кубическая кривая Безье со случайными опорными точками, разгон и торможение (minimum-jerk),
    мелкое дрожание, иногда перелёт мимо цели с коррекцией; время — по закону Фиттса;
  * клик — в случайную точку ближе к центру (нормальное распределение), удержание 50–140 мс;
  * печать — паузы по логнормальному распределению, длиннее после пробелов и знаков, редкие опечатки
    соседней клавишей с исправлением Backspace;
  * прокрутка — «щелчки колеса» по 100 px с ускорением и инерцией.
"""
import asyncio
import math
import random

_ROWS_EN = ["`1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"]
_ROWS_RU = ["ё1234567890-=", "йцукенгшщзхъ", "фывапролджэ", "ячсмитьбю."]


def _neighbors() -> dict[str, str]:
    out: dict[str, str] = {}
    for rows in (_ROWS_EN, _ROWS_RU):
        for r, row in enumerate(rows):
            for c, ch in enumerate(row):
                near = []
                for dr in (-1, 0, 1):
                    rr = r + dr
                    if 0 <= rr < len(rows):
                        for dc in (-1, 0, 1):
                            cc = c + dc
                            if (dr or dc) and 0 <= cc < len(rows[rr]):
                                near.append(rows[rr][cc])
                out[ch] = "".join(near)
    return out


_NEAR = _neighbors()


def _pos(page) -> tuple[float, float]:
    pos = getattr(page, "_ca_mouse", None)
    if pos is None:
        vp = page.viewport_size or {"width": 1200, "height": 700}
        pos = (random.uniform(0.3, 0.7) * vp["width"], random.uniform(0.3, 0.7) * vp["height"])
    return pos


def _bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def path(start, end, target_width: float = 20.0) -> list[tuple[float, float, float]]:
    """Точки траектории (x, y, пауза в секундах до следующей)."""
    (x0, y0), (x1, y1) = start, end
    dist = math.hypot(x1 - x0, y1 - y0)
    if dist < 2:
        return [(x1, y1, 0.0)]
    # Фиттс: T = a + b * log2(D/W + 1), плюс разброс
    duration = (0.12 + 0.11 * math.log2(dist / max(target_width, 4) + 1)) * random.uniform(0.85, 1.25)
    # опорные точки сбоку от прямой — дуга, как от движения запястья
    nx, ny = -(y1 - y0) / dist, (x1 - x0) / dist
    bend = random.uniform(-0.25, 0.25) * min(dist, 600)
    c1 = (x0 + (x1 - x0) * random.uniform(0.2, 0.4) + nx * bend, y0 + (y1 - y0) * random.uniform(0.2, 0.4) + ny * bend)
    c2 = (x0 + (x1 - x0) * random.uniform(0.6, 0.8) + nx * bend * random.uniform(0.3, 0.8),
          y0 + (y1 - y0) * random.uniform(0.6, 0.8) + ny * bend * random.uniform(0.3, 0.8))
    n = max(8, min(60, int(dist / 12)))
    pts = []
    for i in range(1, n + 1):
        t = i / n
        s = 10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5  # minimum-jerk: плавный разгон и торможение
        x, y = _bezier((x0, y0), c1, c2, (x1, y1), s)
        if i < n:  # дрожание руки, к концу меньше
            j = (1 - t) * 1.2
            x, y = x + random.gauss(0, j), y + random.gauss(0, j)
        pts.append((x, y, duration / n * random.uniform(0.7, 1.3)))
    return pts


async def move(page, x: float, y: float, target_width: float = 20.0):
    start = _pos(page)
    pts = []
    dist = math.hypot(x - start[0], y - start[1])
    if dist > 250 and random.random() < 0.25:  # перелёт мимо цели и коррекция
        ox, oy = x + (x - start[0]) / dist * random.uniform(8, 25), y + (y - start[1]) / dist * random.uniform(4, 15)
        pts = path(start, (ox, oy), target_width) + path((ox, oy), (x, y), target_width)
    else:
        pts = path(start, (x, y), target_width)
    for px, py, pause in pts:
        await page.mouse.move(px, py)
        if pause > 0.002:
            await asyncio.sleep(pause)
    page._ca_mouse = (x, y)


def _point_in(box: dict) -> tuple[float, float]:
    w, h = box["width"], box["height"]
    x = box["x"] + w / 2 + max(-w * 0.4, min(w * 0.4, random.gauss(0, w / 6)))
    y = box["y"] + h / 2 + max(-h * 0.4, min(h * 0.4, random.gauss(0, h / 6)))
    return x, y


async def click(page, locator, timeout: float = 15000):
    """Клик по элементу: прокрутить к нему при необходимости, подвести мышь, нажать и отпустить."""
    await locator.scroll_into_view_if_needed(timeout=timeout)
    box = await locator.bounding_box(timeout=timeout)
    if not box:
        await locator.click(timeout=timeout)  # невидимый элемент — как есть
        return
    x, y = _point_in(box)
    await move(page, x, y, target_width=min(box["width"], box["height"]) or 20)
    await asyncio.sleep(random.uniform(0.05, 0.25))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.05, 0.14))
    await page.mouse.up()


# Русская раскладка ЙЦУКЕН -> физические клавиши (code, Windows VK). Playwright печатает символы, которых нет
# в US-раскладке, через insertText — без keydown/keyup, и текст появляется «из воздуха». Поэтому кириллицу
# отправляем настоящими нажатиями клавиш через CDP, как при русской раскладке Windows.
_RU_KEYS: dict[str, tuple[str, int]] = {"ё": ("Backquote", 192), ".": ("Slash", 191)}
for _ru, _us in zip("йцукенгшщзхъфывапролджэячсмитьбю", "qwertyuiop[]asdfghjkl;'zxcvbnm,."):
    _RU_KEYS[_ru] = {"[": ("BracketLeft", 219), "]": ("BracketRight", 221), ";": ("Semicolon", 186),
                     "'": ("Quote", 222), ",": ("Comma", 188), ".": ("Period", 190)}.get(_us, (f"Key{_us.upper()}", ord(_us.upper())))


async def _cdp(page):
    sess = getattr(page, "_ca_cdp", None)
    if sess is None:
        sess = await page.context.new_cdp_session(page)
        page._ca_cdp = sess
    return sess


async def press_char(page, ch: str):
    """Один символ с событиями клавиш. Латиница и знаки US — через Playwright, кириллица — через CDP."""
    low = ch.lower()
    if ord(ch) < 128:
        await page.keyboard.type(ch)
        return
    if low not in _RU_KEYS:
        await page.keyboard.insert_text(ch)  # эмодзи, редкие символы — как вставка
        return
    code, vk = _RU_KEYS[low]
    shift = ch != low
    cdp = await _cdp(page)
    mods = 8 if shift else 0
    if shift:
        await cdp.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Shift", "code": "ShiftLeft",
                                                  "windowsVirtualKeyCode": 16, "modifiers": 8})
    await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": ch, "code": code, "text": ch,
                                              "unmodifiedText": low, "windowsVirtualKeyCode": vk,
                                              "nativeVirtualKeyCode": vk, "modifiers": mods})
    await asyncio.sleep(random.uniform(0.03, 0.09))
    await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch, "code": code, "windowsVirtualKeyCode": vk,
                                              "nativeVirtualKeyCode": vk, "modifiers": mods})
    if shift:
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Shift", "code": "ShiftLeft",
                                                  "windowsVirtualKeyCode": 16, "modifiers": 0})


def _key_delay(prev: str, ch: str, wpm: float) -> float:
    base = 60 / (wpm * 5)  # секунд на символ
    d = random.lognormvariate(math.log(base), 0.35)
    if prev in " .,!?;:\n":
        d *= random.uniform(1.3, 2.2)  # пауза после слова или знака
    if ch.isupper() or not ch.isalnum():
        d *= random.uniform(1.1, 1.6)  # Shift и редкие клавиши медленнее
    return min(d, 1.2)


async def type_text(page, text: str, typos: bool = True, wpm: float | None = None):
    """Печать в текущий фокус. typos=False — без опечаток (для паролей и кодов можно и с ними:
    опечатка сразу исправляется)."""
    wpm = wpm or random.uniform(45, 80)
    prev = " "
    for ch in text:
        low = ch.lower()
        if typos and low in _NEAR and ch.isalpha() and random.random() < 0.03:
            wrong = random.choice(_NEAR[low])
            await press_char(page, wrong.upper() if ch.isupper() else wrong)
            await asyncio.sleep(_key_delay(prev, wrong, wpm) * random.uniform(1.5, 3.0))  # заметили не сразу
            await page.keyboard.press("Backspace")
            await asyncio.sleep(_key_delay(wrong, ch, wpm))
        await press_char(page, ch)
        await asyncio.sleep(_key_delay(prev, ch, wpm))
        prev = ch


async def scroll(page, dy: float):
    """Прокрутка «колесом»: щелчки по 100 px, сначала быстрее, в конце медленнее."""
    sign = 1 if dy >= 0 else -1
    ticks = max(1, round(abs(dy) / 100))
    x, y = _pos(page)
    await page.mouse.move(x, y)
    for i in range(ticks):
        await page.mouse.wheel(0, sign * 100)
        t = i / max(1, ticks - 1)
        await asyncio.sleep(random.uniform(0.03, 0.07) + 0.12 * t * t + (random.uniform(0.2, 0.6) if random.random() < 0.08 else 0))


async def wander(page, seconds: float):
    """«Читает» страницу: случайные движения мыши и прокрутка вниз (иногда вверх) — для прогрева."""
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    vp = page.viewport_size or {"width": 1200, "height": 700}
    while loop.time() < end:
        r = random.random()
        if r < 0.45:
            await move(page, random.uniform(60, vp["width"] - 60), random.uniform(90, vp["height"] - 60))
        elif r < 0.9:
            await scroll(page, random.choice([1, 1, 1, 1, -1]) * random.uniform(200, 900))
        await asyncio.sleep(random.lognormvariate(math.log(0.9), 0.5))
