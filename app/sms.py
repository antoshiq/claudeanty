"""Номера и коды из SMS для сценариев: 5sim или sms-activate (и сервисы с совместимым API).

Шаг «Получить номер» покупает активацию и кладёт номер в {{phone}} (с «+») и {{phone_digits}}
(только цифры). Шаг «Ждать SMS» ждёт код, кладёт его в {{sms_code}} и завершает активацию.
Если код не пришёл или сценарий упал раньше — номер отменяется (деньги возвращаются сервисом).

Ключ и сервис — Настройки → Сценарии. Названия сервисов и стран у провайдеров свои:
5sim — product «telegram», страна «russia» или «any», оператор «any»;
sms-activate — service «tg», страна числом («0» — Россия), оператор «any».
"""
import asyncio
import time
from typing import Callable, Optional

import httpx

from . import settings

FIVESIM = "https://5sim.net/v1"


class SmsError(RuntimeError):
    pass


def provider() -> str:
    return settings.get("sms_provider") or "5sim"


def _key() -> str:
    key = (settings.get("sms_key") or "").strip()
    if not key:
        raise SmsError("не указан ключ сервиса SMS (Настройки → Сценарии)")
    return key


async def _5sim(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(FIVESIM + path, headers={"Authorization": f"Bearer {_key()}", "Accept": "application/json"})
    if r.status_code == 401:
        raise SmsError("5sim: неверный ключ API")
    try:
        data = r.json()
    except ValueError:
        raise SmsError(f"5sim: {r.text.strip()[:150] or r.status_code}") from None
    if r.status_code >= 400:
        raise SmsError(f"5sim: {data}")
    return data


_SA_ERRORS = {"NO_NUMBERS": "нет свободных номеров", "NO_BALANCE": "недостаточно денег на балансе",
              "BAD_KEY": "неверный ключ API", "BAD_SERVICE": "неверный код сервиса", "BAD_ACTION": "неверный запрос",
              "NO_ACTIVATION": "активация не найдена", "BANNED": "аккаунт сервиса заблокирован",
              "WRONG_MAX_PRICE": "номер дороже максимальной цены"}


async def _sa(**params) -> str:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(settings.get("sms_activate_url"), params={"api_key": _key(), **params})
    text = r.text.strip()
    code = text.split(":")[0]
    if code in _SA_ERRORS or r.status_code >= 400:
        raise SmsError(f"sms-activate: {_SA_ERRORS.get(code, text[:150])}")
    return text


async def balance() -> float:
    if provider() == "5sim":
        return float((await _5sim("/user/profile")).get("balance", 0))
    text = await _sa(action="getBalance")
    if not text.startswith("ACCESS_BALANCE:"):
        raise SmsError(f"sms-activate: {text[:150]}")
    return float(text.split(":", 1)[1])


async def buy(service: str, country: str = "", operator: str = "") -> dict:
    """-> {"id", "phone": "+7999…", "digits": "7999…", "provider"}"""
    service = service.strip()
    if not service:
        raise SmsError("не указан сервис (для 5sim — product, например telegram; для sms-activate — код, например tg)")
    if provider() == "5sim":
        data = await _5sim(f"/user/buy/activation/{country.strip() or 'any'}/{operator.strip() or 'any'}/{service}")
        if not data.get("id"):
            raise SmsError(f"5sim: {data}")
        digits = str(data.get("phone", "")).lstrip("+")
        return {"id": str(data["id"]), "phone": "+" + digits, "digits": digits, "provider": "5sim"}
    params = {"action": "getNumber", "service": service}
    if country.strip():
        params["country"] = country.strip()
    if operator.strip() and operator.strip() != "any":
        params["operator"] = operator.strip()
    text = await _sa(**params)
    if not text.startswith("ACCESS_NUMBER:"):
        raise SmsError(f"sms-activate: {text[:150]}")
    _, ident, digits = text.split(":", 2)
    try:
        await _sa(action="setStatus", status=1, id=ident)  # «SMS отправлено» — часть сервисов ждёт этого
    except SmsError:
        pass
    return {"id": ident, "phone": "+" + digits, "digits": digits, "provider": "sms-activate"}


async def check(act: dict) -> Optional[str]:
    """Код из SMS или None, пока не пришёл. Активация отменена/просрочена — ошибка."""
    if act["provider"] == "5sim":
        data = await _5sim(f"/user/check/{act['id']}")
        sms = data.get("sms") or []
        if sms:
            last = sms[-1]
            return str(last.get("code") or last.get("text") or "")
        if data.get("status") in ("CANCELED", "TIMEOUT", "BANNED"):
            raise SmsError(f"5sim: номер {data['status']}")
        return None
    text = await _sa(action="getStatus", id=act["id"])
    if text.startswith("STATUS_OK:"):
        return text.split(":", 1)[1]
    if text.startswith("STATUS_CANCEL"):
        raise SmsError("sms-activate: активация отменена")
    return None


async def finish(act: dict):
    try:
        if act["provider"] == "5sim":
            await _5sim(f"/user/finish/{act['id']}")
        else:
            await _sa(action="setStatus", status=6, id=act["id"])
    except (SmsError, httpx.HTTPError):
        pass  # код уже получен — завершение лишь освобождает номер раньше срока


async def cancel(act: dict):
    try:
        if act["provider"] == "5sim":
            await _5sim(f"/user/cancel/{act['id']}")
        else:
            await _sa(action="setStatus", status=8, id=act["id"])
    except (SmsError, httpx.HTTPError):
        pass


async def wait_code(act: dict, timeout: float = 180, poll: float = 5,
                    log: Callable[[str], None] = lambda s: None) -> str:
    deadline = time.time() + timeout
    while True:
        try:
            code = await check(act)
        except httpx.HTTPError as e:
            log(f"SMS: сервис не ответил ({e.__class__.__name__}), повтор…")
            code = None
        if code:
            return code
        left = deadline - time.time()
        if left <= 0:
            raise SmsError(f"SMS на {act['phone']} не пришло за {int(timeout)} с — номер отменён")
        await asyncio.sleep(min(poll, left))
