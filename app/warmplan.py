"""Планы прогрева под площадку: «Facebook, 7 дней» — по дням какие сайты, сколько поисковых запросов,
сколько времени. Один день плана выполняется не чаще раза в сутки; пропущенный календарный день просто
сдвигает план (день 3 выполнится завтра). Удобнее всего — задача «День плана прогрева» в Планировщике
каждый день в своё время.

Состояние в профиле (Profile.warm_plan): {"plan": ключ, "started": "2026-11-12", "done": ["2026-11-12", …]}.
Когда все дни выполнены, план помечается finished, а метка «Прогрев»/«Новый» меняется на «Готов».

Прогрев идёт без входа в аккаунты: профиль накапливает историю, cookies и «живое» поведение на общих
сайтах и на самой площадке, чтобы к регистрации или первому входу не выглядеть пустым браузером.
"""
import asyncio
import datetime as dt
import random
from typing import Callable, Optional

from . import db, human

NEWS = ["https://www.bbc.com", "https://www.cnn.com", "https://www.theguardian.com", "https://www.reuters.com",
        "https://www.nytimes.com", "https://apnews.com"]
SHOPS = ["https://www.amazon.com", "https://www.ebay.com", "https://www.etsy.com", "https://www.walmart.com",
         "https://www.ikea.com"]
COMMON = ["https://www.wikipedia.org", "https://www.youtube.com", "https://www.reddit.com", "https://www.imdb.com",
          "https://weather.com", "https://www.booking.com", "https://www.pinterest.com", "https://stackoverflow.com",
          "https://www.twitch.tv", "https://www.espn.com"]
QUERIES = ["weather tomorrow", "best pizza near me", "how to cook rice", "football scores", "cheap flights",
           "new movies this week", "iphone vs samsung", "exchange rate", "running shoes", "coffee machine review",
           "how to tie a tie", "translate hello", "recipe pancakes", "best laptop 2026", "nearest pharmacy",
           "bus schedule", "music festival", "book recommendations", "yoga for beginners", "car insurance"]


def _day(sites: list[str], count: int, dwell: int = 15, searches: int = 0) -> dict:
    return {"sites": sites, "count": count, "dwell": dwell, "searches": searches}


PLANS: dict[str, dict] = {
    "generic": {"title": "Универсальный, 5 дней", "days": [
        _day(COMMON + NEWS, 5, 12, 1), _day(COMMON + SHOPS, 6, 15, 2), _day(NEWS + SHOPS, 6, 15, 2),
        _day(COMMON + NEWS + SHOPS, 7, 18, 3), _day(COMMON + NEWS + SHOPS, 8, 18, 3)]},
    "google": {"title": "Google / YouTube, 5 дней", "days": [
        _day(["https://www.google.com", "https://www.youtube.com"] + NEWS, 4, 15, 2),
        _day(["https://www.youtube.com", "https://news.google.com", "https://maps.google.com"] + COMMON, 5, 20, 3),
        _day(["https://www.youtube.com", "https://translate.google.com"] + SHOPS + NEWS, 6, 20, 4),
        _day(["https://www.youtube.com", "https://maps.google.com", "https://www.google.com/shopping"] + COMMON, 6, 25, 4),
        _day(["https://www.youtube.com", "https://accounts.google.com", "https://mail.google.com"] + COMMON, 6, 25, 5)]},
    "facebook": {"title": "Facebook, 7 дней", "days": [
        _day(COMMON + NEWS, 5, 12, 1), _day(COMMON + SHOPS, 6, 15, 2), _day(NEWS + SHOPS, 6, 15, 2),
        _day(["https://www.facebook.com"] + COMMON, 5, 15, 2),
        _day(["https://www.facebook.com", "https://www.instagram.com"] + NEWS, 5, 18, 2),
        _day(["https://www.facebook.com", "https://www.facebook.com/marketplace", "https://www.messenger.com"] + SHOPS,
             6, 18, 3),
        _day(["https://www.facebook.com", "https://www.facebook.com/watch"] + COMMON + NEWS, 7, 20, 3)]},
    "instagram": {"title": "Instagram, 7 дней", "days": [
        _day(COMMON + NEWS, 5, 12, 1), _day(COMMON + ["https://www.pinterest.com"], 6, 15, 2),
        _day(SHOPS + ["https://www.pinterest.com", "https://www.tumblr.com"], 6, 15, 2),
        _day(["https://www.instagram.com"] + COMMON, 5, 15, 2),
        _day(["https://www.instagram.com", "https://www.instagram.com/explore"] + NEWS, 5, 18, 2),
        _day(["https://www.instagram.com", "https://www.facebook.com", "https://www.threads.net"] + COMMON, 6, 18, 3),
        _day(["https://www.instagram.com", "https://www.instagram.com/explore"] + COMMON + SHOPS, 7, 20, 3)]},
    "tiktok": {"title": "TikTok, 5 дней", "days": [
        _day(COMMON + NEWS, 5, 12, 1), _day(COMMON + ["https://www.youtube.com"], 6, 18, 2),
        _day(["https://www.tiktok.com"] + COMMON, 5, 20, 2),
        _day(["https://www.tiktok.com", "https://www.tiktok.com/explore"] + SHOPS, 6, 25, 2),
        _day(["https://www.tiktok.com", "https://www.tiktok.com/explore"] + COMMON + NEWS, 7, 25, 3)]},
}

