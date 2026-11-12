"""Автоотчёты внешних детекторов: pixelscan, CreepJS, fingerprint.com, browserleaks.

Профиль (без окна, с тем же отпечатком и прокси) по очереди открывает сайты, ждёт, пока они досчитают,
и разбирает текст страницы. Итог — список проверок ✓/!/✗ в панели «Отпечаток», как у встроенной проверки.

Сайты меняют вёрстку, поэтому разбор идёт по тексту (подписи полей), а не по CSS-классам. Если сайт
изменился и подписи не нашлись, в отчёте будет «не удалось разобрать» — это повод поправить разборщик.
"""
import asyncio
import re
import time
from typing import Callable, Optional

from . import db

SITES = {
    "pixelscan": ("Pixelscan", "https://pixelscan.net/fingerprint-check"),
    "creepjs": ("CreepJS", "https://abrahamjuliot.github.io/creepjs/"),
    "fingerprint": ("Fingerprint.com", "https://demo.fingerprint.com/playground"),
    "browserleaks": ("BrowserLeaks", "https://browserleaks.com/ip"),
}

Item = tuple[str, str, str]  # (ok|warn|fail, проверка, подробности)


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


async def _wait_text(page, markers: list[str], timeout: float = 45, ready: Optional[str] = None,
                     busy: tuple[str, ...] = ()) -> str:
    """Ждёт, пока в тексте страницы появятся все маркеры (сайт досчитал). Возвращает текст.
    ready — регулярное выражение, которое тоже должно найтись; busy — фразы «ещё считаю»."""
    end = time.monotonic() + timeout
    text = ""
    while time.monotonic() < end:
        try:
            text = await page.evaluate("document.body ? document.body.innerText : ''")
        except Exception:  # noqa: BLE001 — страница ещё перезагружается
            text = ""
        if all(m in text for m in markers) and (not ready or re.search(ready, text))                 and not any(b in text for b in busy):
            await asyncio.sleep(1.5)  # даём дорисовать последние блоки
            return await page.evaluate("document.body.innerText")
        await asyncio.sleep(1)
    return text


def _after(lines: list[str], label: str) -> Optional[str]:
    """Значение в строке после подписи («Bot» → «Not detected»)."""
    for i, ln in enumerate(lines):
        if ln == label and i + 1 < len(lines):
            return lines[i + 1]
    return None


# ---------------- pixelscan ----------------
_PX_NOISE = ("Check Geo API", "Best proxy providers", "How to Fix it?", "Restart")


async def _pixelscan(page, ctx) -> list[Item]:
    text = await _wait_text(page, ["Your Browser Fingerprint is", "Bot check"], 90,
                            ready=r"Your Browser Fingerprint is\s+(consistent|inconsistent)",
                            busy=("Collecting Data", "scanning"))
    if "Your Browser Fingerprint is" not in text:
        return [("warn", "Pixelscan", "не удалось разобрать страницу (сайт изменился или не загрузился)")]
    lines = _lines(text[text.find("Your Browser Fingerprint is"):])
    verdict = re.search(r"Your Browser Fingerprint is\s+(\w+)", text)
    cards: dict[str, str] = {}
    for i, ln in enumerate(lines):
        if ln in ("Browser", "Location", "Proxy", "Fingerprint", "Bot check") and ln not in cards:
            j = i - 1
            # строки-шум между значением и подписью: кнопки и служебный список браузеров «Chrome-29-0,Opera-16-0»
            while j >= 0 and (lines[j] in _PX_NOISE or re.match(r"^[\w .-]+-\d+-\d+,", lines[j])):
                j -= 1
            cards[ln] = lines[j] if j >= 0 else "?"
    items: list[Item] = []
    v = (verdict.group(1) if verdict else "?").lower()
    proxy = cards.get("Proxy", "")
    proxy_only = v == "inconsistent" and "no " not in proxy.lower() and \
        "no masking" in cards.get("Fingerprint", "").lower() and "no automated" in cards.get("Bot check", "").lower()
    items.append(("ok" if v == "consistent" else "warn" if proxy_only else "fail", "Pixelscan: итог",
                  f"fingerprint {v}" + (" — только из-за прокси/VPN, сам отпечаток чистый" if proxy_only else "")))
    fpc = cards.get("Fingerprint", "?")
    items.append(("ok" if fpc.lower().startswith("no ") else "fail", "Pixelscan: маскировка отпечатка", fpc))
    bot = cards.get("Bot check", "?")
    items.append(("ok" if bot.lower().startswith("no ") else "fail", "Pixelscan: бот-проверка", bot))
    items.append(("ok" if proxy.lower().startswith("no ") else "warn", "Pixelscan: прокси",
                  proxy + ("" if proxy.lower().startswith("no ") else " — IP похож на прокси/VPN/хостинг (свойство IP, не браузера)")))
    items.append(("ok", "Pixelscan: браузер и место", f"{cards.get('Browser', '?')}; {cards.get('Location', '?')}"))
    return items


