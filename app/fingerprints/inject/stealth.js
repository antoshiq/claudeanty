(() => {
  // Подмена отпечатка в странице. Всё, что Chromium умеет нативно (UA, Client Hints, платформа, языки,
  // ядра CPU, экран, touch, таймзона, гео, тип сети, датчики), задаётся через CDP и здесь НЕ трогается —
  // так не остаётся JS-следов. Здесь только то, чего CDP не умеет, и каждая подмена выглядит нативной:
  // без prototype, с правильными name/length, не вызывается через new, без своих кадров в стеке ошибок.
  const FP = __FP__;
  const TOKEN = FP.token;  // уникальная метка нашего скрипта (sourceURL) — по ней вырезаем кадры из стека

  // ---------- инструменты «нативности» ----------
  const nativeToString = Function.prototype.toString;
  const masked = new WeakMap();
  const sig = (name) => `function ${name}() { [native code] }`;
  // Ошибка из нативной функции, вызванной нашей обёрткой, несёт в стеке кадры обёртки — их видно через
  // Error.prepareStackTrace (структурные кадры). Поэтому стек пересобирается с точки вызова обёртки
  // (Error.captureStackTrace(e, обёртка) отбрасывает её и всё внутри). Сам e.stack НЕ читаем: чтение
  // вызвало бы prepareStackTrace страницы с нашими кадрами. Ошибки, брошенные кодом страницы
  // (например, из valueOf аргумента), не трогаем — их стек должен остаться как есть.
  const captureStack = Error.captureStackTrace;
  const ownError = (e) => { try { return e instanceof TypeError && /Illegal invocation|requires that 'this'/.test(e.message); }
                            catch (x) { return false; } };
  const clean = (e, fn, always) => {
    if ((always || ownError(e)) && e !== null && typeof e === "object") {
      try { captureStack(e, fn); } catch (x) {}
    }
    return e;
  };
  // Каждый JS-контекст (страница, каждый iframe) получает свою копию скрипта со своим списком masked.
  // Сайты проверяют функции через toString ДРУГОГО контекста (iframe.contentWindow.Function.prototype.toString),
  // поэтому чужую функцию передаём toString её родного контекста — он знает свои подмены.
  const fnToString = { toString() {
    if (masked.has(this)) return masked.get(this);
    if (typeof this === "function") {
      try {
        const P = Object.getPrototypeOf(this);
        if (P && P !== Function.prototype && Object.getPrototypeOf(P) && typeof P.constructor === "function" &&
            P.constructor.prototype === P) {
          const d = Object.getOwnPropertyDescriptor(P, "toString");
          if (d && typeof d.value === "function" && d.value !== fnToString) return d.value.call(this);
        }
      } catch (e) {}
    }
    try { return nativeToString.call(this); } catch (e) { throw clean(e, fnToString); }
  } }.toString;
  masked.set(fnToString, sig("toString"));
  Object.defineProperty(Function.prototype, "toString", { value: fnToString, writable: true, enumerable: false, configurable: true });

  // метод как у браузера: shorthand-метод (нет prototype, TypeError при new), нужные name и length
  const native = (name, length, impl) => {
    const f = { [name](...args) { try { return impl.call(this, args); } catch (e) { throw clean(e, f); } } }[name];
    Object.defineProperty(f, "length", { value: length });
    masked.set(f, sig(name));
    return f;
  };
  const getter = (prop, impl) => {
    const g = Object.getOwnPropertyDescriptor({ get [prop]() { try { return impl.call(this); } catch (e) { throw clean(e, g, true); } } }, prop).get;
    masked.set(g, sig("get " + prop));
    return g;
  };
  // Подменённый геттер сначала вызывает настоящий: та же проверка this («Illegal invocation» при
  // Navigator.prototype.deviceMemory), те же ошибки — отличается только возвращаемое значение.
  const defGet = (obj, prop, impl) => {
    const d = Object.getOwnPropertyDescriptor(obj, prop);
    if (!d || !d.get) return null;
    Object.defineProperty(obj, prop, { get: getter(prop, function () { const real = d.get.call(this); return impl.call(this, d.get, real); }),
      set: d.set, enumerable: d.enumerable, configurable: true });
    return d.get;
  };
  const defMethod = (obj, prop, impl) => {
    const d = Object.getOwnPropertyDescriptor(obj, prop);
    if (!d || typeof d.value !== "function") return null;
    const orig = d.value;
    Object.defineProperty(obj, prop, { value: native(prop, orig.length, function (args) { return impl.call(this, orig, args); }),
      writable: d.writable, enumerable: d.enumerable, configurable: true });
    return orig;
  };

  // детерминированный шум профиля (одинаковый при повторных вызовах)
  const seed = FP.seed >>> 0;
  const hash = (a, b) => { let h = (seed ^ Math.imul(a + 0x9e3779b9, 0x85ebca6b) ^ Math.imul(b + 0x7f4a7c15, 0xc2b2ae35)) >>> 0;
    h = Math.imul(h ^ (h >>> 16), 0x45d9f3b) >>> 0; return (h ^ (h >>> 16)) >>> 0; };

  // ---------- память (у CDP нет переопределения deviceMemory) ----------
  defGet(Navigator.prototype, "deviceMemory", () => FP.memory);

  // ---------- доступная область экрана: панель задач / строка меню ----------
  // Размер экрана задаётся нативно (CDP), но тогда availHeight = height — признак headless («нет панели задач»).
  if (!FP.mobile && FP.screen.avail_height) {
    defGet(Screen.prototype, "availHeight", () => FP.screen.avail_height);
    defGet(Screen.prototype, "availTop", () => FP.screen.avail_top || 0);
  }

  // ---------- WebGL: видеокарта и лимиты, согласованные с ней ----------
  const GLP = FP.webgl.params || {};
  const glValue = (v) => (v && v.i) ? new Int32Array(v.i) : (v && v.f) ? new Float32Array(v.f) : v;
  for (const C of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
    if (!C) continue;
    defMethod(C.prototype, "getParameter", function (orig, args) {
      const p = args[0];
      // UNMASKED_VENDOR/RENDERER_WEBGL: без включённого расширения WEBGL_debug_renderer_info настоящий Chrome
      // отдаёт null (и ставит INVALID_ENUM) — подменяем только то, что браузер действительно вернул бы
      if (p === 37445) { const r = orig.apply(this, args); return r === null ? r : FP.webgl.vendor; }
      if (p === 37446) { const r = orig.apply(this, args); return r === null ? r : FP.webgl.renderer; }
      const real = orig.apply(this, args);  // сначала нативный вызов: та же проверка this и те же ошибки
      return GLP[p] !== undefined ? glValue(GLP[p]) : real;
    });
  }

  // ---------- canvas: стабильный шум без изменения исходного холста ----------
  // Шум только на «краях» (пиксель отличается от соседа): однотонные заливки остаются однотонными,
  // повторный вызов даёт тот же результат, исходный холст не меняется.
  const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
  const noiseData = (data, w) => {
    for (let i = 0; i < data.length; i += 4) {
      const r = i + 4 < data.length && (i / 4 + 1) % w !== 0 ? i + 4 : i - 4;
      if (r < 0) continue;
      if (data[i] === data[r] && data[i + 1] === data[r + 1] && data[i + 2] === data[r + 2] && data[i + 3] === data[r + 3]) continue;
      const h = hash(i, data[i] | (data[i + 1] << 8) | (data[i + 2] << 16));
      if ((h & 7) !== 0) continue;  // ~1/8 краевых пикселей
      const ch = i + (h >>> 3) % 3;
      data[ch] = data[ch] + ((h & 8) ? 1 : -1) & 255;
    }
  };
  const noisyCopy = (canvas) => {
    const w = canvas.width, h = canvas.height;
    if (!w || !h || w * h > 4e6) return null;
    const copy = document.createElement("canvas");
    copy.width = w; copy.height = h;
    const cx = copy.getContext("2d");
    cx.drawImage(canvas, 0, 0);
    const img = origGetImageData.call(cx, 0, 0, w, h);
    noiseData(img.data, w);
    cx.putImageData(img, 0, 0);
    return copy;
  };
  defMethod(HTMLCanvasElement.prototype, "toDataURL", function (orig, args) {
    const copy = noisyCopy(this);
    return orig.apply(copy || this, args);
  });
  defMethod(HTMLCanvasElement.prototype, "toBlob", function (orig, args) {
    const copy = noisyCopy(this);
    return orig.apply(copy || this, args);
  });
  defMethod(CanvasRenderingContext2D.prototype, "getImageData", function (orig, args) {
    const img = orig.apply(this, args);
    noiseData(img.data, img.width);
    return img;
  });

  // ---------- audio: шум один раз на буфер (повторное чтение даёт то же самое) ----------
  if (window.AudioBuffer) {
    const done = new WeakSet();
    const origGCD = AudioBuffer.prototype.getChannelData;
    const ensure = (buf) => {
      if (done.has(buf)) return;
      done.add(buf);
      for (let c = 0; c < buf.numberOfChannels; c++) {
        const d = origGCD.call(buf, c);
        for (let i = 0; i < d.length; i += 97) d[i] += ((hash(c, i) & 0xff) - 128) * 1e-9;
      }
    };
    defMethod(AudioBuffer.prototype, "getChannelData", function (orig, args) { const r = orig.apply(this, args); ensure(this); return r; });
    defMethod(AudioBuffer.prototype, "copyFromChannel", function (orig, args) { origGCD.call(this, 0); ensure(this); return orig.apply(this, args); });
  }

  // ---------- шрифты: скрываем чужие для ОС профиля, подменяем отсутствующие на ПК ----------
  // hidden — шрифт не из списка ОС профиля (например, Segoe UI у macOS-профиля): «его нет».
  // alias — шрифт ОС профиля, которого нет на этом ПК (Helvetica Neue): «он есть», замеры и рисование —
  // похожим установленным шрифтом (Arial). document.fonts.check() НЕ трогаем: настоящий Chrome для
  // системных шрифтов всегда отвечает true, и любая другая логика была бы отличием от него.
  if (FP.fonts && FP.fonts.length) {
    const ALLOWED = new Set(FP.fonts.map(f => f.toLowerCase()));
    const KNOWN = new Set((FP.font_known || []).map(f => f.toLowerCase()));
    const ALIAS = new Map(Object.entries(FP.font_alias || {}).map(([k, v]) => [k.toLowerCase(), v]));
    const GENERIC = new Set(["serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif",
      "ui-sans-serif", "ui-monospace", "ui-rounded", "emoji", "math", "fangsong", "inherit", "initial", "unset", "-apple-system"]);
    const unq = (s) => String(s || "").trim().replace(/^["']|["']$/g, "");
    const split = (ff) => ff.split(",").map(unq).filter(Boolean);
    const hidden = (n) => { n = n.toLowerCase(); return !GENERIC.has(n) && !ALLOWED.has(n); };
    const aliasOf = (n) => ALIAS.get(String(n).toLowerCase());
    const quote = (f) => GENERIC.has(f.toLowerCase()) ? f : `"${f}"`;
    // список шрифтов, каким его «видит» профиль: скрытые убраны, отсутствующие заменены псевдонимами
    // Скрытый шрифт заменяется НЕсуществующим именем, а не выбрасывается: тогда Chrome делает ровно тот откат,
    // что и для отсутствующего шрифта (следующий в списке или шрифт по умолчанию). Подстановка «monospace»
    // давала другой рендер, чем у настоящего браузера (pixelscan рисует «no-real-font-123» и сравнивает).
    const NOFONT = `"${TOKEN}-nf"`;
    const fix = (ff) => split(ff).map(f => hidden(f) ? NOFONT : quote(aliasOf(f) || f)).join(", ");
    const needsFix = (ff) => { const first = split(ff)[0]; return !!first && (hidden(first) || !!aliasOf(first)); };
    const viaFallback = (el, read) => {
      const st = el && el.style, ff = st && st.fontFamily;
      if (ff && needsFix(ff)) {
        const saved = st.fontFamily; st.fontFamily = fix(ff);
        try { return read(); } finally { st.fontFamily = saved; }
      }
      return read();
    };
    for (const [proto, props] of [[HTMLElement.prototype, ["offsetWidth", "offsetHeight"]],
                                  [Element.prototype, ["scrollWidth", "scrollHeight", "clientWidth", "clientHeight"]]])
      for (const prop of props) defGet(proto, prop, function (orig) { return viaFallback(this, () => orig.call(this)); });
    for (const m of ["getBoundingClientRect", "getClientRects"])
      defMethod(Element.prototype, m, function (orig, args) { return viaFallback(this, () => orig.apply(this, args)); });
    // Range: шрифт берём у ближайшего элемента со своим font-family
    const rangeEl = (r) => { let n = r.commonAncestorContainer; if (n && n.nodeType !== 1) n = n.parentElement;
      while (n && n.nodeType === 1 && !(n.style && n.style.fontFamily)) n = n.parentElement; return n; };
    for (const m of ["getBoundingClientRect", "getClientRects"])
      if (window.Range) defMethod(Range.prototype, m, function (orig, args) { return viaFallback(rangeEl(this), () => orig.apply(this, args)); });

    const FONT_RE = /^(.*?\d+(?:\.\d+)?(?:px|pt|em|rem|%|vh|vw)(?:\s*\/\s*\S+)?\s+)(.+)$/;
    const withFont = (orig) => function (o, args) {
      const m = FONT_RE.exec(this.font || "");
      if (m && needsFix(m[2])) {
        const saved = this.font; this.font = m[1] + fix(m[2]);
        try { return o.apply(this, args); } finally { this.font = saved; }
      }
      return o.apply(this, args);
    };
    for (const C of [window.CanvasRenderingContext2D, window.OffscreenCanvasRenderingContext2D])
      // только measureText: перехват fillText/strokeText распознают (pixelscan зависает на «Collecting Data»),
      // поэтому рисование текста идёт как есть — скрытый шрифт ПК теоретически виден по пикселям canvas
      if (C) for (const m of ["measureText"]) defMethod(C.prototype, m, withFont());

    // FontFace(name, local(name)).load(): чужой системный шрифт — «нет», шрифт ОС профиля — «есть»
    if (window.FontFace) {
      const fake = new WeakMap();  // face -> { status, promise }
      defMethod(FontFace.prototype, "load", function (orig, args) {
        const known = fake.get(this);
        if (known) return known.promise;
        const fam = unq(this.family);
        if (hidden(fam) && KNOWN.has(fam.toLowerCase())) {
          const entry = { status: "error", promise: Promise.reject(new DOMException("A network error occurred.", "NetworkError")) };
          fake.set(this, entry);
          return entry.promise;
        }
        const alias = aliasOf(fam);
        if (!alias) return orig.apply(this, args);
        const self = this, entry = { status: "loading" };
        fake.set(this, entry);
        entry.promise = orig.apply(this, args).then(r => { fake.delete(self); return r; }, () => {
          const sub = new FontFace(fam, `local("${alias}")`);
          return orig.call(sub).then(() => { entry.status = "loaded"; return self; },
                                     e => { entry.status = "error"; throw e; });
        });
        return entry.promise;
      });
      defGet(FontFace.prototype, "status", function (g, real) { const f = fake.get(this); return f ? f.status : real; });
      defGet(FontFace.prototype, "loaded", function (g, real) { const f = fake.get(this); return f ? f.promise : real; });
    }
    if (window.queryLocalFonts) defMethod(window, "queryLocalFonts", function () {
      return Promise.reject(new DOMException("Permission denied.", "NotAllowedError"));
    });
  }

  // ---------- медиаустройства: по одному каждого типа с пустыми id, как у Chrome до разрешения ----------
  if (window.MediaDevices && FP.media) {
    defMethod(MediaDevices.prototype, "enumerateDevices", function (orig, args) {
      return orig.apply(this, args).then(real => {
        const byKind = {};
        for (const d of real) if (!byKind[d.kind]) byKind[d.kind] = d;
        // берём настоящие объекты (правильные классы), только набор типов — из отпечатка
        return ["audioinput", "videoinput", "audiooutput"].filter(k => FP.media[k] && byKind[k]).map(k => byKind[k]);
      });
    });
  }

  // ---------- WebGPU: та же видеокарта, что в WebGL ----------
  if (window.GPUAdapterInfo) {
    const vendor = (FP.webgl.vendor.match(/\(([^)]+)\)/) || [null, FP.webgl.vendor])[1].split(" ")[0].toLowerCase();
    defGet(GPUAdapterInfo.prototype, "vendor", () => vendor);
    for (const p of ["architecture", "device", "description"]) defGet(GPUAdapterInfo.prototype, p, () => "");
  }

  // ---------- телефон: батарея и размеры окна ----------
  if (FP.mobile) {
    if (window.BatteryManager) {
      const level = Math.round((0.35 + (hash(1, 2) % 60) / 100) * 100) / 100;
      const charging = (hash(3, 4) & 1) === 1;
      defGet(BatteryManager.prototype, "level", () => level);
      defGet(BatteryManager.prototype, "charging", () => charging);
      defGet(BatteryManager.prototype, "chargingTime", () => charging ? 3600 : Infinity);
      defGet(BatteryManager.prototype, "dischargingTime", () => charging ? Infinity : 18000);
    }
    // у телефона окно = экран; outer* у window — собственные свойства (как у браузера)
    defGet(window, "outerWidth", () => screen.width);
    defGet(window, "outerHeight", () => screen.height);
    defGet(window, "screenX", () => 0);
    defGet(window, "screenY", () => 0);
  }

  // ---------- WebRTC выключен ----------
  if (FP.webrtc === "off") {
    for (const n of ["RTCPeerConnection", "webkitRTCPeerConnection", "RTCDataChannel", "RTCSessionDescription", "RTCIceCandidate"])
      try { delete window[n]; } catch (e) {}
  }
})();
