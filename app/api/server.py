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
from ..models import Account, Profile, Proxy
from ..proxy.checker import check_proxy

app = FastAPI(title="claudeAnty local API")

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _local_origin(origin: str) -> bool:
    from urllib.parse import urlparse
    try:
        return (urlparse(origin).hostname or "") in {"127.0.0.1", "localhost", "::1"}
    except ValueError:
        return False


@app.middleware("http")
async def _guard(request, call_next):
    """API — для ваших скриптов, не для сайтов. Без этой защиты любая открытая в браузере страница могла бы
    запускать профили запросами на 127.0.0.1 (CSRF), а через DNS rebinding — читать пароли и коды 2FA."""
    from fastapi.responses import JSONResponse

    from .. import settings
    host = request.headers.get("host", "").rsplit(":", 1)[0].lower()
    if host not in _LOCAL_HOSTS:
        return JSONResponse({"detail": "запрос не к 127.0.0.1 (Host) — отклонён"}, 403)
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") in ("cross-site", "same-site") or (origin and not _local_origin(origin)):
        return JSONResponse({"detail": "запросы со страниц сайтов к API запрещены"}, 403)
    token = settings.get("api_token")
    if token and request.headers.get("x-api-key") != token and request.query_params.get("token") != token:
        return JSONResponse({"detail": "нужен ключ API: заголовок X-API-Key или ?token="}, 401)
    return await call_next(request)


@app.exception_handler(ValueError)
async def _value_error(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(exc)[:300] or "неверные данные"}, 400)


def _profile_or_404(pid: int) -> Profile:
    p = db.get_profile(pid)
    if not p:
        raise HTTPException(404, "profile not found")
    return p


def _view(p: Profile) -> dict:
    d = p.model_dump(exclude={"fingerprint"})
    d["running"] = manager.is_running(p.id)
    return d


# ---------- движок ----------
@app.get("/engine")
def engine_status(force: bool = False):
    """Движок (chrome | chromium), его версия, последняя стабильная Chrome и предупреждение, если отстаёт."""
    from ..updates import status
    return status(force)


class AutocheckIn(BaseModel):
    profiles: list[int] = []  # пусто — образцы из Настроек / автоматически


@app.post("/engine/autocheck")
def engine_autocheck(body: Optional[AutocheckIn] = None):
    """Проверка профилей-образцов и уникальности — то же, что происходит само после обновления Chrome."""
    from .. import enginewatch
    pids = [_profile_or_404(i).id for i in (body.profiles if body else [])] or None
    return manager.run(enginewatch.run(manager, progress=manager.scheduler.say, pids=pids), timeout=1800)


@app.get("/engine/autocheck")
def engine_autocheck_last():
    from .. import enginewatch, settings
    return {"samples": enginewatch.sample_profiles(), "seen_version": settings.get("engine_seen_version"),
            "last": settings.get("engine_watch_last")}


class ArrangeIn(BaseModel):
    profiles: list[int] = []  # пусто — все запущенные
    mode: str = "grid"  # grid | cascade
    monitor: int = 0
    cols: int = 0  # 0 — авто
    gap: int = 0


@app.post("/windows/arrange")
def arrange_windows(body: ArrangeIn):
    """Разложить окна запущенных профилей сеткой или каскадом (только Windows)."""
    from .. import winlayout
    pids = body.profiles or [p.id for p in db.list_profiles() if manager.is_running(p.id)]
    try:
        return winlayout.arrange([p for p in pids if manager.is_running(p)], body.mode, body.monitor, body.cols,
                                 body.gap)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/windows/monitors")
def list_monitors():
    from .. import winlayout
    return winlayout.monitors()


@app.get("/bans/stats")
def ban_stats(by: str = "provider", days: int = 0):
    """Статистика банов: by = provider | folder | plan | ip_type | country | os | tag; days=0 — за всё время."""
    from .. import banstats
    return banstats.stats(by, days)


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
    if not 1 <= req.count <= 500:
        raise HTTPException(400, "count — от 1 до 500")
    for line in req.proxies:  # сначала разобрать все прокси: ошибка в 7-й строке не должна оставить 6 профилей
        Proxy.parse(line)
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