# ---------------- CreepJS ----------------
async def _creepjs(page, ctx) -> list[Item]:
    text = await _wait_text(page, ["% headless", "% stealth", "% like headless"], 60)
    def pct(rx):
        m = re.search(rx, text)
        return int(m.group(1)) if m else None
    like, headless, stealth = pct(r"(\d+)% like headless"), pct(r"(\d+)% headless"), pct(r"(\d+)% stealth")
    if headless is None:
        return [("warn", "CreepJS", "не удалось разобрать страницу (сайт изменился или не загрузился)")]
    items: list[Item] = [
        ("ok" if headless == 0 else "fail", "CreepJS: headless", f"{headless}%"),
        ("ok" if stealth == 0 else "fail", "CreepJS: stealth (следы подмены)", f"{stealth}%"),
        ("ok" if (like or 0) <= 40 else "warn", "CreepJS: like headless",
         f"{like}% — у обычного Chrome на ПК тоже 20–40% (нет Android-API, светлая тема)"),
    ]
    lies = re.search(r"lies\s*\((\d+)\)", text)
    if lies:
        n = int(lies.group(1))
        items.append(("ok" if n == 0 else "fail", "CreepJS: найдено лжи (lies)", str(n)))
    if "HeadlessChrome" in text:
        items.append(("fail", "CreepJS: UA в воркере", "виден HeadlessChrome"))
    fp_id = re.search(r"FP ID:\s*([0-9a-f]{12})", text)
    if fp_id:
        items.append(("ok", "CreepJS: FP ID", fp_id.group(1) + "…"))
    return items


# ---------------- fingerprint.com ----------------
_FPC_SIGNALS = [  # подпись, считать ли проблемой отпечатка (fail) или свойством IP/окружения (warn)
    ("Bot", "fail"), ("Browser Tampering", "warn"), ("Virtual Machine", "warn"), ("Incognito Mode", "warn"),
    ("Privacy Settings", "warn"), ("Developer Tools", "warn"), ("VPN", "warn"), ("Proxy Detection", "warn"),
    ("IP Blocklist", "warn"),
]


