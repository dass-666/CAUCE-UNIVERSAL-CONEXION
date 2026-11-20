// Interfaz de revisión. JavaScript sin dependencias.
const $ = (s) => document.querySelector(s);
const state = { tab: "lab", analysis: null, proposals: [], columns: [], rows: 0, mapping: {} };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function msg(el, text, kind = "info") {
  el.textContent = text;
  el.className = "msg " + kind;
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Error ${r.status}`);
  return data;
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ------------------------------------------------------------ pestañas
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  state.tab = b.dataset.tab;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("activo", t === b));
  document.querySelectorAll(".panel").forEach((p) => (p.hidden = p.dataset.panel !== state.tab));
}));

$("#html-file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (f) $("#html-in").value = await f.text();
});

// ------------------------------------------------------------ análisis
function renderFields(a) {
  const blocked = a.safety.blocked
    ? `<div class="msg error"><strong>Requiere intervención humana:</strong> ${a.safety.reasons.map(esc).join(" ")} No se rellenará.</div>` : "";
  const warns = a.warnings.map((w) => `<div class="msg aviso">${esc(w)}</div>`).join("");
  const forms = a.forms.map((f) => `
    <h3>Formulario «${esc(f.id || f.name || f.key)}» · ${f.fields.length} campos
      ${f.submit_buttons.length ? `· envío: ${f.submit_buttons.map(esc).join(", ")}` : ""}</h3>
    <table><thead><tr><th>Etiqueta</th><th>Tipo</th><th>Oblig.</th><th>Validaciones</th><th>Opciones</th><th>Selector</th></tr></thead><tbody>
    ${f.fields.map((c) => `<tr class="${c.visible ? "" : "oculto"}">
      <td>${esc(c.label || c.placeholder || c.name)}${c.confirms ? ' <span class="chip">confirmación</span>' : ""}${c.visible ? "" : ' <span class="chip">oculto</span>'}</td>
      <td>${esc(c.type)}</td><td>${c.required ? "sí" : ""}</td>
      <td>${esc([c.pattern && "patrón " + c.pattern, c.minlength != null && "mín " + c.minlength, c.maxlength != null && "máx " + c.maxlength,
                 c.min && "≥ " + c.min, c.max && "≤ " + c.max, c.blocks_paste && "bloquea pegar"].filter(Boolean).join(" · "))}</td>
      <td>${esc(c.options.map((o) => o.label || o.value).join(" | "))}</td>
      <td><code>${esc(c.selector)}</code></td></tr>`).join("")}
    </tbody></table>`).join("");
  $("#campos").innerHTML = blocked + warns + forms;
}

$("#btn-analizar").addEventListener("click", async () => {
  const body = { mode: state.tab };
  if (state.tab === "lab") body.lab = $("#lab-sel").value;
  if (state.tab === "html") { body.html = $("#html-in").value; body.engine = $("#html-static").checked ? "static" : "browser"; }
  if (state.tab === "url") { body.url = $("#url-in").value; body.authorized = $("#url-auth").checked; }
  msg($("#analisis-msg"), "Analizando… (puede tardar unos segundos si la página es dinámica)");
  try {
    const r = await postJSON("/api/analyze", body);
    state.analysis = r.analysis;
    const n = r.analysis.forms.reduce((s, f) => s + f.fields.length, 0);
    msg($("#analisis-msg"), `${n} campos detectados en ${r.analysis.forms.length} formulario(s) · motor: ${r.analysis.engine === "browser" ? "navegador" : "estático"}`, "ok");
    renderFields(r.analysis);
    setProposals(r.proposals);
  } catch (e) { msg($("#analisis-msg"), e.message, "error"); }
});

// ------------------------------------------------------------ datos
$("#data-file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await api("/api/data", { method: "POST", body: fd });
    state.columns = r.columns; state.rows = r.rows;
    msg($("#datos-msg"), `${r.rows} fila(s), ${r.columns.length} columnas`, "ok");
    $("#row-sel").innerHTML = Array.from({ length: r.rows }, (_, i) => `<option value="${i}">Fila ${i + 1}</option>`).join("");
    $("#datos-preview").innerHTML = `<table><thead><tr>${r.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
      ${r.preview.map((row) => `<tr>${r.columns.map((c) => `<td>${esc(row[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    setProposals(r.proposals);
  } catch (err) { msg($("#datos-msg"), err.message, "error"); }
});

// ------------------------------------------------------------ correspondencias
function fieldByKey(k) {
  for (const f of state.analysis?.forms || []) for (const c of f.fields) if (c.key === k) return c;
  return null;
}

function setProposals(props) {
  state.proposals = props || [];
  state.mapping = {};
  state.proposals.forEach((p) => (state.mapping[p.field_key] = p.column));
  renderMapping();
}

function renderMapping() {
  if (!state.proposals.length) {
    $("#map-tabla").innerHTML = '<p class="ayuda">Analice un formulario y cargue datos para ver propuestas.</p>';
    return;
  }
  const opts = (sel) => ['<option value="">— no rellenar —</option>']
    .concat(state.columns.map((c) => `<option ${c === sel ? "selected" : ""}>${esc(c)}</option>`)).join("");
  $("#map-tabla").innerHTML = `<table><thead><tr><th>Campo</th><th>Columna de datos</th><th>Confianza</th><th>Estado</th><th>Motivo</th></tr></thead><tbody>
    ${state.proposals.map((p) => {
      const f = fieldByKey(p.field_key) || {};
      return `<tr><td>${esc(f.label || p.field_key)} <small>(${esc(f.type || "")})</small></td>
        <td><select data-key="${esc(p.field_key)}">${opts(state.mapping[p.field_key])}</select></td>
        <td>${Math.round(p.confidence * 100)}%</td><td><span class="estado ${esc(p.status)}">${esc(p.status.replace("_", " "))}</span></td>
        <td><small>${esc(p.reason)}</small></td></tr>`;
    }).join("")}</tbody></table>`;
  document.querySelectorAll("#map-tabla select").forEach((s) => s.addEventListener("change", () => {
    state.mapping[s.dataset.key] = s.value || null;
    s.closest("tr").querySelector(".estado").textContent = s.value ? "manual" : "ignorado";
    s.closest("tr").querySelector(".estado").className = "estado manual";
  }));
}

async function loadProfiles() {
  const list = await api("/api/profiles");
  $("#perfil-sel").innerHTML = '<option value="">— Configuraciones guardadas —</option>' +
    list.map((p) => `<option value="${esc(p.name)}">${esc(p.name)} · ${esc(p.source_label)}</option>`).join("");
}

$("#btn-guardar").addEventListener("click", async () => {
  try {
    const r = await postJSON("/api/profiles", { name: $("#perfil-nombre").value, mapping: state.mapping });
    msg($("#perfil-msg"), `Guardado en profiles/${r.saved}`, "ok");
    loadProfiles();
  } catch (e) { msg($("#perfil-msg"), e.message, "error"); }
});

$("#btn-cargar").addEventListener("click", async () => {
  const name = $("#perfil-sel").value;
  if (!name) return;
  try {
    const r = await api("/api/profiles/" + encodeURIComponent(name));
    Object.assign(state.mapping, r.mapping);
    renderMapping();
    const extra = r.same_structure === false ? ` · El formulario cambió: faltan ${r.missing_fields.length}, nuevos ${r.new_fields.length}.` : "";
    msg($("#perfil-msg"), `Configuración «${name}» aplicada${extra}`, r.same_structure === false ? "aviso" : "ok");
  } catch (e) { msg($("#perfil-msg"), e.message, "error"); }
});

// ------------------------------------------------------------ relleno
$("#btn-fill").addEventListener("click", async () => {
  msg($("#fill-msg"), "Escribiendo en el navegador… no toque esa ventana hasta que termine.");
  $("#resultados").innerHTML = "";
  try {
    const r = await postJSON("/api/fill", { mapping: state.mapping, row_index: +$("#row-sel").value || 0, delay_ms: +$("#delay").value });
    const rep = r.report;
    if (rep.blocked) { msg($("#fill-msg"), "No se rellenó: " + rep.blocked_reasons.join(" "), "error"); return; }
    msg($("#fill-msg"), rep.ok ? `Completado y verificado en ${rep.duration_ms} ms. Detenido antes del envío.`
      : "Terminado con incidencias: revise la tabla. Detenido antes del envío.", rep.ok ? "ok" : "aviso");
    $("#resultados").innerHTML = `<table><thead><tr><th>Campo</th><th>Método</th><th>Teclas</th><th>Esperado</th><th>Registrado</th><th>Estado</th><th>Detalle</th></tr></thead><tbody>
      ${rep.results.map((x) => `<tr><td>${esc(x.label || x.field_key)}${x.discovered_late ? ' <span class="chip">apareció al rellenar</span>' : ""}</td>
        <td>${esc(x.method)}</td><td>${x.keystrokes || ""}</td><td>${esc(x.expected)}</td><td>${esc(x.actual)}</td>
        <td><span class="estado ${esc(x.status)}">${esc(x.status)}</span></td><td><small>${esc(x.detail || x.validation_message || x.page_error)}</small></td></tr>`).join("")}
      </tbody></table>
      ${rep.confirmations.length ? `<h3>Confirmaciones</h3><ul>${rep.confirmations.map((c) => `<li class="${c.match ? "ok" : "error"}">${esc(c.label)}: ${esc(c.detail)}</li>`).join("")}</ul>` : ""}
      ${rep.submit_buttons.length ? `<p>Botón de envío detectado (no pulsado): <strong>${rep.submit_buttons.map(esc).join(", ")}</strong></p>` : ""}`;
    $("#captura").innerHTML = `<img alt="Captura del formulario rellenado" src="/api/screenshot?t=${Date.now()}">`;
  } catch (e) { msg($("#fill-msg"), e.message, "error"); }
});

$("#btn-enviar").addEventListener("click", async () => {
  try {
    const r = await postJSON("/api/submit", { phrase: $("#frase").value });
    msg($("#envio-msg"), r.submitted ? "Envío realizado por confirmación humana." : "No enviado.", "ok");
    $("#captura").innerHTML = `<img alt="Captura tras el envío" src="/api/screenshot?t=${Date.now()}">`;
  } catch (e) { msg($("#envio-msg"), e.message, "error"); }
});

$("#btn-audit").addEventListener("click", async () => {
  const ev = await api("/api/audit");
  $("#audit").textContent = ev.map((e) => JSON.stringify(e)).join("\n");
});

// ------------------------------------------------------------ inicio
(async function init() {
  const labs = await api("/api/lab");
  $("#lab-sel").innerHTML = labs.map((l) => `<option>${esc(l)}</option>`).join("");
  const st = await api("/api/status");
  $("#delay").value = st.typing_delay_ms;
  $("#hosts-info").textContent = st.allowed_hosts.length ? "Permitidos: " + st.allowed_hosts.join(", ") : "No hay dominios externos permitidos.";
  renderMapping();
  loadProfiles();
})();
