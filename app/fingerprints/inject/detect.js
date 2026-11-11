// Набор детектов JS-подмены и автоматизации (как в CreepJS / fingerprint-тестах).
// Возвращает {имя: {ok, detail}}. Используется во встроенной проверке профиля.
window.__caDetect = async () => {
  const R = {};
  const put = (k, ok, detail = "") => { R[k] = { ok, detail: String(detail) }; };
  const nativeRe = /^function [\w$ ]*\(\) \{\s*\[native code\]\s*\}$/;
  const isNative = (f) => { try { return nativeRe.test(Function.prototype.toString.call(f)); } catch (e) { return false; } };

  // 1. CDP Runtime.enable: при включённом Runtime консоль сериализует объект и трогает геттер stack
  let cdp = false;
  try {
    const e = new Error();
    Object.defineProperty(e, "stack", { configurable: false, get() { cdp = true; return ""; } });
    console.debug(e);
  } catch (x) {}
  await new Promise(r => setTimeout(r, 50));
  put("cdp_runtime", !cdp, cdp ? "Runtime.enable обнаружен (автоматизация)" : "не обнаружен");

  // 2. подменённые функции должны выглядеть как нативные
  const fns = {
    "WebGL.getParameter": [WebGLRenderingContext.prototype.getParameter, 1],
    "Canvas.toDataURL": [HTMLCanvasElement.prototype.toDataURL, 0],
    "Canvas.toBlob": [HTMLCanvasElement.prototype.toBlob, 1],
    "Canvas2D.getImageData": [CanvasRenderingContext2D.prototype.getImageData, 4],
    "Canvas2D.measureText": [CanvasRenderingContext2D.prototype.measureText, 1],
    "AudioBuffer.getChannelData": [AudioBuffer.prototype.getChannelData, 1],
    "MediaDevices.enumerateDevices": [MediaDevices.prototype.enumerateDevices, 0],
    "Function.toString": [Function.prototype.toString, 0],
    "Permissions.query": [Permissions.prototype.query, 1],
    "FontFaceSet.check": [FontFaceSet.prototype.check, 1],
  };
  const bad = [];
  for (const [name, [f, len]] of Object.entries(fns)) {
    if (typeof f !== "function") continue;
    const own = Object.getOwnPropertyNames(f).sort().join(",");
    if (!isNative(f)) bad.push(name + ": toString");
    if ("prototype" in f) bad.push(name + ": есть prototype");
    if (f.length !== len) bad.push(`${name}: length ${f.length}≠${len}`);
    if (own !== "length,name") bad.push(`${name}: свойства ${own}`);
    try { new f(); bad.push(name + ": вызывается через new"); } catch (e) { if (!(e instanceof TypeError)) bad.push(name + ": не TypeError при new"); }
  }
  // toString из ДРУГОГО контекста (так проверяет CreepJS)
  const fr = document.createElement("iframe"); fr.style.display = "none"; document.body.appendChild(fr);
  const foreignToString = fr.contentWindow.Function.prototype.toString;
  for (const [name, [f]] of Object.entries(fns)) {
    if (typeof f !== "function") continue;
    let s = ""; try { s = foreignToString.call(f); } catch (e) { s = "throws"; }
    if (!nativeRe.test(s)) bad.push(name + ": toString из iframe показывает код");
  }
  fr.remove();
  for (const [proto, prop] of [[Navigator.prototype, "hardwareConcurrency"], [Navigator.prototype, "deviceMemory"],
                               [Navigator.prototype, "platform"], [Screen.prototype, "width"],
                               [Screen.prototype, "availHeight"], [Navigator.prototype, "webdriver"]]) {
    const d = Object.getOwnPropertyDescriptor(proto, prop);
    if (!d || !d.get) { bad.push(prop + ": нет геттера на прототипе"); continue; }
    if (!isNative(d.get)) bad.push(prop + ": геттер не нативный");
    if ("prototype" in d.get) bad.push(prop + ": у геттера есть prototype");
    if (d.get.name !== "get " + prop) bad.push(prop + ": имя геттера " + d.get.name);
    try { proto[prop]; bad.push(prop + ": нет «Illegal invocation» на прототипе"); }
    catch (e) { if (!(e instanceof TypeError)) bad.push(prop + ": ошибка не TypeError"); }
  }
  put("native_functions", bad.length === 0, bad.length ? bad.slice(0, 6).join("; ") + (bad.length > 6 ? ` …ещё ${bad.length - 6}` : "") : "все выглядят нативными");

  // 3. собственные свойства у navigator и посторонние глобальные переменные
  const navOwn = Object.getOwnPropertyNames(navigator);
  const suspicious = Object.getOwnPropertyNames(window).filter(k => k !== "__caDetect" &&
    /^(__ca|__pw|__playwright|cdc_|\$cdc|__puppeteer|__webdriver|__driver|__selenium)/i.test(k));
  put("no_artifacts", navOwn.length === 0 && suspicious.length === 0,
      navOwn.length || suspicious.length ? `navigator: [${navOwn}] window: [${suspicious}]` : "следов нет");

  // 4. стек ошибки не содержит кадров подмены: у нативной функции первый кадр — сразу вызывающий код
  let stackLeak = "";
  try { WebGLRenderingContext.prototype.getParameter.call(null, 37446); } catch (e) {
    const first = String(e.stack).split("\n")[1] || "";
    if (!/__caDetect/.test(first)) stackLeak = first.trim();
  }
  put("stack_clean", !stackLeak, stackLeak || "лишних кадров нет");

  // 5. canvas стабилен: два вызова дают одно и то же, однотонная заливка остаётся однотонной
  const c = document.createElement("canvas"); c.width = 220; c.height = 40;
  const x = c.getContext("2d");
  x.textBaseline = "top"; x.font = "16px Arial"; x.fillStyle = "#f60"; x.fillRect(0, 0, 100, 20);
  x.fillStyle = "#069"; x.fillText("claudeAnty, 😀 <canvas> 1.0", 2, 15);
  const a1 = c.toDataURL(), a2 = c.toDataURL();
  const s = document.createElement("canvas"); s.width = s.height = 16;
  const sx = s.getContext("2d"); sx.fillStyle = "rgb(10,20,30)"; sx.fillRect(0, 0, 16, 16);
  const px = sx.getImageData(0, 0, 16, 16).data;
  let solid = true;
  for (let i = 0; i < px.length; i += 4) if (px[i] !== 10 || px[i + 1] !== 20 || px[i + 2] !== 30) { solid = false; break; }
  put("canvas_stable", a1 === a2 && solid, `повтор совпадает: ${a1 === a2}, заливка однотонная: ${solid}`);

  // 6. audio стабилен между вызовами
  let audioOk = true, audioDetail = "";
  try {
    const ctx = new OfflineAudioContext(1, 4410, 44100);
    const osc = ctx.createOscillator(); osc.type = "triangle"; osc.frequency.value = 1000;
    osc.connect(ctx.destination); osc.start(0);
    const buf = await ctx.startRendering();
    const sum = (d) => { let t = 0; for (let i = 0; i < d.length; i++) t += Math.abs(d[i]); return t; };
    const s1 = sum(buf.getChannelData(0)), s2 = sum(buf.getChannelData(0));
    audioOk = s1 === s2; audioDetail = `повтор совпадает: ${audioOk}`;
  } catch (e) { audioDetail = "нет OfflineAudioContext"; }
  put("audio_stable", audioOk, audioDetail);

  // 7. iframe, созданный скриптом, видит тот же отпечаток
  const f = document.createElement("iframe"); f.style.display = "none"; document.body.appendChild(f);
  const fw = f.contentWindow;
  let glf = null;
  try { const g = fw.document.createElement("canvas").getContext("webgl"); const e = g.getExtension("WEBGL_debug_renderer_info"); glf = g.getParameter(e.UNMASKED_RENDERER_WEBGL); } catch (e) {}
  let glm = null;
  try { const g = document.createElement("canvas").getContext("webgl"); const e = g.getExtension("WEBGL_debug_renderer_info"); glm = g.getParameter(e.UNMASKED_RENDERER_WEBGL); } catch (e) {}
  const diffs = [];
  for (const k of ["hardwareConcurrency", "deviceMemory", "platform", "userAgent", "maxTouchPoints"])
    if (fw.navigator[k] !== navigator[k]) diffs.push(`${k}: ${fw.navigator[k]}≠${navigator[k]}`);
  if (fw.screen.width !== screen.width) diffs.push(`screen.width: ${fw.screen.width}≠${screen.width}`);
  if (glf !== glm) diffs.push(`webgl: ${glf}≠${glm}`);
  f.remove();
  put("iframe_consistent", diffs.length === 0, diffs.length ? diffs.join("; ") : "совпадает со страницей");

  // 8. окно и экран согласованы
  const sizeBad = [];
  const mobileUA = /Mobile/.test(navigator.userAgent);  // у телефона страница без meta viewport шире окна — это норма
  if (!mobileUA && innerWidth > outerWidth + 1) sizeBad.push(`innerWidth ${innerWidth} > outerWidth ${outerWidth}`);
  if (mobileUA && outerWidth !== screen.width) sizeBad.push(`телефон: outerWidth ${outerWidth} ≠ screen.width ${screen.width}`);
  if (outerWidth > screen.width + 16) sizeBad.push(`outerWidth ${outerWidth} > screen.width ${screen.width}`);
  if (screen.availWidth > screen.width) sizeBad.push("availWidth > width");
  if (matchMedia(`(device-width: ${screen.width}px)`).matches !== true) sizeBad.push("media device-width ≠ screen.width");
  put("window_screen", sizeBad.length === 0, sizeBad.join("; ") || `окно ${outerWidth}×${outerHeight}, экран ${screen.width}×${screen.height}`);

  // 9. признаки headless из CreepJS, которые зависят от нас
  const hl = [];
  if (typeof Notification !== "undefined" && Notification.permission === "denied") hl.push("Notification.permission = denied");
  if (!mobileUA && screen.height === screen.availHeight && screen.width === screen.availWidth) hl.push("нет панели задач (availHeight = height)");
  if (navigator.plugins.length === 0) hl.push("нет плагинов");
  if (navigator.pdfViewerEnabled === false) hl.push("PDF отключён");
  put("headless_signs", hl.length === 0, hl.join("; ") || "не найдены");

  // 10. navigator.webdriver
  put("webdriver", navigator.webdriver === false, String(navigator.webdriver));
  return R;
};