async def _fingerprint(page, ctx) -> list[Item]:
    text = await _wait_text(page, ["Suspect Score", "Browser Tampering", "Your Visitor ID is"], 60)
    lines = _lines(text)
    if "Suspect Score" not in text:
        return [("warn", "Fingerprint.com", "не удалось разобрать страницу (сайт изменился или не загрузился)")]
    items: list[Item] = []
    vid = _after(lines, "Your Visitor ID is")
    if vid:
        items.append(("ok", "Fingerprint.com: Visitor ID", f"{vid} ({_after(lines, 'Browser') or '?'}, "
                                                           f"{_after(lines, 'Operating System') or '?'})"))
    for label, severity in _FPC_SIGNALS:
        val = _after(lines, label)
        if val is None:
            continue
        good = val.lower().startswith("not detected")
        note = ""
        if not good and label == "Developer Tools":
            note = (" — так сайт видит управление через Playwright (CDP Runtime). Включите Patchright: "
                    "Настройки → Движок → Управление браузером")
        elif not good and label == "Browser Tampering":
            note = (" — на демо-странице этот сигнал нестабилен: при частых проверках с одного IP он появляется "
                    "и у Chrome совсем без подмен. Смотрите на него в сумме с pixelscan «masking» и CreepJS «stealth»")
        elif not good and label in ("VPN", "Proxy Detection", "IP Blocklist"):
            note = " — свойство IP (прокси/VPN/хостинг), а не отпечатка"
        items.append(("ok" if good else severity, f"Fingerprint.com: {label}", val + note))
    score = _after(lines, "Suspect Score")
    if score and score.isdigit():
        s = int(score)
        items.append(("ok" if s < 20 else "warn", "Fingerprint.com: Suspect Score",
                      f"{s} (0 — чисто; растёт от прокси/VPN, VM, DevTools и т.п.)"))
    return items


# ---------------- browserleaks ----------------
_P0F_OS = {"windows": "windows", "macos": "mac", "linux": "linux", "android": "android"}


async def _browserleaks(page, ctx, profile=None) -> list[Item]:
    text = await _wait_text(page, ["IP Address", "WebRTC Leak Test", "TCP/IP Fingerprint"], 45)
    rows: dict[str, str] = {}
    for ln in text.splitlines():
        if "\t" in ln:
            k, _, v = ln.partition("\t")
            rows.setdefault(k.strip(), v.strip())
    ip = rows.get("IP Address")
    if not ip:
        return [("warn", "BrowserLeaks", "не удалось разобрать страницу (сайт изменился или не загрузился)")]
    items: list[Item] = [("ok", "BrowserLeaks: IP", f"{ip} — {rows.get('Country', '?')}, {rows.get('ISP', '?')}")]
    usage, network = rows.get("Usage Type", ""), rows.get("Network", "")
    if re.search(r"hosting|vpn|tor|proxy|data ?center", f"{usage} {network}", re.I):
        items.append(("warn", "BrowserLeaks: тип IP", f"{usage}; {network} — IP хостинга/VPN, лучше резидентный/мобильный прокси"))
    pub = rows.get("Public IP Address", "")
    if pub:
        ok = pub in ("n/a", "", ip)
        items.append(("ok" if ok else "fail", "BrowserLeaks: WebRTC", "публичный IP = IP соединения" if pub == ip else
                      ("WebRTC не раскрывает адрес" if ok else f"WebRTC раскрывает {pub} ≠ {ip}")))
    local = rows.get("Local IP Address", "")
    if local:
        items.append(("ok" if local == "n/a" else "warn", "BrowserLeaks: локальный IP в WebRTC", local))
    m = re.search(r"IPv6 Address\s*\n?\s*([0-9a-fA-F:]{6,})", text)
    if m:
        items.append(("warn", "BrowserLeaks: IPv6", f"виден IPv6 {m.group(1)} — убедитесь, что это адрес прокси/VPN, а не провайдера"))
    tz_ip = rows.get("Timezone", "").split(" ")[0]
    if tz_ip:
        tz_br = await page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
        items.append(("ok" if tz_ip == tz_br else "fail", "BrowserLeaks: таймзона IP = браузера", f"IP: {tz_ip}, браузер: {tz_br}"))
    os_guess = rows.get("OS", "")
    if os_guess and profile is not None:
        want = _P0F_OS.get("android" if profile.kind == "mobile" else profile.os, "")
        same = want and want in os_guess.lower()
        items.append(("ok" if same else "warn", "BrowserLeaks: ОС по TCP/IP (p0f)",
                      f"{os_guess}" + ("" if same else " — сетевой стек прокси/VPN выглядит как другая ОС; "
                                                      "браузер на это не влияет, выбирайте прокси под ОС профиля")))
    al = rows.get("Accept-Language", "")
    if al and profile is not None:
        items.append(("ok" if al.lower().startswith(profile.locale.lower()) else "fail", "BrowserLeaks: Accept-Language",
                      f"{al} (язык профиля {profile.locale})"))
    if rows.get("JA4"):
        items.append(("ok", "BrowserLeaks: TLS (JA4)", rows["JA4"] + " — настоящий TLS Chrome"))
    return items


