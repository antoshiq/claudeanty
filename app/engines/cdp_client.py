"""Собственный CDP-клиент браузера (работает параллельно с Playwright).

Он отвечает за всю эмуляцию — нативно, через CDP, без JS-подмен:
  * страница: UA + Client Hints, языки, ядра CPU, экран/DPR/touch (телефон), таймзона, геолокация,
    тип сети и датчики телефона — применяются ДО первой строчки JS страницы;
  * Web Worker / SharedWorker / ServiceWorker: UA + Client Hints и скрипт worker.js.

Playwright при этом ничего не эмулирует (иначе его настройки перезаписали бы наши),
а Runtime-домен на страницах этот клиент не включает (Runtime.enable детектируется сайтами).

Схема: Target.setAutoAttach(waitForDebuggerOnStart) на уровне браузера и на каждой странице.
Новая цель встаёт на паузу, применяем команды и отпускаем её Runtime.runIfWaitingForDebugger.
"""
import asyncio
import json
import logging
from typing import Any, Optional

import websockets

log = logging.getLogger("claudeanty.cdp")

PAGE_TYPES = ("page", "iframe")
# Что применять к iframe из другого процесса (OOPIF, сайт другого домена). Эмуляция таймзоны, языка и т.п.
# действует на процесс рендерера, поэтому такой iframe её НЕ наследует: без этого в нём видна системная
# таймзона ПК (fingerprint.com: «VPN — timezone mismatch»). Размер экрана и touch задаёт только главная страница.
IFRAME_METHODS = ("Emulation.setUserAgentOverride", "Emulation.setTimezoneOverride", "Emulation.setLocaleOverride",
                  "Emulation.setHardwareConcurrencyOverride", "Emulation.setGeolocationOverride", "Page.")
WORKER_TYPES = ("worker", "shared_worker", "service_worker")


class CDPClient:
    def __init__(self, ws_url: str, page_cmds: list[tuple[str, dict]], worker_cmds: list[tuple[str, dict]],
                 worker_script: str):
        self.ws_url = ws_url
        self.page_cmds = page_cmds
        self.worker_cmds = worker_cmds
        self.worker_script = worker_script
        self._ws = None
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: Optional[asyncio.Task] = None
        self._done_targets: set[str] = set()
        self.stats = {"pages": 0, "workers": 0, "errors": 0, "failed": set()}

    async def start(self):
        self._ws = await websockets.connect(self.ws_url, max_size=None, ping_interval=None)
        self._reader = asyncio.ensure_future(self._read())
        await self.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})

    async def ready(self, timeout: float = 5.0):
        """Ждём, пока уже открытые вкладки получат все переопределения."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            targets = (await self.send("Target.getTargets")).get("targetInfos", [])
            pages = {t["targetId"] for t in targets if t["type"] == "page"}
            if pages and pages <= self._done_targets:
                return True
            await asyncio.sleep(0.05)
        return False

    async def stop(self):
        if self._reader:
            self._reader.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def send(self, method: str, params: Optional[dict] = None, session: Optional[str] = None) -> Any:
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._id] = fut
        await self._ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, 10)

    async def _read(self):
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(msg["error"].get("message")))
                        else:
                            fut.set_result(msg.get("result"))
                elif msg.get("method") == "Target.attachedToTarget":
                    asyncio.ensure_future(self._on_attached(msg["params"]))
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass
        except Exception:  # noqa: BLE001
            log.exception("cdp reader")

    async def _try(self, method, params, session):
        try:
            return await self.send(method, params, session)
        except Exception as e:  # noqa: BLE001
            self.stats["errors"] += 1
            self.stats["failed"].add(f"{method}: {e}")
            return None

    async def _on_attached(self, p: dict):
        sid = p["sessionId"]
        info = p["targetInfo"]
        kind = info["type"]
        try:
            if kind in PAGE_TYPES:
                self.stats["pages"] += 1
                for method, params in self.page_cmds:
                    if kind == "iframe" and not method.startswith(IFRAME_METHODS):
                        continue  # остальное iframe наследует от страницы
                    await self._try(method, params, sid)
                await self._try("Target.setAutoAttach",
                                {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}, sid)
            elif kind in WORKER_TYPES:
                self.stats["workers"] += 1
                for method, params in self.worker_cmds:
                    await self._try(method, params, sid)
                if self.worker_script:
                    await self._try("Runtime.evaluate", {"expression": self.worker_script}, sid)
        finally:
            await self._try("Runtime.runIfWaitingForDebugger", {}, sid)
            if kind == "page":
                self._done_targets.add(info["targetId"])
            if kind not in PAGE_TYPES + WORKER_TYPES:
                await self._try("Target.detachFromTarget", {"sessionId": sid}, None)
