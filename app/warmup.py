"""Прогрев cookies: профиль обходит популярные сайты, прокручивает страницы и задерживается на них,
чтобы не выглядеть «пустым» браузером без истории и cookies."""
import asyncio
import random

from . import db, human, settings

DEFAULT_SITES = [
    "https://www.google.com", "https://www.youtube.com", "https://www.wikipedia.org", "https://www.amazon.com",
    "https://www.reddit.com", "https://www.bbc.com", "https://www.cnn.com", "https://www.ebay.com",
    "https://www.imdb.com", "https://www.microsoft.com", "https://www.apple.com", "https://www.booking.com",
    "https://www.nytimes.com", "https://www.twitch.tv", "https://www.pinterest.com", "https://stackoverflow.com",
    "https://github.com", "https://weather.com", "https://www.espn.com", "https://www.theguardian.com",
]

_limit: asyncio.Semaphore | None = None


async def warmup(manager, pid: int, progress=None) -> dict:
    """Прогрев одного профиля. progress(text) вызывается из цикла браузеров."""
    global _limit
    if _limit is None:
        _limit = asyncio.Semaphore(3)  # не больше трёх прогревов одновременно
    sites = [s.strip() for s in (settings.get("warmup_sites") or DEFAULT_SITES) if s.strip()]
    count = min(int(settings.get("warmup_count")), len(sites))
    dwell = int(settings.get("warmup_dwell"))

    async with _limit:
        p = db.get_profile(pid)
        if not p:
            raise KeyError(pid)
        if p.status in ("", "new"):
            p.status = "warming"
            db.save_profile(p)
        was_running = manager.is_running(pid)
        sess = manager.sessions[pid] if was_running else await manager.start(pid, headless=True)
        visited = 0
        try:
            page = await sess.context.new_page()
            for url in random.sample(sites, count):
                if "://" not in url:
                    url = "https://" + url
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    visited += 1
                    if progress:
                        progress(f"#{pid}: {visited}/{count} {url}")
                    await human.wander(page, random.uniform(dwell * 0.6, dwell * 1.4))
                except Exception:  # noqa: BLE001
                    continue
            cookies = len(await sess.context.cookies())
            await page.close()
        finally:
            if not was_running:
                await manager.stop(pid)
        p = db.get_profile(pid)
        if p and p.status in ("", "new", "warming"):
            p.status = "ready"
            db.save_profile(p)
        return {"visited": visited, "cookies": cookies}
