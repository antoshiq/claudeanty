"""Стабильность отпечатка между запусками.

Профиль должен выглядеть одинаково при каждом запуске: если canvas, WebGL или audio «плывут», сайт видит
то новый, то старый браузер на том же аккаунте. Причины: обновление Chrome или драйвера видеокарты,
смена видеокарты/монитора, ошибка в подмене. Встроенная проверка снимает хэши ключевых частей
отпечатка, хранит историю (отдельно для запуска с окном и без — рендер может отличаться) и сравнивает
с прошлым разом.
"""
import time

HISTORY = 12

# что сравниваем и как это объяснить
PARTS = {
    "canvas": "Canvas 2D (текст, эмодзи, градиенты)",
    "webgl_image": "Картинка WebGL",
    "webgl_params": "Параметры WebGL (видеокарта, лимиты, расширения)",
    "audio": "Audio (OfflineAudioContext)",
    "fonts": "Набор и размеры шрифтов",
    "navigator": "Navigator и экран (без версии Chrome)",
    "math": "Math / движок JS",
}

JS = r"""async () => {
  const sha = async (s) => { const b = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
    return [...new Uint8Array(b)].slice(0, 8).map(x => x.toString(16).padStart(2, '0')).join(''); };
  const out = {};
  try {
    const c = document.createElement('canvas'); c.width = 280; c.height = 60; const x = c.getContext('2d');
    const g = x.createLinearGradient(0, 0, 280, 0); g.addColorStop(0, '#f60'); g.addColorStop(1, '#06c');
    x.fillStyle = g; x.fillRect(0, 0, 280, 60); x.textBaseline = 'top';
    x.font = '16px Arial'; x.fillStyle = '#069'; x.fillText('Cwm fjordbank glyphs vext quiz 😃', 4, 6);
    x.font = '14px "Times New Roman"'; x.fillStyle = 'rgba(102,204,0,.7)'; x.fillText('Съешь же ещё этих булок ∑√', 6, 30);
    x.beginPath(); x.arc(240, 30, 18, 0, Math.PI * 2); x.strokeStyle = '#333'; x.stroke();
    out.canvas = await sha(c.toDataURL());
  } catch (e) { out.canvas = 'error'; }
  try {
    const c = document.createElement('canvas'); c.width = 64; c.height = 64;
    const gl = c.getContext('webgl', { preserveDrawingBuffer: true });
    const sh = (t, src) => { const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, 'attribute vec2 p;varying vec2 v;void main(){v=p;gl_Position=vec4(p,0,1);}'));
    gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, 'precision mediump float;varying vec2 v;void main(){gl_FragColor=vec4(sin(v.x*7.0)*.5+.5,v.y*.5+.5,fract(v.x*v.y*13.0),1);}'));
    gl.linkProgram(pr); gl.useProgram(pr);
    const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, 0, 1, -.8, .9, .9, .7, -.2, -.6]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(pr, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    const px = new Uint8Array(64 * 64 * 4); gl.readPixels(0, 0, 64, 64, gl.RGBA, gl.UNSIGNED_BYTE, px);
    out.webgl_image = await sha(Array.from(px).join(','));
    const e = gl.getExtension('WEBGL_debug_renderer_info');
    const params = [e ? gl.getParameter(e.UNMASKED_VENDOR_WEBGL) : '', e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : ''];
    for (const k of [3379, 34024, 3386, 36347, 36349, 36348, 35661, 34930, 35660, 34921, 34076, 33902, 33901]) {
      const v = gl.getParameter(k); params.push(v && v.length !== undefined ? Array.from(v).join('x') : String(v)); }
    params.push((gl.getSupportedExtensions() || []).sort().join(','));
    out.webgl_params = await sha(params.join('|'));
  } catch (e) { out.webgl_image = out.webgl_params = 'error'; }
  try {
    const ctx = new OfflineAudioContext(1, 5000, 44100);
    const o = ctx.createOscillator(); o.type = 'triangle'; o.frequency.value = 10000;
    const cmp = ctx.createDynamicsCompressor();
    for (const [k, v] of [['threshold', -50], ['knee', 40], ['ratio', 12], ['attack', 0], ['release', .25]]) cmp[k].value = v;
    o.connect(cmp); cmp.connect(ctx.destination); o.start(0);
    const buf = await ctx.startRendering(); const d = buf.getChannelData(0);
    let s = 0; for (let i = 4500; i < 5000; i++) s += Math.abs(d[i]);
    out.audio = await sha(s.toString());
  } catch (e) { out.audio = 'error'; }
  const fonts = ['Arial', 'Segoe UI', 'Calibri', 'Cambria', 'Consolas', 'Verdana', 'Tahoma', 'Georgia', 'Helvetica Neue',
    'Menlo', 'Monaco', 'Geneva', 'Ubuntu', 'DejaVu Sans', 'Liberation Sans', 'Noto Sans', 'Roboto', 'Cantarell',
    'Times New Roman', 'Courier New', 'Comic Sans MS', 'Impact', 'Lucida Console', 'Segoe UI Emoji', 'Apple Color Emoji'];
  const sp = document.createElement('span'); sp.textContent = 'mmmmmmmmmmlliWW@#ёж'; sp.style.fontSize = '64px';
  document.body.appendChild(sp);
  const w = fonts.map(f => { sp.style.fontFamily = `"${f}", monospace`; return f + ':' + sp.offsetWidth + 'x' + sp.offsetHeight; });
  sp.remove();
  out.fonts = await sha(w.join('|'));
  out.navigator = await sha([navigator.platform, navigator.hardwareConcurrency, navigator.deviceMemory, navigator.maxTouchPoints,
    navigator.languages.join(','), screen.width, screen.height, screen.availWidth, screen.availHeight, screen.colorDepth,
    devicePixelRatio, Intl.DateTimeFormat().resolvedOptions().timeZone].join('|'));
  out.math = await sha([Math.tan(-1e300), Math.sinh(1), Math.expm1(1), Math.cbrt(Math.PI), Math.atanh(0.5), Math.log1p(10)].join('|'));
  return out;
}"""


