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

  // Ids generados por la biblioteca (ext-comp-1002, ember123, :r1:) cambian entre cargas: no sirven de ancla estable.
  const autoId = (id) => /^(ext-(comp|gen)-?\d+|ember\d+|:r[0-9a-z]+:|react-select-\d+.*)$/i.test(id || "");

  const cssPath = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      if (node.id && !autoId(node.id) && document.querySelectorAll("#" + esc(node.id)).length === 1) {
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
    if (el.id && !autoId(el.id) && document.querySelectorAll("#" + esc(el.id)).length === 1) return "#" + esc(el.id);
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

  // Controles «cuadro de texto + lista» (combos editables): ARIA, datalist o una flecha/botón junto al cuadro.
  const TRIGGER = '[class*="trigger" i],[class*="arrow" i],[class*="caret" i],[class*="chevron" i],[class*="dropdown-toggle" i],[class*="combo-btn" i]';
  const comboInfo = (el) => {
    if (el.tagName.toLowerCase() !== "input") return null;
    const type = (el.getAttribute("type") || "text").toLowerCase();
    if (type !== "text" && type !== "search") return null;
    const role = (el.getAttribute("role") || "").toLowerCase();
    const popup = (el.getAttribute("aria-haspopup") || "").toLowerCase();
    let trigger = null;
    const parent = el.parentElement;
    if (parent) {
      trigger = Array.from(parent.children).find((c) => c !== el && c.matches(TRIGGER) &&
        !/^(input|textarea|select)$/i.test(c.tagName) && !(c.tagName === "BUTTON" && (c.type || "") === "submit")) || null;
    }
    const aria = role === "combobox" || (popup && popup !== "false" && popup !== "dialog") ||
      el.hasAttribute("aria-autocomplete") || el.hasAttribute("list");
    if (!aria && !trigger) return null;
    // Campo oculto hermano con nombre: guarda el valor interno (en Ext JS: hiddenName). Solo se LEE.
    let hidden = null;
    if (parent) hidden = Array.from(parent.querySelectorAll('input[type="hidden"][name]')).find((h) => h.name) || null;
    return { trigger: trigger ? selectorFor(trigger) : "", hiddenSel: hidden ? selectorFor(hidden) : "",
             hiddenName: hidden ? hidden.name : "" };
  };

  const describe = (el) => {
    const tag = el.tagName.toLowerCase();
    const combo = comboInfo(el);
    let type = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : tag;
    if (tag === "select") type = el.multiple ? "select-multiple" : "select";
    const d = {
      tag, type,
      name: el.getAttribute("name") || (combo ? combo.hiddenName : "") || "",
      id: el.id || "",
      widget: combo ? "combo" : "",
      trigger_selector: combo ? combo.trigger : "",
      value_selector: combo ? combo.hiddenSel : "",
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

  const controls = "input,select,textarea";
  const forms = Array.from(document.querySelectorAll("form"));
  const result = { forms: [], orphan_fields: [], signals: {} };

  forms.forEach((f, i) => {
    const fields = Array.from(f.elements).filter((e) => e.matches && e.matches(controls)).map(describe);
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
  result.orphan_fields = Array.from(document.querySelectorAll(controls))
    .filter((e) => !e.form && !e.closest("form")).map(describe);
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
