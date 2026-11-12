"""Статистика банов: какой провайдер прокси, папка или план прогрева работает хуже.

Каждый бан (вручную, автоматически по признакам на странице, через API) записывается в таблицу bans
вместе со «снимком» профиля на момент бана: папка, прокси и его провайдер, тип IP, план прогрева, ОС,
сколько дней профиль прожил. Запись остаётся и после удаления профиля — забаненные профили обычно
удаляют, а без них статистика была бы слишком оптимистичной.

Доля банов в группе = банов / (профилей сейчас + удалённых забаненных) этой группы.
Провайдер прокси — поле «Провайдер» прокси, если задано, иначе домен хоста (gate.smartproxy.com →
smartproxy.com); для прокси по IP — первые два октета (185.12.*.*), так прокси одного провайдера
обычно оказываются вместе.
"""
import json
import re
import time
from collections import defaultdict
from typing import Optional

from . import db
from .models import Profile, Proxy

DIMENSIONS = {
    "provider": "Провайдер прокси",
    "folder": "Папка",
    "plan": "План прогрева",
    "ip_type": "Тип IP прокси",
    "country": "Страна прокси",
    "os": "ОС профиля",
    "tag": "Тег",
}
_SLD = {"co", "com", "net", "org", "gov", "edu", "ac"}  # co.uk, com.ru… — домен из трёх частей
_IP_TYPES = {"residential": "резидентный", "mobile": "мобильный", "hosting": "хостинг / ЦОД", "proxy": "прокси / VPN"}

db._conn.executescript("""
CREATE TABLE IF NOT EXISTS bans(
  id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL, ts REAL NOT NULL, unbanned REAL,
  how TEXT NOT NULL, reason TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS bans_profile ON bans(profile_id, ts);
""")
_seeded = False


def provider(px: Optional[Proxy]) -> str:
    if not px:
        return "без прокси"
    if getattr(px, "provider", ""):
        return px.provider.strip()
    host = px.host.strip().lower().strip("[]")
    if re.fullmatch(r"[\d.]+", host):
        parts = host.split(".")
        return f"{parts[0]}.{parts[1]}.*.*" if len(parts) == 4 else host
    if ":" in host:  # IPv6
        return ":".join(host.split(":")[:2]) + ":*"
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SLD:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _created(p: Profile) -> float:
    if getattr(p, "created", 0):
        return p.created
    first = db._conn.execute("SELECT MIN(ts) FROM activity WHERE profile_id=?", (p.id,)).fetchone()[0]
    return first or 0


def snapshot(p: Profile) -> dict:
    """Чем был профиль в момент бана — по этим полям считается статистика."""
    px = db.get_proxy(p.proxy_id)
    created = _created(p)
    from .warmplan import PLANS
    plan = (p.warm_plan or {}).get("plan", "")
    return {"name": p.name, "folder": p.folder, "tags": list(p.tags), "os": p.os, "kind": p.kind,
            "proxy_id": p.proxy_id, "provider": provider(px), "ip_type": px.ip_type if px else "",
            "country": px.country if px else "", "plan": plan, "plan_title": PLANS.get(plan, {}).get("title", ""),
            "plan_done": len((p.warm_plan or {}).get("done", [])), "created": created}


def record_ban(p: Profile, how: str, reason: str = "") -> None:
    """Вызывается, когда профилю ставят метку «Бан» (how: manual | auto | api)."""
    with db._lock:
        db._conn.execute("INSERT INTO bans(profile_id, ts, how, reason, data) VALUES(?,?,?,?,?)",
                         (p.id, time.time(), how, reason[:300], json.dumps(snapshot(p), ensure_ascii=False)))
        db._conn.commit()


def record_unban(pid: int) -> None:
    with db._lock:
        db._conn.execute("UPDATE bans SET unbanned=? WHERE profile_id=? AND unbanned IS NULL", (time.time(), pid))
        db._conn.commit()


def status_changed(p: Profile, old: str, new: str, how: str, reason: str = "") -> None:
    if new == "ban" and old != "ban":
        record_ban(p, how, reason)
    elif old == "ban" and new != "ban":
        record_unban(p.id)


def list_bans(since: float = 0) -> list[dict]:
    ensure_seeded()
    with db._lock:
        rows = db._conn.execute("SELECT id, profile_id, ts, unbanned, how, reason, data FROM bans WHERE ts >= ? "
                                "ORDER BY ts", (since,)).fetchall()
    return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


