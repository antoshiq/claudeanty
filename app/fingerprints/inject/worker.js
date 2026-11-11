(() => {
  // Подмена внутри Web Worker / SharedWorker / ServiceWorker (воркер стоит на паузе, пока скрипт не отработает).
  // UA, платформа и языки задаются через CDP (Network.setUserAgentOverride); здесь — то, чего CDP в воркерах не умеет.
  const FP = __FP__;
  const TOKEN = FP.token;  // метка скрипта (sourceURL)
  const nativeToString = Function.prototype.toString;
  const masked = new WeakMap();
  const sig = (n) => `function ${n}() { [native code] }`;
  // стек ошибок нативных функций пересобирается с точки вызова обёртки (см. stealth.js), e.stack не читаем
  const captureStack = Error.captureStackTrace;
  const ownError = (e) => { try { return e instanceof TypeError && /Illegal invocation|requires that 'this'/.test(e.message); }
                            catch (x) { return false; } };
  const clean = (e, fn, always) => {
    if ((always || ownError(e)) && e !== null && typeof e === "object") {
      try { captureStack(e, fn); } catch (x) {}
    }
    return e;
  };
  const fnToString = { toString() { if (masked.has(this)) return masked.get(this);
    try { return nativeToString.call(this); } catch (e) { throw clean(e, fnToString); } } }.toString;
  masked.set(fnToString, sig("toString"));
  Object.defineProperty(Function.prototype, "toString", { value: fnToString, writable: true, enumerable: false, configurable: true });
  const defGet = (obj, prop, value) => {
    const d = Object.getOwnPropertyDescriptor(obj, prop);
    if (!d || !d.get) return;
    const g = Object.getOwnPropertyDescriptor({ get [prop]() { try { d.get.call(this); } catch (e) { throw clean(e, g, true); } return value; } }, prop).get;
    masked.set(g, sig("get " + prop));
    Object.defineProperty(obj, prop, { get: g, set: d.set, enumerable: d.enumerable, configurable: true });
  };

  if (self.WorkerNavigator) {
    defGet(WorkerNavigator.prototype, "hardwareConcurrency", FP.cores);
    defGet(WorkerNavigator.prototype, "deviceMemory", FP.memory);
    defGet(WorkerNavigator.prototype, "platform", FP.platform);  // CDP-платформа до воркеров не доходит
  }
  const GLP = FP.webgl.params || {};
  for (const C of [self.WebGLRenderingContext, self.WebGL2RenderingContext]) {
    if (!C) continue;
    const orig = C.prototype.getParameter;
    const f = { getParameter(...a) {
      try {
        const real = orig.apply(this, a);
        const p = a[0];
        if (p === 37445) return real === null ? real : FP.webgl.vendor;  // без расширения Chrome отдаёт null
        if (p === 37446) return real === null ? real : FP.webgl.renderer;
        const v = GLP[p];
        if (v !== undefined) return (v && v.i) ? new Int32Array(v.i) : (v && v.f) ? new Float32Array(v.f) : v;
        return real;
      } catch (e) { throw clean(e, f); }
    } }.getParameter;
    Object.defineProperty(f, "length", { value: 1 });
    masked.set(f, sig("getParameter"));
    Object.defineProperty(C.prototype, "getParameter", { value: f, writable: true, enumerable: true, configurable: true });
  }
})();