class AccountsImportIn(BaseModel):
    lines: str  # строки аккаунтов, по одной на профиль
    format: str = "login:pass:2fa:proxy"  # поля: login, pass, 2fa, proxy, email, email_pass, name, notes, -
    service: str = ""  # Google, Facebook… — сервис аккаунтов
    url: str = ""
    template: Optional[Profile] = None  # настройки новых профилей; по умолчанию — из Настроек
    name_mode: str = "login"  # login | number
    folder: str = ""
    tags: list[str] = []
    free_proxies: bool = False  # строкам без прокси — свободные прокси из списка


@app.post("/profiles/import-accounts")
def import_accounts(body: AccountsImportIn):
    """Купленные аккаунты строками login:pass:2fa:proxy -> по профилю на строку. Любая ошибка в строках —
    400 и ни одного профиля (сначала разбираются все строки)."""
    from .. import accimport, settings
    rows, errors = accimport.parse_lines(body.lines, body.format)
    if errors:
        raise HTTPException(400, {"errors": [{"line": n, "error": e} for n, e in errors]})
    if not rows:
        raise HTTPException(400, "нет строк с аккаунтами")
    free = None
    if body.free_proxies:
        used = {p.proxy_id for p in db.list_profiles() if p.proxy_id}
        free = [px for px in db.list_proxies() if px.id not in used and px.status != "fail"]
    defaults = {k: settings.get(f"default_{k}") for k in ("kind", "engine", "os", "device", "auto_geo")}
    return accimport.create_profiles(manager, rows, service=body.service, url=body.url, template=body.template,
                                     name_mode=body.name_mode, folder=body.folder, tags=body.tags,
                                     free_proxies=free, defaults=defaults)


@app.patch("/profiles/{pid}")
def patch_profile(pid: int, changes: dict):
    p = _profile_or_404(pid)
    if changes.get("status") == "ban" and p.status != "ban":
        from ..notify import notify
        notify("ban", f"Профиль #{p.id} {p.name} (через API)")
    changes.pop("id", None)
    changes.pop("fingerprint", None)
    data = p.model_copy(update=changes)
    data = Profile(**data.model_dump())
    if {"kind", "os", "engine", "device"} & changes.keys():
        data.fingerprint = {}
        manager._prepare_fingerprint(data)
    saved = db.save_profile(data)
    if "status" in changes and changes["status"] != p.status:
        from .. import activity, banstats
        from ..models import STATUSES
        if "ban" in (p.status, saved.status):
            activity.log(pid, "ban", f"метка «{STATUSES.get(p.status, ('—',))[0]}» → "
                                     f"«{STATUSES.get(saved.status, ('—',))[0]}» через API")
        banstats.status_changed(saved, p.status, saved.status, "api")
    return _view(saved)


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


@app.post("/profiles/{pid}/external-check")
def external_check(pid: int, sites: str = ""):
    """Отчёт внешних детекторов. sites — через запятую: pixelscan,creepjs,fingerprint,browserleaks (пусто = все)."""
    from ..external_check import run_external
    _profile_or_404(pid)
    return manager.run(run_external(manager, pid, [s for s in sites.split(",") if s] or None), timeout=600)


@app.get("/profiles/{pid}/health")
def profile_health(pid: int, days: int = 30, kind: str = ""):
    """История проверок: по дням (✓ / ! / ✗) и последние записи с пометкой, что стало хуже. kind: check | external."""
    from .. import health
    p = _profile_or_404(pid)
    k = kind or None
    return {"status": p.status, "ban_reason": p.ban_reason, "captcha_streak": p.captcha_streak,
            "daily": [{**d, "date": d["date"].isoformat()} for d in health.daily([pid], k, days)],
            "recent": health.recent([pid], k, limit=30)}