_limit: Optional[asyncio.Semaphore] = None


def today() -> str:
    return dt.date.today().isoformat()


def assign(pid: int, plan: str) -> None:
    """Назначить план (пустой ключ — снять)."""
    p = db.get_profile(pid)
    if not p:
        return
    if not plan:
        p.warm_plan = {}
    else:
        if plan not in PLANS:
            raise KeyError(f"нет плана {plan}")
        p.warm_plan = {"plan": plan, "started": today(), "done": []}
        if p.status in ("", "new"):
            p.status = "warming"
    db.save_profile(p)
    from . import activity
    activity.log(pid, "plan", f"назначен план «{PLANS[plan]['title']}»" if plan else "план прогрева снят")


def progress(p) -> str:
    wp = p.warm_plan or {}
    plan = PLANS.get(wp.get("plan", ""))
    if not plan:
        return ""
    n, total = len(wp.get("done", [])), len(plan["days"])
    if wp.get("finished") or n >= total:
        return f"«{plan['title']}» — выполнен ({total} из {total})"
    state = "сегодня выполнен" if today() in wp.get("done", []) else "сегодня ещё не выполнялся"
    return f"«{plan['title']}» — день {n + 1} из {total}, {state}"


def due(p) -> bool:
    """Есть ли у профиля невыполненный день плана на сегодня."""
    wp = p.warm_plan or {}
    plan = PLANS.get(wp.get("plan", ""))
    return bool(plan) and not wp.get("finished") and len(wp.get("done", [])) < len(plan["days"]) \
        and today() not in wp.get("done", [])


async def _search(page, query: str):
    await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=30000)
    box = page.locator("textarea[name=q], input[name=q]").first
    await box.wait_for(state="visible", timeout=10000)
    await human.click(page, box)
    await human.type_text(page, query)
    await asyncio.sleep(random.uniform(0.3, 1.0))
    await page.keyboard.press("Enter")
    await page.wait_for_load_state("domcontentloaded", timeout=30000)
    await human.wander(page, random.uniform(4, 9))
    if "/sorry/" in page.url:  # Google показал капчу «необычный трафик» — дальше в этот день не ищем
        raise RuntimeError("Google просит капчу")
    results = page.locator("#search a:has(h3)")
    n = await results.count()
    if n:
        await human.click(page, results.nth(random.randrange(min(n, 5))))
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        await human.wander(page, random.uniform(8, 18))


async def run_day(manager, pid: int, log: Callable[[str], None] = lambda s: None, force: bool = False) -> dict:
    """Выполнить следующий день плана. force=True — даже если сегодня уже выполняли."""
    global _limit
    if _limit is None:
        _limit = asyncio.Semaphore(3)
    p = db.get_profile(pid)
    if not p:
        raise KeyError(pid)
    wp = dict(p.warm_plan or {})
    plan = PLANS.get(wp.get("plan", ""))
    if not plan:
        return {"skipped": "плана нет"}
    done = list(wp.get("done", []))
    if len(done) >= len(plan["days"]):
        return {"skipped": "план уже выполнен"}
    if today() in done and not force:
        return {"skipped": "сегодняшний день уже выполнен"}
    n = len(done)
    day = plan["days"][n]
    async with _limit:
        was_running = manager.is_running(pid)
        sess = manager.sessions[pid] if was_running else await manager.start(pid, headless=True)
        visited, searched = 0, 0
        try:
            page = await sess.context.new_page()
            sites = random.sample(day["sites"], min(day["count"], len(day["sites"])))
            searches = random.sample(QUERIES, day["searches"])
            # поисковые запросы вперемешку с сайтами — как у человека
            tasks = [("site", s) for s in sites] + [("search", q) for q in searches]
            random.shuffle(tasks)
            no_search = False
            for kind, value in tasks:
                if kind == "search" and no_search:
                    continue
                try:
                    if kind == "search":
                        await _search(page, value)
                        searched += 1
                    else:
                        await page.goto(value, wait_until="domcontentloaded", timeout=30000)
                        await human.wander(page, random.uniform(day["dwell"] * 0.6, day["dwell"] * 1.5))
                        visited += 1
                    log(f"#{pid}: план, день {n + 1}: {'поиск «' + value + '»' if kind == 'search' else value}")
                except Exception as e:  # noqa: BLE001 — сайт не открылся, капча — идём дальше
                    if "капчу" in str(e):
                        no_search = True
                        log(f"#{pid}: Google просит капчу — поиски на сегодня пропущены")
                    continue
            await page.close()
        finally:
            if not was_running:
                await manager.stop(pid)
    p = db.get_profile(pid)
    wp = dict(p.warm_plan or {})
    wp["done"] = list(wp.get("done", [])) + [today()]
    finished = len(wp["done"]) >= len(plan["days"])
    if finished:
        wp["finished"] = True
        if p.status in ("", "new", "warming"):
            p.status = "ready"
    p.warm_plan = wp
    db.save_profile(p)
    from . import activity
    text = (f"день {n + 1} из {len(plan['days'])} плана «{plan['title']}»: сайтов {visited}, поисков {searched}"
            + (" — план выполнен" if finished else ""))
    activity.log(pid, "plan", text)
    return {"day": n + 1, "total": len(plan["days"]), "visited": visited, "searched": searched, "finished": finished}
