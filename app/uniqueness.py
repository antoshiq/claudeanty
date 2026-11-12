"""Уникальность профилей между собой: не выдают ли они, что работают на одном компьютере.

Каждый профиль на этом ПК рисует canvas, WebGL и audio одним и тем же железом. Если подмена (шум от seed
профиля) не сработала, у двух профилей совпадут хэши — и сайт свяжет их аккаунты, даже если IP, cookies
и User-Agent разные. Ещё один частый след — общий выходной IP прокси.

Хэши берутся из истории проверок (fp_history: «Проверить отпечаток» и проверка стабильности) или
снимаются заново (collect) — профиль запускается без окна на несколько секунд.
"""
from collections import defaultdict
from typing import Callable, Optional

from . import db

# часть отпечатка -> (насколько плохо совпадение, пояснение)
PARTS = {
    "canvas": ("fail", "Canvas совпадает — сайты связывают профили по нему в первую очередь"),
    "webgl_image": ("fail", "картинка WebGL совпадает — та же видеокарта без шума"),
    "audio": ("fail", "audio-отпечаток совпадает"),
    "navigator": ("warn", "одинаковые экран, ядра, память, язык и таймзона — вместе это заметно"),
    "webgl_params": ("info", "одинаковая модель видеокарты — нормально, такие встречаются массово"),
    "fonts": ("info", "одинаковые шрифты — нормально для одной ОС"),
    "math": ("info", "одинаковый движок JS — нормально для одной версии Chrome"),
}


def latest_hashes(pids: Optional[list[int]] = None) -> dict[int, dict]:
    """Последние хэши каждого профиля: {pid: {"mode", "time", "hashes"}}; приоритет — режим без окна."""
    out = {}
    for p in db.list_profiles():
        if pids is not None and p.id not in pids:
            continue
        hist = p.fp_history or []
        rec = next((h for h in reversed(hist) if h.get("mode") == "headless"), None) or (hist[-1] if hist else None)
        if rec:
            out[p.id] = rec
    return out


def analyze(pids: Optional[list[int]] = None) -> dict:
    """-> {"groups": [{"part", "level", "why", "pids"}], "shared_ip": [...], "missing": [pid], "checked": n}"""
    profiles = {p.id: p for p in db.list_profiles() if pids is None or p.id in pids}
    hashes = latest_hashes(list(profiles))
    groups = []
    for part, (level, why) in PARTS.items():
        by: dict = defaultdict(list)
        for pid, rec in hashes.items():
            h = (rec.get("hashes") or {}).get(part)
            if h and h != "error":
                by[(rec.get("mode"), h)].append(pid)  # режимы не смешиваем: рендер с окном и без может отличаться
        for (_, _h), ids in by.items():
            if len(ids) > 1:
                groups.append({"part": part, "level": level, "why": why, "pids": sorted(ids)})
    # общий прокси / выходной IP
    proxies = {px.id: px for px in db.list_proxies()}
    by_ip: dict = defaultdict(list)
    for p in profiles.values():
        px = proxies.get(p.proxy_id)
        key = (px.ip or f"{px.host}:{px.port}") if px else "без прокси (ваш IP)"
        by_ip[key].append(p.id)
    shared = [{"ip": ip, "pids": sorted(ids)} for ip, ids in by_ip.items() if len(ids) > 1]
    order = {"fail": 0, "warn": 1, "info": 2}
    groups.sort(key=lambda g: (order[g["level"]], -len(g["pids"])))
    return {"groups": groups, "shared_ip": shared, "missing": sorted(set(profiles) - set(hashes)),
            "checked": len(hashes), "total": len(profiles)}


def format_report(r: dict, names: Optional[dict] = None) -> str:
    names = names or {p.id: p.name for p in db.list_profiles()}
    who = lambda ids: ", ".join(f"#{i} {names.get(i, '')}".strip() for i in ids[:12]) + \
        (f" и ещё {len(ids) - 12}" if len(ids) > 12 else "")  # noqa: E731
    mark = {"fail": "✗", "warn": "!", "info": "·"}
    lines = [f"Сравнено профилей: {r['checked']} из {r['total']}"]
    bad = [g for g in r["groups"] if g["level"] != "info"]
    if r["checked"] < 2:
        lines.append("· Сравнивать пока нечего: нужны снятые отпечатки хотя бы у двух профилей")
    elif not bad:
        lines.append("✓ Canvas, WebGL, audio и navigator у всех профилей разные — по отпечатку их не связать")
    for g in r["groups"]:
        lines.append(f"{mark[g['level']]} {g['part']}: {g['why']}\n    {who(g['pids'])}")
    for s in r["shared_ip"]:
        lines.append(f"! Общий выход {s['ip']}: {who(s['pids'])} — сайты видят их с одного IP; "
                     "для разных аккаунтов одной площадки нужны разные прокси")
    if r["missing"]:
        lines.append(f"· Без снятого отпечатка (нажмите «Снять заново»): {who(r['missing'])}")
    return "\n".join(lines)


async def collect(manager, pids: list[int], progress: Callable[[str], None] = lambda s: None) -> int:
    """Снять хэши отпечатка у профилей (без окна; запущенные — в их окне) и записать в историю."""
    from . import stability
    from .check import URL
    from .engines.base import evaluate_main
    n = 0
    for pid in pids:
        p = db.get_profile(pid)
        if not p:
            continue
        was_running = manager.is_running(pid)
        try:
            sess = manager.sessions[pid] if was_running else await manager.start(pid, headless=True)
        except Exception as e:  # noqa: BLE001 — например, мёртвый прокси
            progress(f"#{pid}: не запустился — {str(e).splitlines()[0][:120]}")
            continue
        try:
            await sess.context.route(URL + "**", lambda r: r.fulfill(body="<!doctype html><body></body>",
                                                                     content_type="text/html"))
            page = await sess.context.new_page()
            await page.goto(URL)
            hashes = await evaluate_main(page, stability.JS)
            await page.close()
            await sess.context.unroute(URL + "**")
            mode = "headless" if getattr(sess, "headless", False) else "window"
            chrome = manager.chrome_version if p.engine == "chromium" else p.engine
            p = db.get_profile(pid)
            p.fp_history = stability.record(p.fp_history, hashes, mode, chrome)
            db.save_profile(p)
            n += 1
            progress(f"#{pid}: отпечаток снят ({n} из {len(pids)})")
        except Exception as e:  # noqa: BLE001
            progress(f"#{pid}: ошибка — {str(e).splitlines()[0][:120]}")
        finally:
            if not was_running:
                await manager.stop(pid)
    return n