@app.get("/profiles/{pid}/activity")
def profile_activity(pid: int, days: int = 30, kind: str = "", limit: int = 200):
    """Журнал активности: запуски, остановки (сколько работал, сайты), сценарии, прогрев, прокси, метки."""
    import time
    _profile_or_404(pid)
    return db.list_activity(pid, since=time.time() - days * 86400, kind=kind or None, limit=limit)


class PlanIn(BaseModel):
    plan: str = ""  # generic | google | facebook | instagram | tiktok; пусто — снять


@app.get("/warm-plans")
def warm_plans():
    from ..warmplan import PLANS
    return {k: {"title": v["title"], "days": v["days"]} for k, v in PLANS.items()}


@app.post("/profiles/{pid}/warm-plan")
def set_warm_plan(pid: int, body: PlanIn):
    from .. import warmplan
    _profile_or_404(pid)
    try:
        warmplan.assign(pid, body.plan)
    except KeyError as e:
        raise HTTPException(400, str(e))
    return {"progress": warmplan.progress(db.get_profile(pid))}


@app.post("/profiles/{pid}/warm-plan/run")
def run_warm_plan_day(pid: int, force: bool = False):
    """Выполнить следующий день плана прогрева (без окна)."""
    from ..warmplan import run_day
    _profile_or_404(pid)
    return manager.run(run_day(manager, pid, force=force), timeout=3600)


@app.get("/uniqueness")
def uniqueness_report(ids: str = ""):
    """Совпадения canvas / WebGL / audio / navigator и общие IP между профилями (ids через запятую, пусто — все)."""
    from .. import uniqueness
    pids = [int(x) for x in ids.split(",") if x.strip()] or None
    r = uniqueness.analyze(pids)
    return {**r, "text": uniqueness.format_report(r)}


@app.post("/uniqueness/collect")
def uniqueness_collect(ids: str = ""):
    """Снять хэши отпечатка заново (каждый профиль запускается без окна на несколько секунд)."""
    from .. import uniqueness
    pids = [int(x) for x in ids.split(",") if x.strip()] or [p.id for p in db.list_profiles()]
    return {"collected": manager.run(uniqueness.collect(manager, pids), timeout=None)}


@app.post("/profiles/{pid}/stability")
def stability(pid: int, runs: int = 3):
    """Несколько запусков подряд и сравнение хэшей canvas / WebGL / audio / шрифтов / navigator."""
    from ..check import stability_test
    _profile_or_404(pid)
    try:
        return manager.run(stability_test(manager, pid, runs), timeout=900)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


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


# ---------- пароли и 2FA ----------
@app.get("/profiles/{pid}/accounts")
def list_accounts(pid: int):
    """Аккаунты профиля с паролями и текущими кодами 2FA (API доступен только с этого компьютера)."""
    from .. import totp
    _profile_or_404(pid)
    out = []
    for a in db.list_accounts(pid):
        d = a.model_dump()
        if a.totp:
            try:
                d["code"], d["code_remaining"] = totp.code(a.totp), totp.remaining(a.totp)
            except ValueError as e:
                d["code_error"] = str(e)
        out.append(d)
    return out


@app.post("/profiles/{pid}/accounts")
def add_account(pid: int, acc: Account):
    from .. import totp
    _profile_or_404(pid)
    if acc.totp and not totp.valid(acc.totp):
        raise HTTPException(400, "секрет 2FA не разобран: нужен base32 (A–Z, 2–7) или ссылка otpauth://totp/…")
    acc.id, acc.profile_id = None, pid
    return db.save_account(acc).model_dump()


@app.delete("/accounts/{aid}")
def delete_account(aid: int):
    db.delete_account(aid)
    return {"ok": True}


@app.get("/accounts/{aid}/totp")
def account_totp(aid: int):
    from .. import totp
    a = db.get_account(aid)
    if not a or not a.totp:
        raise HTTPException(404, "account or 2FA secret not found")
    return {"code": totp.code(a.totp), "remaining": totp.remaining(a.totp)}


# ---------- сценарии (RPA) и планировщик ----------
@app.get("/scenarios")
def list_scenarios():
    return db.list_docs("scenarios")


