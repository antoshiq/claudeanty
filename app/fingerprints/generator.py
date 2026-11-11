"""Согласованная генерация отпечатка. Генерируется один раз и сохраняется в профиле.

Версия Chrome в UA/Client Hints подставляется при запуске из реального движка (engines/chrome_version.py),
производные поля (параметры WebGL, шрифты ОС, медиаустройства) достраиваются функцией enrich().
"""
import json
import random
import re
from pathlib import Path

_DIR = Path(__file__).parent / "data"
DEVICES: dict = json.loads((_DIR / "devices.json").read_text(encoding="utf-8"))
DESKTOP: dict = json.loads((_DIR / "desktop.json").read_text(encoding="utf-8"))
FONTS: dict = json.loads((_DIR / "fonts.json").read_text(encoding="utf-8"))

CORES = [2, 4, 6, 8, 10, 12, 16, 20, 24]
MEMORY = [2, 4, 8, 16, 32]
SCREENS = [(1280, 720), (1280, 800), (1366, 768), (1440, 900), (1536, 864), (1600, 900), (1680, 1050),
           (1920, 1080), (1920, 1200), (2560, 1080), (2560, 1440), (2880, 1800), (3440, 1440), (3840, 2160)]

# параметры WebGL по семействам видеокарт: id параметра -> значение ({"i": [...]} = Int32Array, {"f": [...]} = Float32Array)
_GL = {
    "d3d11": {3379: 16384, 34024: 16384, 3386: {"i": [32767, 32767]}, 36347: 4096, 36349: 1024, 36348: 30, 35661: 32,
              34930: 16, 35660: 16, 34921: 16, 34076: 16384, 33902: {"f": [1, 1]}, 33901: {"f": [1, 1024]}},
    "apple": {3379: 16384, 34024: 16384, 3386: {"i": [16384, 16384]}, 36347: 1024, 36349: 1024, 36348: 31, 35661: 32,
              34930: 16, 35660: 16, 34921: 16, 34076: 16384, 33902: {"f": [1, 1]}, 33901: {"f": [1, 511]}},
    "gl": {3379: 16384, 34024: 16384, 3386: {"i": [16384, 16384]}, 36347: 4096, 36349: 1024, 36348: 31, 35661: 32,
           34930: 16, 35660: 16, 34921: 16, 34076: 16384, 33902: {"f": [1, 1]}, 33901: {"f": [1, 1024]}},
    "adreno": {3379: 16384, 34024: 16384, 3386: {"i": [16384, 16384]}, 36347: 256, 36349: 256, 36348: 31, 35661: 32,
               34930: 16, 35660: 16, 34921: 32, 34076: 16384, 33902: {"f": [1, 8]}, 33901: {"f": [1, 1023]}},
    "mali": {3379: 8192, 34024: 8192, 3386: {"i": [8192, 8192]}, 36347: 256, 36349: 256, 36348: 15, 35661: 96,
             34930: 16, 35660: 16, 34921: 16, 34076: 8192, 33902: {"f": [1, 1]}, 33901: {"f": [1, 1024]}},
}


def gl_family(renderer: str) -> str:
    r = renderer.lower()
    if "adreno" in r:
        return "adreno"
    if "mali" in r:
        return "mali"
    if "apple" in r or "metal" in r:
        return "apple"
    if "direct3d" in r or "d3d11" in r:
        return "d3d11"
    return "gl"


def gpus_for(os_name: str) -> list[tuple[str, str]]:
    if os_name == "android":
        seen, out = set(), []
        for d in DEVICES.values():
            k = (d["gpu_vendor"], d["gpu_renderer"])
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out
    return [tuple(g) for g in DESKTOP[os_name]["gpus"]]


def _viewport_for(w: int, h: int) -> dict:
    return {"width": min(w, 1366), "height": min(h - 120, 700)}


def generate_desktop(os_name: str = "windows") -> dict:
    d = DESKTOP[os_name]
    w, h = random.choice(DESKTOP["screens"])
    vendor, renderer = random.choice(d["gpus"])
    return {
        "kind": "desktop", "os": os_name, "seed": random.randint(1, 2**31 - 1),
        # версия Chrome будет заменена реальной при запуске
        "user_agent": f"Mozilla/5.0 ({d['ua_os']}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "platform": d["platform"],
        "ch": {"brands": [], "full_version": "131.0.0.0", "platform": d["ch_platform"],
               "platform_version": d["ch_platform_version"], "architecture": "x86",
               "model": "", "mobile": False, "bitness": "64"},
        "screen": {"width": w, "height": h, "avail_height": h - 40, "dpr": 1.0, "color_depth": 24},
        "viewport": _viewport_for(w, h),
        "webgl": {"vendor": vendor, "renderer": renderer},
        **_cpu_ram(os_name, renderer),
        "touch_points": 0, "mobile": False,
    }


def _cpu_ram(os_name: str, renderer: str) -> dict:
    """Ядра и память, которые встречаются вместе на реальных машинах."""
    if os_name == "macos" and "Apple" in renderer:
        cores = random.choice([8, 8, 10, 12])
        memory = random.choice([8, 16, 16, 32])
    else:
        cores = random.choice(DESKTOP["cores"])
        memory = random.choice([m for m in DESKTOP["memory"] if m >= (8 if cores >= 12 else 4)])
    return {"cores": cores, "memory": memory}