def compare(history: list[dict], current: dict, mode: str, chrome: str) -> tuple[str, str]:
    """-> (ok|warn, подробности) относительно прошлой записи того же режима."""
    prev = next((h for h in reversed(history) if h.get("mode") == mode), None)
    if not prev:
        return "ok", "первая запись — сравнение со следующей проверкой"
    changed = [k for k in PARTS if k in current and current[k] != prev["hashes"].get(k)]
    if not changed:
        return "ok", f"совпадает с проверкой {prev['time']} (" + ", ".join(PARTS) + ")"
    names = ", ".join(PARTS[k].split(" (")[0] for k in changed)
    old_major, new_major = str(prev.get("chrome", "")).split(".")[0], chrome.split(".")[0]
    if old_major != new_major:
        why = (f"Chrome обновился {prev.get('chrome')} → {chrome}: после обновления браузера рендер иногда меняется, "
               "у обычных пользователей так же")
    elif set(changed) <= {"canvas", "webgl_image", "webgl_params"}:
        why = "Chrome тот же — вероятно, обновился драйвер видеокарты или сменилась видеокарта/монитор"
    elif "navigator" in changed:
        why = "изменились настройки профиля (экран, язык, таймзона, ядра) — если вы их не меняли, это ошибка"
    else:
        why = "Chrome тот же — проверьте, не менялись ли настройки профиля"
    return "warn", f"с {prev['time']} изменилось: {names}. {why}"


def record(history: list[dict], current: dict, mode: str, chrome: str) -> list[dict]:
    history = list(history or [])
    history.append({"time": time.strftime("%Y-%m-%d %H:%M"), "mode": mode, "chrome": chrome, "hashes": current})
    return history[-HISTORY:]