_PARSERS = {"pixelscan": _pixelscan, "creepjs": _creepjs, "fingerprint": _fingerprint, "browserleaks": _browserleaks}


async def run_external(manager, pid: int, sites: Optional[list[str]] = None,
                       progress: Optional[Callable[[str], None]] = None) -> dict:
    p = db.get_profile(pid)
    if not p:
        raise KeyError(pid)
    keys = [k for k in (sites or list(SITES)) if k in SITES]
    was_running = manager.is_running(pid)
    sess = manager.sessions[pid] if was_running else await manager.start(pid, headless=True)
    p = db.get_profile(pid) or p  # язык и таймзона могли обновиться по IP при запуске
    out: dict = {"time": time.strftime("%Y-%m-%d %H:%M"), "sites": {}}
    try:
        for key in keys:
            title, url = SITES[key]
            if progress:
                progress(f"#{pid}: {title}…")
            page = await sess.context.new_page()
            res: dict = {"title": title, "url": url, "items": []}
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                parser = _PARSERS[key]
                items = await (parser(page, sess.context, p) if key == "browserleaks" else parser(page, sess.context))
                from .hostos import mismatch, warning
                if key == "fingerprint" and mismatch(p.kind, p.os):  # у такого профиля Tampering ожидаем — объясняем
                    items = [(st, t, f"{warning(p.kind, p.os, short=True)}. Возьмите ОС как у ПК"
                              if t.endswith("Browser Tampering") and st != "ok" else d) for st, t, d in items]
                res["items"] = [list(i) for i in items]
            except Exception as e:  # noqa: BLE001
                res["error"] = str(e).splitlines()[0][:200]
            finally:
                try:
                    await page.close()
                except Exception:  # noqa: BLE001
                    pass
            out["sites"][key] = res
    finally:
        if not was_running:
            await manager.stop(pid)
    all_items = [i for s in out["sites"].values() for i in s["items"]]
    out["ok"] = sum(1 for s, *_ in all_items if s == "ok")
    out["warn"] = sum(1 for s, *_ in all_items if s == "warn")
    out["fail"] = sum(1 for s, *_ in all_items if s == "fail")
    from . import activity, health
    activity.log(pid, "check", f"внешние детекторы: ✓ {out['ok']}  ! {out['warn']}  ✗ {out['fail']}")
    out["worse"] = [t for _, t, *_ in health.record(pid, "external", out)["worse"]]
    p = db.get_profile(pid)
    if p:
        p.last_external = out
        db.save_profile(p)
        if out["fail"]:
            from .notify import notify
            notify("check_fail", f"Детекторы, профиль #{pid} {p.name}: ✗ {out['fail']}\n" + "\n".join(
                f"✗ {t} — {d}" for s in out["sites"].values() for st, t, d in s["items"] if st == "fail"))
    return out


def format_external(r: dict) -> str:
    mark = {"ok": "✓", "warn": "!", "fail": "✗"}
    lines = [f"Внешние детекторы {r['time']}: ✓ {r.get('ok', 0)}   ! {r.get('warn', 0)}   ✗ {r.get('fail', 0)}"]
    for key, s in r.get("sites", {}).items():
        lines.append(f"  {s['title']} — {s['url']}")
        if s.get("error"):
            lines.append(f"    ✗ не открылся: {s['error']}")
        for st, title, detail in s.get("items", []):
            title = title.split(": ", 1)[-1]
            lines.append(f"    {mark.get(st, '?')} {title}" + (f" — {detail}" if detail else ""))
    return "\n".join(lines)
