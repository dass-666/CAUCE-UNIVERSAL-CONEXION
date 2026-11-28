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

const TXT_ESTADO = { confirmada: "confirmada (acuse verificado)", incierta: "resultado desconocido", en_curso: "en curso",
  no_enviada: "no enviada", rechazada: "rechazada por el portal", revisada: "revisada por un operador" };

$("#btn-enviar").addEventListener("click", async () => {
  const b = $("#btn-enviar");
  b.disabled = true;                       // un doble clic no envía dos veces (el servidor también lo impide)
  msg($("#envio-msg"), "Enviando… no cierre el navegador. No se repetirá automáticamente.", "info");
  try {
    const r = await postJSON("/api/submit", { phrase: $("#frase").value });
    const op = r.operacion || {};
    const kind = op.estado === "confirmada" ? "ok" : op.estado === "incierta" ? "error" : "aviso";
    msg($("#envio-msg"), `Operación ${op.id}: ${TXT_ESTADO[op.estado] || op.estado}${op.folio ? " · folio " + op.folio : ""}. ${r.nota}`, kind);
    $("#frase").value = "";
    $("#captura").innerHTML = `<img alt="Captura tras el envío" src="/api/screenshot?t=${Date.now()}">`;
  } catch (e) { msg($("#envio-msg"), e.message, "error"); }
  finally { b.disabled = false; refrescarEstado(); refrescarOperaciones(); }
});

// ------------------------------------------------------------ estado del puesto y operaciones
const TXT_MOTOR = { conectado: ["Network Engine conectado", "ok"], no_disponible: ["Network Engine no disponible", "aviso"],
  no_configurado: ["Network Engine no configurado", "neutro"], credencial_invalida: ["Credencial de Network Engine no válida", "error"],
  credencial_incorrecta: ["Credencial de Network Engine incorrecta", "error"], limitado: ["Network Engine: espaciando latidos", "aviso"],
  sesion_ocupada: ["Network Engine: sesión ocupada", "error"], configuracion_invalida: ["Configuración de Network Engine no válida", "error"] };
const TXT_RED = { verificada: ["Conectividad verificada", "ok"], degradada: ["Conectividad degradada", "aviso"],
  perdida: ["Conectividad perdida", "error"], desconocida: ["Conectividad sin mediciones", "neutro"],
  desactualizada: ["Información de conectividad desactualizada", "aviso"], sin_diagnostico: ["Sin diagnóstico de red", "neutro"] };
const TXT_FASE = { iniciada: "Sesión iniciada", activa: "Sesión activa", perdida_conectividad: "Sesión sin conectividad",
  recuperada: "Sesión recuperada", requiere_intervencion: "Sesión: revisión humana necesaria", incierta: "Sesión incierta",
  interrumpida: "Sesión interrumpida (navegador)", cerrada: "Sesión cerrada" };
const TXT_MOTIVO = { operation_interrupted: "una operación se interrumpió con el resultado sin confirmar",
  engine_restart_during_operation: "Network Engine se reinició durante una operación", session_expired: "la sesión del portal caducó",
  previous_state_unreadable: "el estado anterior de Network Engine no se pudo leer", state_recovered_from_backup: "Network Engine usó su copia de respaldo" };

function ind(el, [texto, kind]) { el.textContent = texto; el.className = "ind " + kind; }