@app.post("/scenarios")
def save_scenario(doc: dict):
    """{"name": "...", "steps": [{"type": "open", "url": "..."}, ...]} — с "id" обновляет существующий."""
    from ..rpa import validate
    err = validate(doc.get("steps", []))
    if err:
        raise HTTPException(400, err)
    return db.save_doc("scenarios", doc)


@app.delete("/scenarios/{sid}")
def delete_scenario(sid: int):
    db.delete_doc("scenarios", sid)
    return {"ok": True}


class RunIn(BaseModel):
    profiles: list[int]
    headless: bool = False
    close_after: bool = True


@app.post("/scenarios/{sid}/run")
def run_scenario(sid: int, body: RunIn):
    """Выполняет сценарий на профилях и возвращает результат по каждому (ждёт окончания)."""
    from ..rpa import jobs
    if not db.get_doc("scenarios", sid):
        raise HTTPException(404, "scenario not found")
    return manager.run(jobs.run_many(manager, sid, body.profiles, body.headless, body.close_after,
                                     log=manager.scheduler.say), timeout=None)


@app.get("/tasks")
def list_tasks():
    return db.list_docs("tasks")


@app.post("/tasks/{tid}/run")
def run_task(tid: int):
    if not db.get_doc("tasks", tid):
        raise HTTPException(404, "task not found")
    return {"result": manager.run(manager.scheduler.run_task(tid, manual=True), timeout=None)}


# ---------- экспорт/импорт профилей, резервные копии ----------
class ExportIn(BaseModel):
    profiles: list[int]
    path: str  # куда сохранить .caprofile
    secrets: bool = False  # включить пароли и 2FA
    password: str = ""  # зашифровать файл паролем (AES-256-GCM, ключ из пароля через scrypt)


@app.post("/profiles/export")
def export_profiles(body: ExportIn):
    from ..backup import export_profiles as do_export
    for pid in body.profiles:
        _profile_or_404(pid)
    return manager.run(do_export(manager, body.profiles, body.path, include_secrets=body.secrets,
                                 password=body.password), timeout=3600)


class ImportIn(BaseModel):
    path: str
    password: str = ""  # для файлов, зашифрованных паролем


@app.post("/profiles/import")
def import_profiles(body: ImportIn):
    from ..backup import import_archive
    try:
        return manager.run(import_archive(manager, body.path, password=body.password), timeout=3600)
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


class ExternalImportIn(BaseModel):
    source: str = "file"  # file | dolphin | adspower
    path: str = ""
    token: str = ""  # Dolphin Anty
    base: str = "http://local.adspower.net:50325"  # AdsPower
    api_key: str = ""
    cookies: bool = True


@app.post("/profiles/import-external")
def import_external(body: ExternalImportIn):
    """Импорт из Dolphin Anty (API-токен), AdsPower (локальный API) или таблицы CSV/XLSX/JSON."""
    from .. import importers
    try:
        if body.source == "dolphin":
            rows = importers.fetch_dolphin(body.token)
        elif body.source == "adspower":
            rows = importers.fetch_adspower(body.base, body.api_key)
        else:
            rows = importers.read_file(body.path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    return manager.run(importers.import_items(manager, rows, manager.scheduler.say, cookies=body.cookies), timeout=None)


@app.post("/backup")
def backup_now():
    from ..backup import backup_all
    return manager.run(backup_all(manager), timeout=7200)


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


@app.post("/proxies/{proxy_id}/change-ip")
def change_proxy_ip(proxy_id: int):
    """Открыть ссылку смены IP мобильного прокси и дождаться нового адреса."""
    from ..proxy.rotate import change_ip
    p = db.get_proxy(proxy_id)
    if not p:
        raise HTTPException(404, "proxy not found")
    return manager.run(change_ip(p, manager.scheduler.say), timeout=300)


def start_api_thread() -> threading.Thread:
    cfg = uvicorn.Config(app, host=API_HOST, port=API_PORT, log_level="warning")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True, name="api-server")
    t.start()
    return t
