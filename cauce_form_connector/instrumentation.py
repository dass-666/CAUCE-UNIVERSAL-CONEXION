"""Instrumentación para DEMOSTRAR la escritura secuencial (pruebas y demo).

Se inyecta en la página antes de que cargue y registra, por campo, cuántos
keydown/keyup/input/paste recibió y si alguien asignó `.value` por código.
No se usa durante el relleno normal: solo para generar evidencia.
"""

KEYLOG_JS = r"""
(() => {
  window.__keylog = {};
  window.__valueSets = {};
  const id = (e) => (e.target && (e.target.id || e.target.name)) || '?';
  const rec = (e) => {
    const k = id(e);
    const r = window.__keylog[k] = window.__keylog[k] || {keydown: 0, keyup: 0, keypress: 0, input: 0, paste: 0, enter: 0, inputTypes: {}, keys: []};
    if (e.type === 'keydown') { if (e.key.length === 1) { r.keydown++; r.keys.push(e.key); } if (e.key === 'Enter') r.enter++; }
    if (e.type === 'keyup' && e.key.length === 1) r.keyup++;
    if (e.type === 'keypress') r.keypress++;
    if (e.type === 'input') { r.input++; r.inputTypes[e.inputType] = (r.inputTypes[e.inputType] || 0) + 1; }
    if (e.type === 'paste') r.paste++;
  };
  ['keydown', 'keyup', 'keypress', 'input', 'paste'].forEach((t) => document.addEventListener(t, rec, true));
  // Cuenta asignaciones programáticas de .value (lo que el conector NO debe hacer en texto)
  for (const proto of [HTMLInputElement.prototype, HTMLTextAreaElement.prototype]) {
    const d = Object.getOwnPropertyDescriptor(proto, 'value');
    Object.defineProperty(proto, 'value', {
      configurable: true, get() { return d.get.call(this); },
      set(v) { const k = this.id || this.name || '?'; window.__valueSets[k] = (window.__valueSets[k] || 0) + 1; return d.set.call(this, v); },
    });
  }
})();
"""
