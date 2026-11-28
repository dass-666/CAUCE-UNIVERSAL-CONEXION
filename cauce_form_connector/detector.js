// Detector de formularios que se ejecuta DENTRO del navegador (page.evaluate).
// Lee el DOM ya renderizado (incluidos campos creados por JavaScript) y
// devuelve una estructura "cruda" que form_detector.py normaliza.
// No modifica la página.
() => {
  const esc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/([^\w-])/g, "\\$1");
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

  const isVisible = (el) => {
    if (!el.isConnected) return false;
    const st = getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden") return false;
    if (el.type === "hidden") return false;
    const r = el.getBoundingClientRect();
    // Casillas personalizadas suelen ocultar el input real con opacity 0: se considera visible
    return r.width > 0 || r.height > 0 || el.getClientRects().length > 0;
  };

  const cssPath = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      if (node.id && document.querySelectorAll("#" + esc(node.id)).length === 1) {
        parts.unshift("#" + esc(node.id));
        break;
      }
      let sel = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (same.length > 1) sel += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(sel);
      node = node.parentElement;
    }
    return parts.join(" > ");
  };

  const selectorFor = (el) => {
    if (el.id && document.querySelectorAll("#" + esc(el.id)).length === 1) return "#" + esc(el.id);
    const tag = el.tagName.toLowerCase();
    if (el.name) {
      const s = `${tag}[name="${el.name.replace(/"/g, '\\"')}"]`;
      if (document.querySelectorAll(s).length === 1) return s;
    }
    return cssPath(el);
  };

  const textOf = (node) => clean(node ? node.innerText || node.textContent : "");

  const labelFor = (el) => {
    // 1) <label for=id>
    if (el.id) {
      const l = document.querySelector(`label[for="${el.id.replace(/"/g, '\\"')}"]`);
      if (l) return textOf(l);
    }
    // 2) label envolvente
    const wrap = el.closest("label");
    if (wrap) {
      const copy = wrap.cloneNode(true);
      copy.querySelectorAll("input,select,textarea").forEach((n) => n.remove());
      const t = clean(copy.textContent);
      if (t) return t;
    }
    // 3) aria-labelledby
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const t = lb.split(/\s+/).map((id) => textOf(document.getElementById(id))).join(" ");
      if (clean(t)) return clean(t);
    }
    // 4) texto inmediatamente anterior dentro del mismo contenedor
    let prev = el.previousElementSibling;
    while (prev) {
      if (/^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(prev.tagName)) break;
      const t = textOf(prev);
      if (t && t.length < 120) return t;
      prev = prev.previousElementSibling;
    }
    const parent = el.parentElement;
    if (parent && parent.querySelectorAll("input,select,textarea").length === 1) {
      const copy = parent.cloneNode(true);
      copy.querySelectorAll("input,select,textarea,script,style").forEach((n) => n.remove());
      const t = clean(copy.textContent);
      if (t && t.length < 120) return t;
    }
    return "";
  };

  const intAttr = (el, name) => {
    const v = el.getAttribute(name);
    if (v === null || v === "" || isNaN(parseInt(v, 10))) return null;
    const n = parseInt(v, 10);
    return n >= 0 ? n : null;
  };

  // Pistas del formato que espera el campo (solo texto de la página, nunca valores escritos): atributos de las librerías
  // de calendario y máscaras, title y el texto de ayuda asociado.
  // Formato en notación de PHP (Ext JS, flatpickr): d/j día, m/n mes, Y año de 4 cifras. Cualquier otra letra (año de
  // 2 cifras, nombres de mes, horas) hace que no se use: no se adivina.
  const phpFecha = (f) => /^[djmnY]([\/\-.])[djmnY]\1[djmnY]$/.test(f)
    ? f.replace(/[dj]/, "dd").replace(/[mn]/, "mm").replace(/Y/, "yyyy") : "";
  // El campo es de fecha según su componente o sus clases (para no confundirlo con una lista ni abrir su calendario)
  const pareceFecha = (el) => {
    try { const E = window.Ext; if (E) { let c = null;
        try { if (E.Component && E.Component.fromElement) c = E.Component.fromElement(el); } catch (e) {}
        if (!c && E.getCmp) c = E.getCmp((el.id || "").replace(/-inputEl$/, "")) || null;
        if (c && c.isXType && (c.isXType("datefield") || c.isXType("timefield"))) return true; } } catch (e) {}
    if (el._flatpickr || /hasDatepicker|datepicker|flatpickr/i.test(el.className || "")) return true;
    for (let n = el.parentElement, k = 0; n && k < 3 && n.tagName !== "FORM" && n !== document.body; n = n.parentElement, k++) {
      if (Array.from(n.querySelectorAll("input:not([type=hidden]),select,textarea")).some((x) => x !== el && isVisible(x))) break;
      if (n.querySelector("[class*=date-trigger i],[class*=datepicker i],[class*=calendar i]")) return true;
    }
    return false;
  };
  const numerico = (el) => el.getAttribute("role") === "spinbutton";

  const formatHint = (el) => {
    const out = [];
    for (const a of ["data-date-format", "data-dateformat", "data-format", "data-mask", "data-inputmask",
      "data-inputmask-inputformat", "data-inputmask-alias", "data-pattern", "dateformat", "format", "title", "aria-placeholder"]) {
      const v = el.getAttribute(a);
      if (v) out.push(v);
    }
    // Formato configurado en el componente de calendario del propio campo (lectura de su configuración, sin tocarlo)
    try {
      const $ = window.jQuery;
      if ($ && $.fn && $.fn.datepicker && /hasDatepicker/.test(el.className)) {       // jQuery UI: «yy» es el año de 4 cifras
        const f = $(el).datepicker("option", "dateFormat");
        if (typeof f === "string" && !/(^|[^y])y([^y]|$)/.test(f)) out.push(f.replace(/yy/g, "yyyy").replace(/\bd\b/g, "dd").replace(/\bm\b/g, "mm"));
      } else if ($ && $.fn) {
        const d = $(el).data("datepicker") || $(el).data("DateTimePicker");
        const f = d && ((d.o && d.o.format) || (typeof d.format === "function" ? d.format() : d.format));
        if (typeof f === "string") out.push(f);
      }
      if (el._flatpickr && el._flatpickr.config && typeof el._flatpickr.config.dateFormat === "string" && !el._flatpickr.altInput) out.push(phpFecha(el._flatpickr.config.dateFormat));
      // flatpickr con campo alternativo: el cuadro visible usa «altFormat»
      document.querySelectorAll("input.flatpickr-input").forEach((o) => { const fp = o._flatpickr;
        if (fp && fp.altInput === el && fp.config && typeof fp.config.altFormat === "string") out.push(phpFecha(fp.config.altFormat)); });
      // Ext JS: el campo de fecha declara su formato (notación de PHP: d/m/Y)
      const E = window.Ext;
      if (E) {
        let c = null;
        try { if (E.Component && E.Component.fromElement) c = E.Component.fromElement(el); } catch (e) {}
        if (!c && E.getCmp) c = E.getCmp((el.id || "").replace(/-inputEl$/, "")) || E.getCmp(el.id) || null;
        if (c && c.isXType && c.isXType("datefield") && typeof c.format === "string") out.push(phpFecha(c.format));
      }
      // Inputmask con formato de fecha explícito (una máscara 99/99/9999 no dice el orden y no se usa)
      const im = el.inputmask && el.inputmask.opts;
      if (im && typeof im.inputFormat === "string" && /d/i.test(im.inputFormat) && /y/i.test(im.inputFormat)) out.push(im.inputFormat);
      const M = window.M;
      const mi = M && M.Datepicker && M.Datepicker.getInstance && M.Datepicker.getInstance(el);
      if (mi && mi.options && typeof mi.options.format === "string") out.push(mi.options.format);
    } catch (e) {}
    const ids = (el.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    for (const id of ids) { const t = textOf(document.getElementById(id)); if (t) out.push(t); }
    const nx = el.nextElementSibling;
    if (nx && /^(SMALL|SPAN|P|DIV|EM|I)$/.test(nx.tagName) && !nx.querySelector("input,select,textarea,button")) {
      const t = textOf(nx); if (t && t.length < 80) out.push(t);
    }
    return clean(out.join(" | ")).slice(0, 300);
  };

  const describe = (el) => {
    const tag = el.tagName.toLowerCase();
    let type = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : tag;
    if (tag === "select") type = el.multiple ? "select-multiple" : "select";
    const d = {
      tag, type,
      name: el.getAttribute("name") || "",
      id: el.id || "",
      selector: selectorFor(el),
      label: labelFor(el),
      placeholder: el.getAttribute("placeholder") || "",
      aria_label: el.getAttribute("aria-label") || "",
      autocomplete: (el.getAttribute("autocomplete") || "").toLowerCase(),
      required: el.required || el.getAttribute("aria-required") === "true",
      disabled: !!el.disabled,
      readonly: !!el.readOnly,
      visible: isVisible(el),
      pattern: el.getAttribute("pattern") || "",
      minlength: intAttr(el, "minlength"),
      maxlength: intAttr(el, "maxlength"),
      min: el.getAttribute("min") || "",
      max: el.getAttribute("max") || "",
      step: el.getAttribute("step") || "",
      inputmode: el.getAttribute("inputmode") || "",
      format_hint: formatHint(el),
      blocks_paste: el.hasAttribute("onpaste") || el.dataset.noPaste !== undefined,
      confirm_hint: el.getAttribute("data-confirm") || el.getAttribute("data-match") || "",
      value: type === "checkbox" || type === "radio" ? (el.value || "on") : "",
      checked: !!el.checked,
      options: [],
    };
    if (tag === "select") {
      d.options = Array.from(el.options).map((o) => ({ value: o.value, label: clean(o.text), selected: o.selected }));
    }
    if (type === "radio") {
      const group = el.closest("fieldset");
      const legend = group ? group.querySelector("legend") : null;
      d.group_label = legend ? textOf(legend) : "";
    }
    return d;
  };

  // ---------------------------------------------------------------------------------------------------------
  // Listas desplegables hechas con JavaScript -> type "custom-select". Se reconocen por cuatro vías, en este orden:
  //  1) <select> nativo que la página oculta y sustituye por un widget (Select2, Chosen, Tom Select, Choices...):
  //     las opciones y el valor registrado son los del <select>; se interactúa con el widget visible.
  //  2) ARIA: role=combobox (también sobre <input>) o aria-haspopup=listbox (MUI, Ant Design, Element Plus...).
  //  3) input oculto con nombre dentro de un contenedor con clase de desplegable.
  //  4) elemento pulsable cuya clase o etiqueta indica desplegable (select/dropdown/combo/...), sin control nativo.
  // Los controles nativos internos del widget (buscador, select sustituido) no se listan como campos aparte.
  // Las opciones que solo existen al abrir la lista se obtienen después con un clic normal (listas.py).
  const DROP_RE = /select|dropdown|combo|listbox|picker|choices|chosen|autocomplete|multiselect/i;
  const ID_INESTABLE = /\d{3,}|^:|^rc[_-]|^el-id|^react-select|^headlessui|^radix|^mui-\d|^downshift|^select2-|^choices-/i;
  const cls = (el) => (el.getAttribute && el.getAttribute("class")) || "";
  const visibleReal = (el) => {
    if (!el || !isVisible(el)) return false;
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    return r.width > 4 && r.height > 4 && st.opacity !== "0" && !/hidden-accessible|visually-hidden|sr-only/i.test(cls(el));
  };
  const slugJs = (t) => clean(t).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 30);
  const idEstable = (el) => (el && el.id && !ID_INESTABLE.test(el.id)) ? el.id : "";
  const pulsable = (c) => c.hasAttribute("tabindex") || getComputedStyle(c).cursor === "pointer" || !!c.querySelector("[tabindex]");
  const Q_DISPARADOR = "[role=combobox],[aria-haspopup],[class*=selection i],[class*=single i],[class*=control i],[class*=inner i],"
    + "[class*=toggle i],[class*=trigger i],[class*=wrapper i],[class*=main i],button,a,[tabindex]";
  const disparador = (w) => (w.matches(Q_DISPARADOR) ? [w] : []).concat(Array.from(w.querySelectorAll(Q_DISPARADOR)))
    .filter((x) => !x.matches("input[type=hidden],select,option") && visibleReal(x))[0] || w;

  const widgetDe = (sel) => {     // widget visible que sustituye a un <select> que la página oculta
    if (visibleReal(sel) || sel.multiple) return null;
    const esWidget = (c) => c && c.tagName !== "LABEL" && !c.matches("input,select,textarea,option,script,style") && visibleReal(c)
      && (DROP_RE.test(cls(c)) || c.matches("[role=combobox]") || !!c.querySelector("[role=combobox]"));
    for (const c of [sel.nextElementSibling, sel.previousElementSibling]) {
      if (esWidget(c) && !c.querySelector("select")) return c;
    }
    let mejor = null;             // el ancestro MÁS EXTERNO que sigue siendo solo este widget
    for (let n = sel.parentElement, k = 0; n && k < 3 && n.tagName !== "FORM" && n !== document.body; n = n.parentElement, k++) {
      if (n.querySelectorAll("select").length !== 1) break;
      if (esWidget(n)) mejor = n;
      else if (!mejor && esWidget(n.nextElementSibling) && !n.nextElementSibling.querySelector("select") && !visibleReal(n)) return n.nextElementSibling;
    }
    if (mejor) return mejor;
    const sig = sel.nextElementSibling;   // último recurso: hermano visible y pulsable justo después del select oculto
    return (sig && !sig.matches("input,select,textarea,label") && visibleReal(sig) && pulsable(sig)) ? sig : null;
  };

  const raizAria = (e, raiz) => {  // contenedor del widget: el ancestro más externo con clase de desplegable y un solo control
    let mejor = null;
    for (let n = e.parentElement, k = 0; n && k < 5 && n !== raiz && n.tagName !== "FORM" && n !== document.body; n = n.parentElement, k++) {
      if (n.querySelectorAll("[role=combobox],[aria-haspopup=listbox]").length > 1) break;
      if (Array.from(n.querySelectorAll("input:not([type=hidden]),select,textarea")).some((x) => x !== e && visibleReal(x) && !x.matches("[role=combobox]"))) break;
      if (DROP_RE.test(cls(n)) || DROP_RE.test(n.tagName)) mejor = n;
    }
    if (mejor) return mejor;
    for (let n = e.parentElement, k = 0; n && k < 3 && n !== raiz; n = n.parentElement, k++) {
      if (n.querySelectorAll("input[type=hidden],input[aria-hidden=true]").length === 1
          && n.querySelectorAll("[role=combobox],[aria-haspopup=listbox]").length === 1) return n;
    }
    return e.parentElement || e;
  };

  const etiquetaLista = (el, root, sel) => {
    const porFor = (n) => { if (!n || !n.id) return ""; const l = document.querySelector(`label[for="${n.id.replace(/"/g, '\\"')}"]`); return l ? textOf(l) : ""; };
    let t = porFor(sel) || porFor(el);
    if (t) return t;
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {       // solo etiquetas de fuera del widget: dentro está el valor elegido, que cambia
      t = clean(lb.split(/\s+/).map((id) => document.getElementById(id)).filter((n) => n && !root.contains(n) && !el.contains(n)).map(textOf).join(" "));
      if (t) return t;
    }
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    if (root.nextElementSibling && root.nextElementSibling.tagName === "LABEL" && !root.nextElementSibling.getAttribute("for")) {
      t = textOf(root.nextElementSibling);      // etiqueta flotante colocada después del control
      if (t && t.length < 120) return t;
    }
    for (let n = root, k = 0; n && k < 3 && n.tagName !== "FORM" && n !== document.body; n = n.parentElement, k++) {
      t = porFor(n);
      if (t) return t;
      for (let p = n.previousElementSibling; p; p = p.previousElementSibling) {
        if (p.tagName === "SELECT" || p.matches("script,style,input[type=hidden]")) continue;
        if (p.matches("input,textarea,button") || p.querySelector("input:not([type=hidden]),textarea,[role=combobox]")) break;
        const tx = textOf(p);
        if (tx && tx.length < 120) return tx;
      }
    }
    return "";
  };

  const opcionesEnDom = (el, root) => {
    const lb = document.getElementById(el.getAttribute("aria-controls") || "") || document.getElementById(el.getAttribute("aria-owns") || "");
    for (const base of [lb, root]) {
      if (!base) continue;
      for (const s of ["[role=option]", "[data-value]", "li", "[class*=option i]", "[class*=item i]"]) {
        const xs = Array.from(base.querySelectorAll(s)).filter((o) => o !== el && !el.contains(o) && !o.contains(el)
          && !o.matches("input,select,textarea,option") && textOf(o) && o.getAttribute("data-value") !== ""
          && !/placeholder|disabled/i.test(cls(o)) && o.getAttribute("aria-disabled") !== "true");
        if (xs.length) {
          const vistos = new Set();
          return xs.map((o) => ({ value: o.getAttribute("data-value") || textOf(o), label: textOf(o), selected: false }))
            .filter((o) => !vistos.has(o.value) && vistos.add(o.value));
        }
      }
    }
    return [];
  };

  const listasDe = (raiz, conGenericas) => {
    const campos = [], reclamados = [], internos = new Set();
    const libre = (x) => !reclamados.some((r) => r === x || r.contains(x) || x.contains(r));
    const nuevo = (el, root, sel, nombre, idPreferido) => {
      const d = describe(el);
      d.type = "custom-select"; d.tag = el.tagName.toLowerCase();
      d.label = etiquetaLista(el, root, sel);
      d.name = sel ? (sel.getAttribute("name") || "") : "";
      d.id = sel ? (sel.id || "") : (idEstable(el) || idPreferido || idEstable(root) || (nombre ? "lista_" + nombre : "lista_" + (slugJs(d.label) || "sin_nombre")));
      d.required = sel ? sel.required : (el.getAttribute("aria-required") === "true" || root.getAttribute("aria-required") === "true");
      d.disabled = sel ? sel.disabled : (el.getAttribute("aria-disabled") === "true" || /(^|[\s-])disabled($|[\s-])/i.test(cls(root)));
      d.readonly = false; d.visible = visibleReal(el) || visibleReal(root);
      d.placeholder = ""; d.pattern = ""; d.minlength = null; d.maxlength = null; d.autocomplete = ""; d.confirm_hint = "";
      const nativas = sel ? Array.from(sel.options).filter((o) => o.value !== "" && !o.disabled)
        .map((o) => ({ value: o.value, label: clean(o.text), selected: false })) : [];
      d.options = nativas.length ? nativas : opcionesEnDom(el, root);
      reclamados.push(root);
      root.querySelectorAll("input:not([type=hidden]),select,textarea").forEach((x) => internos.add(x));
      if (el.matches("input,select,textarea")) internos.add(el);
      if (sel) internos.add(sel);
      campos.push(d);
    };
    // 1) <select> sustituido por un widget
    Array.from(raiz.querySelectorAll("select")).forEach((sel) => {
      const w = widgetDe(sel);
      if (w && libre(w)) nuevo(disparador(w), w, sel);
    });
    // 2) ARIA
    Array.from(raiz.querySelectorAll("[role=combobox],[aria-haspopup=listbox]")).forEach((e) => {
      if (e.matches("select,textarea,option") || !libre(e)) return;
      if (e.matches("input") && (pareceFecha(e) || numerico(e))) return;      // fecha con calendario o número: no es una lista
      if (!visibleReal(e) && !e.matches("input")) return;
      const root = e.matches("input") ? raizAria(e, raiz) : (raizAria(e, raiz).contains(e) ? raizAria(e, raiz) : e.parentElement);
      if (!libre(root) || !visibleReal(root)) return;
      const h = root.querySelector("input[type=hidden][name]");
      nuevo(visibleReal(e) ? e : disparador(root), root, null, h ? h.name : "");
    });
    // 3) input oculto con nombre en un contenedor con clase de desplegable
    Array.from(raiz.querySelectorAll("input[type=hidden][name]")).forEach((h) => {
      const c = h.parentElement;
      if (!c || !libre(c) || c.querySelector("select") || !visibleReal(c)) return;
      // Si lo que la persona usa es un cuadro de texto con nombre, el control es ese cuadro: se comprueba aparte si
      // abre una lista (sondeo de cuadros de texto), en vez de darlo por lista solo por la clase de su envoltorio.
      if (Array.from(c.querySelectorAll("input:not([type=hidden])")).some((x) => visibleReal(x) && x.getAttribute("name") && /^(text|search|)$/i.test(x.getAttribute("type") || ""))) return;
      const t = Array.from(c.querySelectorAll("[class*=toggle i],[class*=trigger i],[class*=selected i],[class*=display i],[class*=current i],button")).filter(visibleReal)[0]
        || Array.from(c.children).find((x) => x !== h && !/^(UL|OL|INPUT|LABEL)$/.test(x.tagName) && visibleReal(x));
      if (!t || !(DROP_RE.test(cls(c)) || DROP_RE.test(cls(t)))) return;
      nuevo(t, c, null, h.name, "lista_" + h.name);
    });
    // 4) genéricas: clase o etiqueta de desplegable, pulsable, sin control nativo visible dentro
    if (conGenericas) {
      Array.from(raiz.querySelectorAll("*")).forEach((c) => {
        if (!(DROP_RE.test(cls(c)) || /-select|dropdown/i.test(c.tagName)) || c.matches("label,option,optgroup,form,fieldset,script,style")) return;
        if (!libre(c) || !visibleReal(c) || !pulsable(c) || c.closest("[role=listbox],ul,ol")) return;
        if (c.querySelector("select,textarea,input:not([type=hidden])") || textOf(c).length > 160) return;
        // Adorno de un cuadro de texto (flecha, botones de subir/bajar, icono de calendario): el control es el cuadro
        for (let n = c.parentElement, k = 0; n && k < 3 && n !== raiz && n !== document.body; n = n.parentElement, k++) {
          const vis = Array.from(n.querySelectorAll("input:not([type=hidden]),textarea")).filter(visibleReal);
          if (vis.length === 1) return;
          if (vis.length > 1) break;
        }
        nuevo(disparador(c), c, null, "");
      });
    }
    return { campos, internos };
  };

  const controls = "input,select,textarea";
  const forms = Array.from(document.querySelectorAll("form"));
  const result = { forms: [], orphan_fields: [], signals: {} };

  forms.forEach((f, i) => {
    const listas = listasDe(f, true);
    const fields = Array.from(f.elements).filter((e) => e.matches && e.matches(controls) && !listas.internos.has(e)).map(describe)
      .concat(listas.campos);
    const submits = Array.from(f.querySelectorAll('button, input[type=submit], input[type=image]'))
      .filter((b) => (b.type || "submit").toLowerCase() === "submit" || b.tagName === "INPUT")
      .map((b) => clean(b.innerText || b.value || b.getAttribute("aria-label") || "Enviar"));
    result.forms.push({
      index: i, selector: f.id ? "#" + esc(f.id) : cssPath(f), name: f.getAttribute("name") || "",
      id: f.id || "", action: f.getAttribute("action") || "", method: (f.getAttribute("method") || "get").toLowerCase(),
      fields, submit_buttons: submits,
    });
  });

  // Campos fuera de cualquier <form> (frecuente en aplicaciones SPA)
  const fuera = (e) => !e.form && !e.closest("form");
  const listasSueltas = { campos: [], internos: new Set() };
  if (Array.from(document.querySelectorAll("select,[role=combobox],[aria-haspopup=listbox]")).some(fuera)) {
    // fuera de <form>: solo vías 1 y 2 (las genéricas confundirían menús de navegación con campos)
    const tmp = listasDe(document.body, false);
    tmp.campos.forEach((d) => { const el = document.querySelector(d.selector); if (el && fuera(el)) listasSueltas.campos.push(d); });
    tmp.internos.forEach((x) => { if (fuera(x)) listasSueltas.internos.add(x); });
  }
  result.orphan_fields = Array.from(document.querySelectorAll(controls))
    .filter((e) => fuera(e) && !listasSueltas.internos.has(e)).map(describe).concat(listasSueltas.campos);
  result.orphan_submits = Array.from(document.querySelectorAll("button")).filter((b) => !b.closest("form"))
    .map((b) => clean(b.innerText)).filter((t) => /enviar|submit|send|registrar|confirmar|guardar/i.test(t));

  // Señales de que la página exige intervención humana (no se intentan evadir)
  const html = document.documentElement.outerHTML.toLowerCase();
  const meta = document.querySelector('meta[name="automation"], meta[name="robots-automation"]');
  result.signals = {
    captcha_iframe: Array.from(document.querySelectorAll("iframe")).some((fr) =>
      /recaptcha|hcaptcha|turnstile|challenges\.cloudflare|arkoselabs|funcaptcha|captcha/i.test(fr.src || "")),
    captcha_widget: !!document.querySelector(".g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey], #captcha, [name*=captcha i]"),
    captcha_text: /no soy un robot|i'm not a robot|verify you are human|verifica que eres humano/.test(html),
    meta_no_automation: !!(meta && /none|deny|human/i.test(meta.getAttribute("content") || "")),
    human_only_markers: document.querySelectorAll("[data-human-only]").length,
  };
  return result;
}
