"""Встроенная проверка отпечатка профиля.

Профиль запускается без окна (с тем же отпечатком и прокси), открывает внутреннюю страницу,
которая собирает данные в странице и в Web Worker, WebRTC-адреса, таймзону и т.п.
Дальше Python сравнивает всё между собой и с IP прокси и выдаёт список проверок.
"""
import time
from pathlib import Path

from . import db
from .proxy.checker import locale_for

URL = "https://check.claudeanty.test/"
SITES = ["https://abrahamjuliot.github.io/creepjs/", "https://pixelscan.net/", "https://browserleaks.com/webrtc"]

_DETECT = (Path(__file__).parent / "fingerprints" / "inject" / "detect.js").read_text(encoding="utf-8")

DETECT_TITLES = {
    "cdp_runtime": "Следы автоматизации (CDP) не видны",
    "native_functions": "Подменённые функции неотличимы от нативных",
    "no_artifacts": "Нет посторонних глобальных переменных",
    "stack_clean": "Стек ошибок без следов подмены",
    "canvas_stable": "Canvas стабилен (шум не обнаруживается)",
    "audio_stable": "Audio стабилен (шум не обнаруживается)",
    "iframe_consistent": "Iframe совпадает со страницей",
    "window_screen": "Окно и экран согласованы",
    "headless_signs": "Нет признаков headless (как в CreepJS)",
}

PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head><body><script>
window.collect = async () => {
  const out = {};
  let gl = null;
  try { const c = document.createElement('canvas').getContext('webgl'); const e = c.getExtension('WEBGL_debug_renderer_info');
        gl = e ? c.getParameter(e.UNMASKED_RENDERER_WEBGL) : c.getParameter(c.RENDERER); } catch (e) {}
  const uad = navigator.userAgentData;
  out.main = { ua: navigator.userAgent, platform: navigator.platform, cores: navigator.hardwareConcurrency,
    mem: navigator.deviceMemory ?? null, langs: navigator.languages.join(','), gl,
    brands: uad ? uad.brands.map(b => b.brand + '/' + b.version).join(', ') : null };
  out.webdriver = navigator.webdriver;
  out.tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.lang = navigator.language;
  out.touch = navigator.maxTouchPoints;
  out.ontouch = 'ontouchstart' in window;
  out.screen = [screen.width, screen.height];
  out.inner = [innerWidth, innerHeight];
  out.mobile = uad ? uad.mobile : null;
  // воркер
  const code = `self.onmessage = () => { let gl = null;
    try { const c = new OffscreenCanvas(1,1).getContext('webgl'); const x = c.getExtension('WEBGL_debug_renderer_info');
          gl = x ? c.getParameter(x.UNMASKED_RENDERER_WEBGL) : c.getParameter(c.RENDERER); } catch (e) {}
    const u = navigator.userAgentData;
    postMessage({ ua: navigator.userAgent, platform: navigator.platform, cores: navigator.hardwareConcurrency,
      mem: navigator.deviceMemory ?? null, langs: navigator.languages.join(','), gl,
      brands: u ? u.brands.map(b => b.brand + '/' + b.version).join(', ') : null }); }`;
  try {
    const w = new Worker(URL.createObjectURL(new Blob([code], { type: 'text/javascript' })));
    out.worker = await new Promise(r => { w.onmessage = ev => r(ev.data); w.postMessage(1); setTimeout(() => r(null), 5000); });
  } catch (e) { out.worker = null; }
  // WebRTC: какие IP видны через STUN
  out.webrtc = await new Promise(res => {
    if (typeof RTCPeerConnection !== 'function') return res('disabled');
    try {
      const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
      const ips = new Set();
      pc.createDataChannel('x');
      pc.onicecandidate = e => {
        if (!e.candidate) { res([...ips]); try { pc.close(); } catch (x) {} return; }
        const parts = e.candidate.candidate.split(' ');
        if (parts[4]) ips.add(parts[4]);
      };
      pc.createOffer().then(o => pc.setLocalDescription(o));
      setTimeout(() => { res([...ips]); try { pc.close(); } catch (x) {} }, 5000);
    } catch (e) { res('error: ' + e.message); }
  });
  // шрифты: шрифт другой ОС должен выглядеть отсутствующим
  const span = (f) => { const s = document.createElement('span'); s.style.fontSize = '72px'; s.style.fontFamily = f;
    s.textContent = 'mmmmmmmmmmlli'; document.body.appendChild(s); const v = s.offsetWidth; s.remove(); return v; };
  const base = span('monospace');
  out.fonts = {};
  for (const f of ['Segoe UI', 'Helvetica Neue', 'Ubuntu', 'Roboto', 'Arial', 'Calibri', 'Menlo'])
    out.fonts[f] = span(`'${f}', monospace`) !== base;
  return out;
};
</script><script>__DETECT__</script></body></html>""".replace("__DETECT__", _DETECT)

_UA_OS = {"Win32": "Windows", "MacIntel": "Macintosh", "Linux x86_64": "Linux", "Linux armv81": "Android"}
_OS_FONTS = {"windows": "Segoe UI", "macos": "Helvetica Neue", "linux": "Ubuntu", "android": "Roboto"}


def _is_public(ip: str) -> bool:
    if ip.endswith(".local") or ":" in ip and ip.lower().startswith(("fe80", "fd", "fc")):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return ":" in ip  # IPv6 глобальный
    a, b = int(parts[0]), int(parts[1])
    return not (a in (10, 127) or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or (a == 169 and b == 254))


async def run_check(manager, pid: int) -> dict:
    p = db.get_profile(pid)
    if not p:
        raise KeyError(pid)
    was_running = manager.is_running(pid)
    sess = manager.sessions[pid] if was_running else await manager.start(pid, headless=True)
    ctx = sess.context
    items: list[tuple[str, str, str]] = []  # (ok|warn|fail, проверка, подробности)

    def add(ok, title, detail=""):
        items.append(("ok" if ok is True else ok if isinstance(ok, str) else "fail", title, detail))

    try:
        await ctx.route(URL + "**", lambda r: r.fulfill(body=PAGE, content_type="text/html; charset=utf-8"))
        page = await ctx.new_page()
        await page.goto(URL, timeout=30000)
        d = await page.evaluate("collect()")
        det = await page.evaluate("__caDetect()")
        await page.close()
        await ctx.unroute(URL + "**")

        ip = {}
        try:
            r = await ctx.request.get("http://ip-api.com/json/?fields=status,query,countryCode,timezone", timeout=15000)
            ip = await r.json()
        except Exception as e:  # noqa: BLE001
            add("warn", "Внешний IP", f"не удалось определить: {e}")
        if ip.get("status") == "success":
            add(True, "Внешний IP", f"{ip['query']} ({ip['countryCode']})")

        m, w = d["main"], d.get("worker")
        # 1. версия Chrome = движок
        if p.engine == "chromium":
            engine_major = manager.chrome_version.split(".")[0]
            ua_major = (m["ua"].split("Chrome/")[1].split(".")[0]) if "Chrome/" in m["ua"] else "?"
            add(ua_major == engine_major, "Версия Chrome совпадает с движком",
                f"UA: {ua_major}, движок: {engine_major}")
        # 2. воркер = страница
        if not w:
            add("warn", "Значения в Web Worker", "воркер не ответил")
        else:
            diff = [k for k in m if k in w and w[k] != m[k]]
            add(not diff, "Web Worker совпадает со страницей",
                "все значения совпадают" if not diff else "различаются: " + ", ".join(f"{k} ({w[k]} ≠ {m[k]})" for k in diff))
        # 3. webdriver
        add(d["webdriver"] is False, "navigator.webdriver скрыт", str(d["webdriver"]))
        # 4. платформа соответствует UA
        expect = _UA_OS.get(m["platform"])
        add(bool(expect) and expect in m["ua"], "Платформа соответствует User-Agent", f"{m['platform']} ↔ UA")
        # 5. таймзона и язык
        if ip.get("timezone"):
            add(d["tz"] == ip["timezone"], "Таймзона совпадает с IP", f"браузер: {d['tz']}, IP: {ip['timezone']}")
            want = locale_for(ip.get("countryCode", "")).split("-")[0]
            have = d["lang"].split("-")[0]
            add(True if have == want or want == "en" else "warn", "Язык соответствует стране IP",
                f"браузер: {d['lang']}, ожидается: {want}-*")
        # 6. WebRTC
        rtc = d["webrtc"]
        if rtc == "disabled":
            add(True, "WebRTC", "отключён в профиле")
        elif isinstance(rtc, list):
            public = [x for x in rtc if _is_public(x)]
            leak = [x for x in public if ip.get("query") and x != ip["query"]]
            add(not leak, "WebRTC не раскрывает реальный IP",
                ("утечка: " + ", ".join(leak)) if leak else (f"видно: {', '.join(public) or 'только локальные/скрытые адреса'}"))
        else:
            add("warn", "WebRTC", str(rtc))
        # 7. touch
        mobile = p.kind == "mobile"
        add((d["touch"] > 0 and d["ontouch"]) if mobile else d["touch"] == 0, "Сенсорный экран соответствует типу",
            f"maxTouchPoints={d['touch']}, ontouchstart={d['ontouch']}")
        # 8. экран
        if not mobile:
            add(d["screen"][0] >= d["inner"][0], "Окно меньше экрана", f"экран {d['screen']}, окно {d['inner']}")
        # 9. шрифты
        if p.engine == "chromium":
            os_name = "android" if mobile else p.os
            own = _OS_FONTS.get(os_name)
            foreign = [f for f, os_ in ((v, k) for k, v in _OS_FONTS.items()) if os_ != os_name and d["fonts"].get(f)]
            add(not foreign, "Нет шрифтов чужой ОС", ("видны: " + ", ".join(foreign)) if foreign else
                f"шрифт своей ОС «{own}»: {'есть' if d['fonts'].get(own) else 'нет на этом ПК (норма при эмуляции)'}")
        # 10. детекты JS-подмены и автоматизации (как в CreepJS)
        for key, title in DETECT_TITLES.items():
            if key in det and (p.engine == "chromium" or key in ("cdp_runtime", "no_artifacts", "iframe_consistent")):
                add(det[key]["ok"], title, det[key]["detail"])
    finally:
        if not was_running:
            await manager.stop(pid)

    result = {"time": time.strftime("%Y-%m-%d %H:%M"), "items": items,
              "ok": sum(1 for s, *_ in items if s == "ok"), "total": len(items),
              "fail": sum(1 for s, *_ in items if s == "fail")}
    p = db.get_profile(pid)
    if p:
        p.last_check = result
        db.save_profile(p)
    return result


async def open_check_sites(manager, pid: int):
    sess = manager.sessions.get(pid) or await manager.start(pid)
    for url in SITES:
        page = await sess.context.new_page()
        try:
            await page.goto(url, wait_until="commit", timeout=30000)
        except Exception:  # noqa: BLE001
            pass


def format_report(r: dict) -> str:
    mark = {"ok": "✓", "warn": "!", "fail": "✗"}
    lines = [f"Проверка {r['time']}: {r['ok']} из {r['total']} в порядке" + (f", проблем: {r['fail']}" if r["fail"] else "")]
    for s, title, detail in r["items"]:
        lines.append(f"  {mark[s]} {title}" + (f" — {detail}" if detail else ""))
    return "\n".join(lines)