def ensure_seeded():
    """Профили с меткой «Бан», поставленной до появления статистики, — одна запись на профиль."""
    global _seeded
    if _seeded:
        return
    _seeded = True
    with db._lock:
        have = {r[0] for r in db._conn.execute("SELECT DISTINCT profile_id FROM bans").fetchall()}
    for p in db.list_profiles():
        if p.status != "ban" or p.id in have:
            continue
        last = db.list_activity(p.id, kind="ban", limit=1)
        ts = last[0]["ts"] if last else time.time()
        with db._lock:
            db._conn.execute("INSERT INTO bans(profile_id, ts, how, reason, data) VALUES(?,?,?,?,?)",
                             (p.id, ts, "seed", p.ban_reason[:300], json.dumps(snapshot(p), ensure_ascii=False)))
            db._conn.commit()


def _keys(dim: str, d: dict) -> list[str]:
    if dim == "provider":
        return [d.get("provider") or "без прокси"]
    if dim == "folder":
        return [d.get("folder") or "без папки"]
    if dim == "plan":
        return [d.get("plan_title") or d.get("plan") or "без плана"]
    if dim == "ip_type":
        return [_IP_TYPES.get(d.get("ip_type", ""), d.get("ip_type") or "неизвестно / без прокси")]
    if dim == "country":
        return [d.get("country") or "—"]
    if dim == "os":
        return [{"windows": "Windows", "macos": "macOS", "linux": "Linux", "android": "Android"}.get(d.get("os"), "—")]
    if dim == "tag":
        return list(d.get("tags") or []) or ["без тегов"]
    return ["—"]


def stats(dim: str = "provider", days: int = 0) -> dict:
    """-> {"dim", "rows": [{"group", "profiles", "bans", "rate", "active_bans", "avg_life_days", "last_ban"}],
    "total_profiles", "total_bans"}. days=0 — за всё время (баны раньше периода не считаются)."""
    if dim not in DIMENSIONS:
        raise ValueError(f"неизвестный разрез «{dim}» — можно: {', '.join(DIMENSIONS)}")
    since = time.time() - days * 86400 if days else 0
    bans = list_bans(since)
    profiles = {p.id: p for p in db.list_profiles()}
    groups: dict = defaultdict(lambda: {"profiles": set(), "bans": 0, "banned": set(), "active": set(),
                                        "life": [], "last": 0.0})
    # кто в группе: забаненные — по снимку на момент бана (потом их часто переносят в папку «бан»),
    # остальные — по текущему состоянию
    banned_ids = {b["profile_id"] for b in bans}
    for p in profiles.values():
        if p.id in banned_ids:
            continue
        for k in _keys(dim, snapshot(p)):
            groups[k]["profiles"].add(p.id)
    for b in bans:
        d = b["data"]
        for k in _keys(dim, d):
            g = groups[k]
            g["bans"] += 1
            g["banned"].add(b["profile_id"])
            g["profiles"].add(b["profile_id"])  # удалённый или перемещённый после бана — тоже был в группе
            if b["unbanned"] is None and b["profile_id"] in profiles and profiles[b["profile_id"]].status == "ban":
                g["active"].add(b["profile_id"])
            if d.get("created"):
                g["life"].append((b["ts"] - d["created"]) / 86400)
            g["last"] = max(g["last"], b["ts"])
    rows = []
    for k, g in groups.items():
        n = len(g["profiles"])
        rows.append({"group": k, "profiles": n, "bans": g["bans"], "banned_profiles": len(g["banned"]),
                     "rate": round(len(g["banned"]) / n, 3) if n else 0.0, "active_bans": len(g["active"]),
                     "avg_life_days": round(sum(g["life"]) / len(g["life"]), 1) if g["life"] else None,
                     "last_ban": time.strftime("%Y-%m-%d", time.localtime(g["last"])) if g["last"] else ""})
    rows.sort(key=lambda r: (-r["rate"], -r["bans"], r["group"]))
    return {"dim": dim, "title": DIMENSIONS[dim], "days": days, "rows": rows, "total_profiles": len(profiles),
            "total_bans": len(bans), "banned_profiles": len({b["profile_id"] for b in bans})}


def format_stats(s: dict) -> str:
    lines = [f"{s['title']}: банов {s['total_bans']}" + (f" за {s['days']} дн." if s["days"] else " за всё время")]
    for r in s["rows"]:
        life = f", жили до бана в среднем {r['avg_life_days']} дн." if r["avg_life_days"] is not None else ""
        lines.append(f"  {r['group']}: {r['banned_profiles']} из {r['profiles']} ({r['rate'] * 100:.0f}%){life}")
    return "\n".join(lines)
