"""Локальный API (только 127.0.0.1) для автоматизации: Playwright / Puppeteer / Selenium."""
import threading
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import cookies as ck
from .. import db
from ..config import API_HOST, API_PORT
from ..fingerprints.generator import DEVICES
from ..manager import manager
from ..models import Profile, Proxy
from ..proxy.checker import check_proxy

app = FastAPI(title="claudeAnty local API")


def _profile_or_404(pid: int) -> Profile:
    p = db.get_profile(pid)
    if not p:
        raise HTTPException(404, "profile not found")
    return p


def _view(p: Profile) -> dict:
    d = p.model_dump(exclude={"fingerprint"})
    d["running"] = manager.is_running(p.id)
    return d


# ---------- профили ----------
@app.get("/devices")
def devices():
    return {k: v["label"] for k, v in DEVICES.items()}


@app.get("/profiles")
def list_profiles():
    return [_view(p) for p in db.list_profiles()]


@app.get("/profiles/{pid}")
def get_profile(pid: int):
    return _view(_profile_or_404(pid))


@app.post("/profiles")
def create_profile(p: Profile):
    p.id = None
    return _view(manager.create_profile(p))


class BulkRequest(BaseModel):
    template: Profile
    count: int = 1
    name_prefix: str = "Profile"
    proxies: list[str] = []  # строки type://user:pass@host:port, по одной на профиль


@app.post("/profiles/bulk")
def create_bulk(req: BulkRequest):
    out = []
    for i in range(req.count):
        p = req.template.model_copy(deep=True)
        p.id = None
        p.fingerprint = {}
        p.name = f"{req.name_prefix} {i + 1}"
        if i < len(req.proxies):
            p.proxy_id = db.save_proxy(Proxy.parse(req.proxies[i])).id
        out.append(_view(manager.create_profile(p)))
    return out


@app.patch("/profiles/{pid}")
def patch_profile(pid: int, changes: dict):
    p = _profile_or_404(pid)
    changes.pop("id", None)
    changes.pop("fingerprint", None)
    data = p.model_copy(update=changes)
    data = Profile(**data.model_dump())
    if {"kind", "os", "engine", "device"} & changes.keys():
        data.fingerprint = {}
        manager._prepare_fingerprint(data)
    return _view(db.save_profile(data))


@app.delete("/profiles/{pid}")
def delete_profile(pid: int):
    _profile_or_404(pid)
    manager.delete_profile(pid)
    return {"ok": True}


# ---------- запуск ----------
@app.get("/profiles/{pid}/start")
def start_profile(pid: int, headless: bool = False):
    _profile_or_404(pid)
    try:
        sess = manager.run(manager.start(pid, headless=headless))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e))
    return {"ok": True, "engine": sess.engine, "debug_port": sess.debug_port, "ws_endpoint": sess.ws_endpoint,
            "note": "" if sess.ws_endpoint else "Для camoufox внешний CDP-доступ недоступен (Firefox)"}


@app.get("/profiles/{pid}/stop")
def stop_profile(pid: int):
    manager.run(manager.stop(pid))
    return {"ok": True}


# ---------- проверка, прогрев, синхронизатор ----------
@app.post("/profiles/{pid}/check")
def check_profile(pid: int):
    from ..check import run_check
    _profile_or_404(pid)
    return manager.run(run_check(manager, pid), timeout=300)


@app.post("/profiles/{pid}/warmup")
def warmup_profile(pid: int):
    from ..warmup import warmup
    _profile_or_404(pid)
    return manager.run(warmup(manager, pid), timeout=1800)


class SyncIn(BaseModel):
    leader: int
    followers: list[int]


@app.post("/sync/start")
def sync_start(body: SyncIn):
    manager.run(manager.sync.start(body.leader, body.followers))
    return manager.sync.status()


@app.post("/sync/stop")
def sync_stop():
    manager.run(manager.sync.stop())
    return manager.sync.status()


@app.get("/sync")
def sync_status():
    return manager.sync.status()


# ---------- cookies ----------
@app.get("/profiles/{pid}/cookies")
def get_cookies(pid: int, format: str = "json"):
    _profile_or_404(pid)
    cookies = manager.run(manager.get_cookies(pid))
    text = ck.export_netscape(cookies) if format == "netscape" else ck.export_json(cookies)
    return PlainTextResponse(text, media_type="application/json" if format == "json" else "text/plain")


class CookiesIn(BaseModel):
    cookies: str  # JSON или Netscape


@app.post("/profiles/{pid}/cookies")
def set_cookies(pid: int, body: CookiesIn):
    _profile_or_404(pid)
    try:
        n = manager.run(manager.set_cookies(pid, body.cookies))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    return {"ok": True, "imported": n}


# ---------- прокси ----------
@app.get("/proxies")
def list_proxies():
    return [p.model_dump() for p in db.list_proxies()]


class ProxyIn(BaseModel):
    url: Optional[str] = None
    proxy: Optional[Proxy] = None


@app.post("/proxies")
def add_proxy(body: ProxyIn):
    p = Proxy.parse(body.url) if body.url else body.proxy
    if not p:
        raise HTTPException(400, "url or proxy required")
    p.id = None
    return db.save_proxy(p).model_dump()


@app.delete("/proxies/{proxy_id}")
def del_proxy(proxy_id: int):
    db.delete_proxy(proxy_id)
    return {"ok": True}


@app.post("/proxies/{proxy_id}/check")
def check(proxy_id: int):
    p = db.get_proxy(proxy_id)
    if not p:
        raise HTTPException(404, "proxy not found")
    res = manager.run(check_proxy(p))
    db.save_proxy(p)
    return res


def start_api_thread() -> threading.Thread:
    cfg = uvicorn.Config(app, host=API_HOST, port=API_PORT, log_level="warning")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True, name="api-server")
    t.start()
    return t
