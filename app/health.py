"""Здоровье профилей: история проверок отпечатка и отчётов внешних детекторов.

Каждая проверка («Проверить отпечаток», «Отчёт внешних детекторов», вручную или по расписанию)
сохраняется в таблицу health: сколько пунктов ✓ / ! / ✗ и их статусы. По истории строится график по
датам, а при ухудшении — пункт стал ✗ или ✗ стало больше, чем в прошлый раз, — уходит уведомление
в Telegram (событие health_worse). Переходы ✓ → ! только видны в истории: часть сигналов (например,
«Browser Tampering» на fingerprint.com) шумит и без причины, уведомлять о них — спамить.
"""
import datetime as dt
import time
from typing import Iterable, Optional

from . import db

KINDS = {"check": "Проверка отпечатка", "external": "Внешние детекторы"}
_RANK = {"ok": 0, "warn": 1, "fail": 2}
_seeded = False


def _items(kind: str, result: dict) -> list[list[str]]:
    if kind == "check":
        raw = result.get("items", [])
    else:
        raw = [i for s in (result.get("sites") or {}).values() for i in s.get("items", [])]
        raw += [["fail", f"{s.get('title', k)}: не открылся", s["error"]]
                for k, s in (result.get("sites") or {}).items() if s.get("error")]
    return [[st, title, (detail or "")[:200]] for st, title, detail in raw]


def _ts(text: str) -> float:
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp()
    except (TypeError, ValueError):
        return time.time()


def compare(prev: Optional[dict], items: list) -> tuple[list, list]:
    """-> (стало ✗ или новые ✗, прочие ухудшения ✓ → !) по сравнению с прошлой записью."""
    if not prev:
        return [], []
    before = {title: st for st, title, *_ in prev["items"]}
    worse_fail, worse_warn = [], []
    for st, title, *rest in items:
        was = before.get(title)
        if st == "fail" and was != "fail":
            worse_fail.append([st, title, rest[0] if rest else "", was or "нет"])
        elif st == "warn" and was == "ok":
            worse_warn.append([st, title, rest[0] if rest else "", was])
    return worse_fail, worse_warn


def record(pid: int, kind: str, result: dict) -> dict:
    """Сохранить результат в историю, сравнить с прошлым разом и уведомить, если стало хуже."""
    ensure_seeded()
    items = _items(kind, result)
    prev = next(iter(reversed(db.list_health(pid, kind=kind))), None)
    worse_fail, worse_warn = compare(prev, items)
    n = {s: sum(1 for st, *_ in items if st == s) for s in _RANK}
    db.add_health(pid, time.time(), kind, n["ok"], n["warn"], n["fail"], items)
    if worse_fail or (prev and n["fail"] > prev["fail"]):
        from .notify import notify
        p = db.get_profile(pid)
        lines = [f"✗ {t} — {d}" + (f" (было: {_mark(was)})" if was != "нет" else "") for _, t, d, was in worse_fail]
        notify("health_worse", f"Профиль #{pid} {p.name if p else ''}, {KINDS[kind].lower()}: "
                               f"✗ {prev['fail'] if prev else 0} → {n['fail']}\n" + "\n".join(lines[:15]))
    return {"worse": worse_fail, "warn": worse_warn, "prev": prev}


def _mark(st: str) -> str:
    return {"ok": "✓", "warn": "!", "fail": "✗"}.get(st, st)


def ensure_seeded():
    """Один раз переносит в историю последние проверки, сделанные до появления истории."""
    global _seeded
    if _seeded:
        return
    _seeded = True
    have = db.health_profiles()
    for p in db.list_profiles():
        if p.id in have:
            continue
        for kind, res in (("check", p.last_check), ("external", p.last_external)):
            if res and res.get("time"):
                items = _items(kind, res)
                n = {s: sum(1 for st, *_ in items if st == s) for s in _RANK}
                db.add_health(p.id, _ts(res["time"]), kind, n["ok"], n["warn"], n["fail"], items)


def daily(pids: Optional[Iterable[int]] = None, kind: Optional[str] = None, days: int = 30) -> list[dict]:
    """По дням за последние days дней: сумма ✓ / ! / ✗ последней записи дня каждого профиля и вида проверки.
    -> [{"date": date, "ok", "warn", "fail", "runs", "profiles"}] — все дни подряд, пустые тоже."""
    ensure_seeded()
    start = dt.date.today() - dt.timedelta(days=days - 1)
    since = dt.datetime.combine(start, dt.time()).timestamp()
    wanted = set(pids) if pids is not None else None
    last: dict = {}  # (день, профиль, вид) -> запись (последняя за день)
    runs: dict = {}
    for r in db.list_health(since=since, kind=kind):
        if wanted is not None and r["profile_id"] not in wanted:
            continue
        day = dt.date.fromtimestamp(r["ts"])
        last[(day, r["profile_id"], r["kind"])] = r
        runs[day] = runs.get(day, 0) + 1
    out = []
    for i in range(days):
        day = start + dt.timedelta(days=i)
        recs = [r for (d, *_), r in last.items() if d == day]
        out.append({"date": day, "ok": sum(r["ok"] for r in recs), "warn": sum(r["warn"] for r in recs),
                    "fail": sum(r["fail"] for r in recs), "runs": runs.get(day, 0),
                    "profiles": len({r["profile_id"] for r in recs})})
    return out


def recent(pids: Optional[Iterable[int]] = None, kind: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Последние записи (новые сверху) с отметкой, что стало хуже/лучше по сравнению с предыдущей того же вида."""
    ensure_seeded()
    wanted = set(pids) if pids is not None else None
    rows = [r for r in db.list_health(kind=kind) if wanted is None or r["profile_id"] in wanted]
    prev: dict = {}
    out = []
    for r in rows:
        key = (r["profile_id"], r["kind"])
        worse_fail, worse_warn = compare(prev.get(key), r["items"])
        p = prev.get(key)
        better = bool(p) and r["fail"] < p["fail"]
        out.append({**r, "worse": worse_fail + worse_warn, "better": better})
        prev[key] = r
    return list(reversed(out))[:limit]
