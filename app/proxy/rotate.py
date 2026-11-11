"""Смена IP мобильного прокси по ссылке провайдера (перед запуском профиля или по кнопке).

У мобильных прокси обычно есть «ссылка смены IP»: GET-запрос на неё переподключает модем, и через
несколько секунд прокси выходит в сеть с новым адресом. claudeAnty дёргает ссылку перед запуском
профиля, ждёт, пока IP сменится, и заново определяет гео (таймзона и язык профиля подстроятся).

IP не меняется, если этим же прокси сейчас пользуется другой запущенный профиль — иначе у него
оборвались бы соединения посреди работы.
"""
import asyncio
import time

import httpx

from .. import db, settings
from ..models import Proxy
from .checker import check_proxy

_locks: dict[int, asyncio.Lock] = {}
_last: dict[int, float] = {}


async def change_ip(proxy: Proxy, say=lambda s: None) -> dict:
    """Дёргает ссылку смены IP и ждёт новый адрес. Возвращает {"ok", "old", "new", "message"}."""
    if not proxy.change_ip_url:
        return {"ok": False, "message": "у прокси нет ссылки смены IP"}
    lock = _locks.setdefault(proxy.id or 0, asyncio.Lock())
    async with lock:
        cooldown = int(settings.get("change_ip_cooldown"))
        since = time.time() - _last.get(proxy.id or 0, 0)
        if since < cooldown:
            return {"ok": True, "old": proxy.ip, "new": proxy.ip,
                    "message": f"IP менялся {int(since)} с назад — пропускаю (пауза {cooldown} с)"}
        old = proxy.ip
        say(f"Прокси {proxy.host}:{proxy.port}: смена IP…")
        try:
            async with httpx.AsyncClient(timeout=40, follow_redirects=True) as c:  # напрямую, не через прокси
                r = await c.get(proxy.change_ip_url)
            if r.status_code >= 400:
                return {"ok": False, "old": old, "message": f"ссылка ответила {r.status_code}: {r.text[:120]}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "old": old, "message": f"ссылка недоступна: {e}"}
        _last[proxy.id or 0] = time.time()
        # модем переподключается: ждём, пока прокси снова ответит (и, желательно, с другим IP)
        new = ""
        deadline = time.time() + int(settings.get("change_ip_wait"))
        await asyncio.sleep(3)
        while time.time() < deadline:
            res = await check_proxy(proxy, timeout=10, deep=False)
            if res.get("ok"):
                new = proxy.ip
                if new != old:
                    break
            await asyncio.sleep(3)
        db.save_proxy(proxy)
        if not new:
            return {"ok": False, "old": old, "message": "после смены IP прокси не отвечает"}
        msg = f"IP сменился: {old or '?'} → {new}" if new != old else f"IP не изменился ({new}) — провайдер мог выдать тот же"
        return {"ok": True, "old": old, "new": new, "message": msg}


def busy_with(proxy_id: int, running: dict, exclude: int) -> list[int]:
    """Запущенные профили (кроме exclude), которые используют этот прокси."""
    out = []
    for pid in running:
        if pid == exclude:
            continue
        p = db.get_profile(pid)
        if p and p.proxy_id == proxy_id:
            out.append(pid)
    return out