async function refrescarEstado() {
  let d;
  try { d = await api("/api/diagnostico"); } catch (_) { return; }
  ind($("#ind-motor"), TXT_MOTOR[d.motor] || [d.motor, "neutro"]);
  ind($("#ind-red"), TXT_RED[d.conectividad] || [d.conectividad, "neutro"]);
  const fase = d.fase ? [TXT_FASE[d.fase] || d.fase, d.fase === "activa" || d.fase === "recuperada" ? "ok"
    : d.fase === "iniciada" ? "neutro" : d.fase === "requiere_intervencion" || d.fase === "interrumpida" ? "error" : "aviso"]
    : ["Sesión: sin datos del motor", "neutro"];
  ind($("#ind-sesion"), fase);
  ind($("#ind-op"), d.operacion === "in_progress" ? ["Operación en curso", "aviso"]
    : d.operaciones_abiertas ? [`Resultado desconocido: ${d.operaciones_abiertas}`, "error"] : ["Operación: ninguna pendiente", "neutro"]);
  $("#estado-texto").textContent = d.motor_texto + (d.etiqueta ? ` Puesto: ${d.etiqueta}.` : "") +
    (d.ultima_respuesta_s != null && d.motor === "conectado" ? ` Última respuesta hace ${Math.round(d.ultima_respuesta_s)} s.` : "");
  // Una sola alerta de revisión, con sus motivos (sin alarmas repetidas ni contradictorias)
  const motivos = [];
  if (d.revision_general) motivos.push(`El registro de operaciones necesita revisión de un operador (${d.revision_general.motivo}). No se envía nada hasta entonces.`);
  if (d.operaciones_abiertas) motivos.push(`${d.operaciones_abiertas} operación(es) con resultado desconocido en este puesto: compruebe el acuse o pida la revisión.`);
  if (d.requiere_persona) motivos.push(`Network Engine pide revisión humana: ${TXT_MOTIVO[d.motivo] || d.motivo || "motivo no indicado"}. La resuelve un operador autorizado (centro de revisiones), no este puesto.`);
  const rv = $("#revision");
  rv.hidden = !motivos.length;
  rv.innerHTML = motivos.length ? `<strong>Revisión humana necesaria</strong><ul>${motivos.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : "";
}

async function refrescarOperaciones() {
  let r;
  try { r = await api("/api/operaciones?limit=10"); } catch (_) { return; }
  if (!r.operaciones.length) { $("#operaciones").innerHTML = '<p class="ayuda">Sin operaciones todavía.</p>'; return; }
  $("#operaciones").innerHTML = `<table><thead><tr><th>Operación</th><th>Estado</th><th>Motivo</th><th>Folio</th><th>Hora (UTC)</th><th></th></tr></thead><tbody>
    ${r.operaciones.map((o) => `<tr><td><code>${esc(o.id)}</code></td><td><span class="estado ${esc(o.estado)}">${esc(TXT_ESTADO[o.estado] || o.estado)}</span></td>
      <td><small>${esc(o.motivo || "")}</small></td><td>${esc(o.folio || "")}</td><td><small>${esc((o.actualizada || "").slice(11, 19))}</small></td>
      <td>${o.estado === "incierta" && o.verificable ? `<button class="verificar" data-op="${esc(o.id)}">Comprobar acuse</button>` : ""}</td></tr>`).join("")}
    </tbody></table>`;
  document.querySelectorAll("button.verificar").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const v = await postJSON(`/api/operaciones/${b.dataset.op}/verificar`, {});
      msg($("#envio-msg"), v.cambio ? `Acuse verificado: ${v.operacion.folio}.` : "El portal no tiene constancia todavía: sigue pendiente de revisión (no se reenvía).", v.cambio ? "ok" : "aviso");
    } catch (e) { msg($("#envio-msg"), e.message, "error"); }
    refrescarEstado(); refrescarOperaciones();
  }));
}

$("#btn-cerrar").addEventListener("click", async () => {
  try {
    await postJSON("/api/close", {});
    state.analysis = null; state.proposals = []; state.columns = []; state.mapping = {};
    ["#campos", "#datos-preview", "#resultados", "#captura"].forEach((s) => ($(s).innerHTML = ""));
    $("#row-sel").innerHTML = ""; $("#data-file").value = "";
    renderMapping();
    msg($("#cerrar-msg"), "Navegador cerrado y datos descartados de la memoria.", "ok");
  } catch (e) { msg($("#cerrar-msg"), e.message, "error"); }
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
  refrescarEstado(); refrescarOperaciones();
  setInterval(refrescarEstado, 3000);
  setInterval(refrescarOperaciones, 10000);
})();
