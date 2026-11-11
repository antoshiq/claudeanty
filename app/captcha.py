"""Решение капч через внешние сервисы: 2captcha или CapSolver (ключ — в Настройках → Сценарии).

Поддерживаются reCAPTCHA v2 (в т.ч. невидимая), hCaptcha и Cloudflare Turnstile: на странице ищется
ключ сайта (sitekey), задача отправляется в сервис, ответ (токен) подставляется в скрытое поле формы,
а колбэк сайта вызывается, как это сделала бы сама капча. У обоих сервисов одинаковый протокол
createTask / getTaskResult; задачи без прокси (решаются с IP сервиса — сайты это обычно принимают).
"""
import asyncio
import time

import httpx

from . import settings
from .engines.base import evaluate_main

PROVIDERS = {
    "2captcha": ("https://api.2captcha.com", {"recaptcha": "RecaptchaV2TaskProxyless", "hcaptcha": "HCaptchaTaskProxyless",
                                             "turnstile": "TurnstileTaskProxyless"}),
    "capsolver": ("https://api.capsolver.com", {"recaptcha": "ReCaptchaV2TaskProxyLess", "hcaptcha": "HCaptchaTaskProxyLess",
                                               "turnstile": "AntiTurnstileTaskProxyLess"}),
}

DETECT_JS = r"""() => {
  const fromUrl = (src, re) => { const m = re.exec(src || ''); return m ? decodeURIComponent(m[1]) : null; };
  const res = [];
  for (const el of document.querySelectorAll('[data-sitekey]')) {
    const cls = (el.className || '') + ' ' + (el.tagName || '');
    const type = /h-captcha/.test(cls) ? 'hcaptcha' : /cf-turnstile/.test(cls) ? 'turnstile' : 'recaptcha';
    res.push({ type, sitekey: el.getAttribute('data-sitekey'), invisible: el.getAttribute('data-size') === 'invisible',
               callback: el.getAttribute('data-callback') || '' });
  }
  for (const f of document.querySelectorAll('iframe')) {
    const s = f.src || '';
    let k;
    if ((k = fromUrl(s, /google\.com\/recaptcha\/(?:api2|enterprise)\/anchor\?.*?[?&]k=([^&]+)/)) ||
        (k = fromUrl(s, /recaptcha\.net\/recaptcha\/(?:api2|enterprise)\/anchor\?.*?[?&]k=([^&]+)/)))
      res.push({ type: 'recaptcha', sitekey: k, invisible: /size=invisible/.test(s), callback: '' });
    else if ((k = fromUrl(s, /hcaptcha\.com\/.*?[#&?]sitekey=([^&]+)/)))
      res.push({ type: 'hcaptcha', sitekey: k, invisible: false, callback: '' });
    else if ((k = fromUrl(s, /challenges\.cloudflare\.com\/.*?\/(0x[0-9A-Za-z_-]{10,})\//)))
      res.push({ type: 'turnstile', sitekey: k, invisible: false, callback: '' });
  }
  return res[0] || null;
}"""

INJECT_JS = r"""([type, token, callback]) => {
  const set = (sel) => document.querySelectorAll(sel).forEach(el => { el.value = token; el.innerHTML = token;
    el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); });
  if (type === 'turnstile') set('input[name="cf-turnstile-response"]');
  else { set('textarea[name="g-recaptcha-response"], #g-recaptcha-response'); if (type === 'hcaptcha') set('textarea[name="h-captcha-response"]'); }
  const call = (name) => { try { const fn = name.split('.').reduce((o, k) => o && o[k], window); if (typeof fn === 'function') { fn(token); return true; } } catch (e) {} return false; };
  if (callback && call(callback)) return 'callback';
  // reCAPTCHA без data-callback: колбэк лежит в ___grecaptcha_cfg.clients
  try {
    const seen = new Set();
    const walk = (o, d) => { if (!o || d > 5 || seen.has(o)) return null; seen.add(o);
      for (const k of Object.keys(o)) { const v = o[k];
        if (k === 'callback') { if (typeof v === 'function') return v; if (typeof v === 'string' && call(v)) return true; }
        if (v && typeof v === 'object') { const r = walk(v, d + 1); if (r) return r; } } return null; };
    const cfg = window.___grecaptcha_cfg;
    if (cfg && cfg.clients) for (const c of Object.values(cfg.clients)) { const cb = walk(c, 0); if (typeof cb === 'function') { cb(token); return 'grecaptcha'; } if (cb) return 'grecaptcha'; }
  } catch (e) {}
  return 'field';
}"""


async def solve(captcha: dict, url: str, timeout: float = 180) -> str:
    provider = settings.get("captcha_provider")
    key = settings.get("captcha_key")
    if not key:
        raise ValueError("не задан ключ сервиса капч (Настройки → Сценарии)")
    base, types = PROVIDERS[provider]
    task = {"type": types[captcha["type"]], "websiteURL": url, "websiteKey": captcha["sitekey"]}
    if captcha["type"] == "recaptcha" and captcha.get("invisible"):
        task["isInvisible"] = True
    async with httpx.AsyncClient(timeout=30) as c:
        r = (await c.post(f"{base}/createTask", json={"clientKey": key, "task": task})).json()
        if r.get("errorId"):
            raise RuntimeError(f"{provider}: {r.get('errorCode')} {r.get('errorDescription', '')}".strip())
        tid = r["taskId"]
        end = time.time() + timeout
        while time.time() < end:
            await asyncio.sleep(5)
            r = (await c.post(f"{base}/getTaskResult", json={"clientKey": key, "taskId": tid})).json()
            if r.get("errorId"):
                raise RuntimeError(f"{provider}: {r.get('errorCode')} {r.get('errorDescription', '')}".strip())
            if r.get("status") == "ready":
                sol = r.get("solution") or {}
                return sol.get("gRecaptchaResponse") or sol.get("token") or ""
    raise TimeoutError(f"{provider}: капча не решена за {int(timeout)} с")


async def solve_on_page(page, log=lambda s: None, solver=None) -> str:
    """Найти капчу на странице, решить и подставить ответ. Возвращает, что было сделано."""
    found = None
    for frame in [page.main_frame, *[f for f in page.frames if f != page.main_frame]]:
        try:
            found = await frame.evaluate(DETECT_JS)
        except Exception:  # noqa: BLE001
            continue
        if found:
            target = frame
            break
    if not found:
        return "капчи на странице нет"
    log(f"Капча {found['type']} ({found['sitekey'][:12]}…) — отправлена в {settings.get('captcha_provider')}")
    token = await (solver or solve)(found, page.url)
    if not token:
        raise RuntimeError("сервис вернул пустой ответ")
    how = await evaluate_main(target, INJECT_JS, [found["type"], token, found.get("callback", "")])
    return f"капча {found['type']} решена ({how})"


async def balance() -> float:
    provider, key = settings.get("captcha_provider"), settings.get("captcha_key")
    base, _ = PROVIDERS[provider]
    async with httpx.AsyncClient(timeout=20) as c:
        r = (await c.post(f"{base}/getBalance", json={"clientKey": key})).json()
    if r.get("errorId"):
        raise RuntimeError(f"{r.get('errorCode')} {r.get('errorDescription', '')}".strip())
    return float(r.get("balance", 0))
