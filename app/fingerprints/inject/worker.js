(() => {
  // Подмена внутри Web Worker / SharedWorker / ServiceWorker (воркер стоит на паузе, пока скрипт не отработает).
  // UA, платформа и языки задаются через CDP (Network.setUserAgentOverride); здесь — то, чего CDP в воркерах не умеет.
  const FP = __FP__;
  const TOKEN = FP.token;
  const nativeToString = Function.prototype.toString;
  const masked = new WeakMap();
  const sig = (n) => `function ${n}() { [native code] }`;
  const clean = (e) => { try { if (e && typeof e.stack === "string" && e.stack.includes(TOKEN))
    e.stack = e.stack.split("\n").filter(l => !l.includes(TOKEN)).join("\n"); } catch (x) {} return e; };
  const fnToString = { toString() { if (masked.has(this)) return masked.get(this);
    try { return nativeToString.call(this); } catch (e) { throw clean(e); } } }.toString;
  masked.set(fnToString, sig("toString"));
  Object.defineProperty(Function.prototype, "toString", { value: fnToString, writable: true, enumerable: false, configurable: true });
  const defGet = (obj, prop, value) => {
    const d = Object.getOwnPropertyDescriptor(obj, prop);
    if (!d || !d.get) return;
    const g = Object.getOwnPropertyDescriptor({ get [prop]() { try { d.get.call(this); } catch (e) { throw clean(e); } return value; } }, prop).get;
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
        if (p === 37445) return FP.webgl.vendor;
        if (p === 37446) return FP.webgl.renderer;
        const v = GLP[p];
        if (v !== undefined) return (v && v.i) ? new Int32Array(v.i) : (v && v.f) ? new Float32Array(v.f) : v;
        return real;
      } catch (e) { throw clean(e); }
    } }.getParameter;
    Object.defineProperty(f, "length", { value: 1 });
    masked.set(f, sig("getParameter"));
    Object.defineProperty(C.prototype, "getParameter", { value: f, writable: true, enumerable: true, configurable: true });
  }
})();
