"""Автопроверка после обновления Chrome.

Обновление движка — самый частый момент, когда маскировка ломается незаметно: в новом Chrome появляется
API, меняется поведение подменённой функции или формат строки видеокарты. Поэтому, как только версия
движка сменилась (Chrome обновился сам, или нажали «Обновить движок»), профили-образцы сами проходят
встроенную проверку и проверку уникальности (снимаются хэши canvas / WebGL / audio без окна). Результат
сравнивается с прошлой проверкой этих профилей и уходит в Telegram (событие engine_update); при
ухудшении — с пометкой ⚠, а новые ✗ дополнительно попадают в «Здоровье профилей» (health_worse).

Образцы — из Настроек (engine_watch_profiles); если не выбраны — до двух профилей автоматически:
последний проверенный ПК-профиль и телефонный, у которых прокси не помечен нерабочим.
Проверка идёт в цикле планировщика: работает, пока открыт claudeAnty.
"""
import logging
import time
from typing import Callable, Optional

from . import db, settings
from .engines import chrome_version as cv

log = logging.getLogger("claudeanty.enginewatch")

POLL = 600  # секунд: как часто сверять версию движка
_last_poll = 0.0
_busy = False


def current() -> str:
    try:
        return cv.engine_version()
    except Exception:  # noqa: BLE001 — движок не найден
        return ""


def sample_profiles() -> list[int]:
    chosen = [i for i in (settings.get("engine_watch_profiles") or []) if db.get_profile(i)]
    if chosen:
        return chosen
    proxies = {px.id: px for px in db.list_proxies()}
    out = []
    for kind in ("desktop", "mobile"):
        cands = [p for p in db.list_profiles() if p.kind == kind and p.engine == "chromium"
                 and not (p.proxy_id and (p.proxy_id not in proxies or proxies[p.proxy_id].status == "fail"))]
        if not cands:
            continue
        # сначала те, что уже проверялись (есть с чем сравнить), из них — проверенный последним
        cands.sort(key=lambda p: (bool(p.last_check), (p.last_check or {}).get("time", ""), p.id), reverse=True)
        out.append(cands[0].id)
    return out


async def run(manager, reason: str = "", progress: Callable[[str], None] = lambda s: None,
              pids: Optional[list[int]] = None) -> dict:
    """Проверка образцов + уникальность. -> {"version", "profiles": [...], "worse": bool, "text"}."""
    global _busy
    from . import uniqueness
    from .check import run_check
    _busy = True
    try:
        version = current()
        pids = pids or sample_profiles()
        if not pids:
            return {"version": version, "profiles": [], "worse": False,
                    "text": "нет профилей-образцов: создайте Chromium-профиль или выберите образцы в Настройках"}
        lines, worse = [], False
        rows = []
        # уникальность «до»: хэши образцов обновит сама проверка (run_check пишет их в историю отпечатка)
        before = {(g["part"], tuple(g["pids"])) for g in uniqueness.analyze()["groups"] if g["level"] == "fail"}
        for pid in pids:
            p = db.get_profile(pid)
            prev = (p.last_check or {}) if p else {}
            if manager.is_running(pid):
                lines.append(f"#{pid} {p.name}: запущен — пропущен, проверьте вручную")
                continue
            progress(f"Автопроверка Chrome {version}: #{pid} «{p.name}»…")
            try:
                r = await run_check(manager, pid)
            except Exception as e:  # noqa: BLE001 — мёртвый прокси и т.п.
                lines.append(f"✗ #{pid} {p.name}: не запустился — {str(e).splitlines()[0][:150]}")
                worse = True
                rows.append({"pid": pid, "error": str(e)[:200]})
                continue
            was = f" (было ✓ {prev.get('ok')}/{prev.get('total')}, ✗ {prev.get('fail')})" if prev else ""
            new_fail = list(r.get("worse") or [])
            mark = "✗" if r["fail"] else "✓"
            lines.append(f"{mark} #{pid} {p.name}: ✓ {r['ok']}/{r['total']}, ✗ {r['fail']}{was}")
            lines += [f"    стало ✗: {t}" for t in new_fail[:8]]
            if new_fail or (prev and r["fail"] > prev.get("fail", 0)):
                worse = True
            rows.append({"pid": pid, "ok": r["ok"], "total": r["total"], "fail": r["fail"], "new_fail": new_fail})
        # уникальность: свежие хэши образцов против последних хэшей остальных профилей
        un = uniqueness.analyze()
        bad = [g for g in un["groups"] if g["level"] == "fail" and set(g["pids"]) & set(pids)]
        new_bad = [g for g in bad if (g["part"], tuple(g["pids"])) not in before]
        if bad:
            lines.append("✗ Уникальность: " + "; ".join(f"{g['part']} совпадает у #{', #'.join(map(str, g['pids']))}"
                                                         for g in bad[:5]))
            worse = worse or bool(new_bad)
        else:
            lines.append("✓ Уникальность: canvas / WebGL / audio образцов не совпадают с другими профилями")
        head = (f"⚠ После обновления Chrome {reason or version} маскировка стала хуже" if worse
                else f"Chrome {reason or version}: образцы проверены, всё как раньше")
        text = head + "\n" + "\n".join(lines)
        res = {"version": version, "time": time.strftime("%Y-%m-%d %H:%M"), "profiles": rows, "worse": worse,
               "uniqueness_fail": [g["part"] for g in bad], "text": text}
        settings.set("engine_watch_last", res)
        from .notify import notify
        notify("engine_update", text)
        return res
    finally:
        _busy = False


async def poll(manager, say: Callable[[str], None]) -> Optional[dict]:
    """Вызывается из цикла планировщика: раз в POLL секунд сверяет версию; сменилась — проверка образцов."""
    global _last_poll
    now = time.time()
    if _busy or now - _last_poll < POLL:
        return None
    _last_poll = now
    version = current()
    if not version:
        return None
    seen = settings.get("engine_seen_version")
    if not seen:  # первый запуск с этой функцией — запоминаем версию, проверять не с чем
        settings.set("engine_seen_version", version)
        return None
    if version == seen:
        return None
    settings.set("engine_seen_version", version)
    if not settings.get("engine_watch"):
        return None
    reason = f"{seen} → {version}"
    say(f"Движок обновился: {reason}. Автопроверка профилей-образцов…")
    res = await run(manager, reason, say)
    say(res["text"].splitlines()[0])
    return res