def generate_mobile(device_key: str | None = None) -> dict:
    key = device_key if device_key in DEVICES else random.choice(list(DEVICES))
    dev = DEVICES[key]
    return {
        "kind": "mobile", "os": "android", "device": key, "seed": random.randint(1, 2**31 - 1),
        "user_agent": (f"Mozilla/5.0 (Linux; Android {dev['android']}; {dev['model']}) AppleWebKit/537.36 "
                       f"(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),
        "platform": "Linux armv81",
        "ch": {"brands": [], "full_version": "131.0.0.0", "platform": "Android",
               "platform_version": f"{dev['android']}.0.0", "architecture": "",
               "model": dev["model"], "mobile": True, "bitness": ""},
        "screen": {"width": dev["width"], "height": dev["height"], "avail_height": dev["height"],
                   "dpr": dev["dpr"], "color_depth": 24},
        "viewport": {"width": dev["width"], "height": dev["height"] - 56},
        "webgl": {"vendor": dev["gpu_vendor"], "renderer": dev["gpu_renderer"]},
        "cores": dev["cores"], "memory": dev["memory"],
        "touch_points": 5, "mobile": True,
    }


def generate(kind: str, os_name: str = "windows", device: str = "") -> dict:
    return generate_mobile(device) if kind == "mobile" else generate_desktop(os_name)


def enrich(fp: dict) -> dict:
    """Производные поля, которые считаются при каждом запуске (подходит и для старых профилей)."""
    fp = dict(fp)
    os_name = "android" if fp.get("mobile") else fp.get("os", "windows")
    gl = dict(fp.get("webgl", {}))
    gl["params"] = {str(k): v for k, v in _GL[gl_family(gl.get("renderer", ""))].items()}
    fp["webgl"] = gl
    fp["fonts"] = FONTS.get(os_name, [])
    if not fp.get("mobile"):
        # доступная область экрана: Windows — панель задач снизу, macOS — строка меню сверху, Linux — панель сверху
        bar_top, bar_bottom = {"windows": (0, 40), "macos": (25, 0), "linux": (27, 0)}.get(os_name, (0, 40))
        sc = dict(fp["screen"])
        sc["avail_top"] = bar_top
        sc["avail_height"] = sc["height"] - bar_top - bar_bottom
        fp["screen"] = sc
    fp["media"] = ({"audioinput": 1, "videoinput": 2, "audiooutput": 1} if fp.get("mobile")
                   else {"audioinput": 1, "videoinput": 1, "audiooutput": 1})
    return fp


# ---------- ручное редактирование ----------
def apply_edit(fp: dict, screen=None, cores=None, memory=None, gpu=None) -> dict:
    """Меняет поля отпечатка, сохраняя согласованность (viewport под экран и т.п.)."""
    fp = dict(fp)
    if screen and not fp.get("mobile"):
        w, h = screen
        fp["screen"] = {**fp["screen"], "width": w, "height": h, "avail_height": h - 40}
        fp["viewport"] = _viewport_for(w, h)
    if cores:
        fp["cores"] = int(cores)
    if memory:
        fp["memory"] = int(memory)
    if gpu:
        fp["webgl"] = {"vendor": gpu[0], "renderer": gpu[1]}
    return fp


def validate(kind: str, os_name: str, fp: dict, locale: str = "", timezone: str = "", auto_geo: bool = True) -> list[str]:
    """Список проблем согласованности. Пустой список — всё в порядке."""
    errs = []
    gpus = gpus_for("android" if kind == "mobile" else os_name)
    gpu = (fp.get("webgl", {}).get("vendor"), fp.get("webgl", {}).get("renderer"))
    if gpu not in gpus:
        errs.append("Видеокарта не подходит к выбранной ОС/устройству")
    cores, mem = fp.get("cores", 0), fp.get("memory", 0)
    if kind == "mobile":
        if cores > 12:
            errs.append("У телефонов не бывает больше 12 ядер")
        if mem > 16:
            errs.append("У телефонов не бывает больше 16 ГБ памяти")
    else:
        if os_name == "macos" and "Apple" in (gpu[1] or "") and cores not in (8, 10, 12, 16, 20, 24):
            errs.append("У Mac на Apple Silicon бывает 8–24 ядра")
        if cores >= 16 and mem <= 4:
            errs.append(f"{cores} ядер при {mem} ГБ памяти выглядит неправдоподобно")
    if not auto_geo:
        if locale and not re.fullmatch(r"[a-z]{2,3}(-[A-Z]{2})?", locale):
            errs.append("Язык должен быть в формате ru-RU, en-US или de")
        if timezone and not _valid_tz(timezone):
            errs.append(f"Неизвестная таймзона «{timezone}». Пример: Europe/Moscow")
    return errs


def _valid_tz(tz: str) -> bool:
    try:
        import zoneinfo
        zones = zoneinfo.available_timezones()
        if zones:
            return tz in zones
    except Exception:  # noqa: BLE001
        pass
    return bool(re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+\-]+){1,2}|UTC", tz))
