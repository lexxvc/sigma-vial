// ============================================================
// SIGMA-Vial · Consola de supervisión táctica (frontend)
// Una sola página con 4 vistas (Fase 4). Habla con la API FastAPI:
//   REST para lanzar/consultar/decidir y SSE para el avance del Crew en vivo.
// ============================================================
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { sis: null, motor: "stub", escenario: "pico_matutino", ciclo: null, eventos: [], filtro: "todo",
            res: null, bit: null, sse: null, red: null, sim: null, nodo: "I3" };
const NOMBRES_AG = { ANALISTA: "Analista", INGENIERO: "Ingeniero", AUDITOR: "Auditor", SINTETIZADOR: "Sintetizador",
                     VALIDADOR: "Validador", PUBLICADOR: "Publicador", SISTEMA: "Sistema", OPERADOR: "Operador", VERIFICACION: "Verificación" };

const guardar = (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} };
const leerLS = k => { try { return localStorage.getItem(k); } catch (_) { return null; } };
const operador = () => $("operador").value.trim() || "Operador en turno";

async function api(ruta, opciones = {}) {
  const r = await fetch(ruta, { headers: { "Content-Type": "application/json" }, ...opciones });
  let cuerpo = null;
  try { cuerpo = await r.json(); } catch (_) {}
  if (!r.ok) { const e = new Error((cuerpo && (cuerpo.detail?.mensaje || cuerpo.detail)) || r.statusText); e.status = r.status; e.cuerpo = cuerpo; throw e; }
  return cuerpo;
}
function toast(txt, tipo = "ok") {
  const t = $("toast");
  t.className = `fixed bottom-5 right-5 z-50 rounded-lg px-4 py-3 text-sm shadow-lg ${tipo === "ok" ? "bg-emerald-700 text-white" : tipo === "error" ? "bg-red-700 text-white" : "bg-slate-800 text-white"}`;
  t.textContent = txt; clearTimeout(t._t); t._t = setTimeout(() => t.classList.add("hidden"), 4500);
}
function modal(titulo, html, botones = []) {
  $("mTit").textContent = titulo; $("mCuerpo").innerHTML = html; $("mPie").innerHTML = "";
  for (const b of botones) {
    const el = document.createElement("button"); el.className = b.cls || "btn-ghost"; el.textContent = b.txt;
    el.onclick = b.fn || cerrarModal; $("mPie").appendChild(el);
  }
  $("modal").classList.remove("hidden");
}
const cerrarModal = () => $("modal").classList.add("hidden");
$("mCerrar").onclick = cerrarModal;

// ---------------- navegación ----------------
function vista() {
  const v = (location.hash || "#configuracion").slice(1);
  document.querySelectorAll(".vista").forEach(s => s.classList.toggle("hidden", s.id !== "v-" + v));
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("sel", a.dataset.nav === v));
  if (v === "resultados") cargarResultados();
  if (v === "mapa") { iniciarMapa(); cargarBitacora(); }
  return v;
}
window.addEventListener("hashchange", vista);

// ---------------- vista 1: configuración ----------------
async function cargarSistema() {
  S.sis = await api("/api/sistema");
  const s = S.sis;
  for (const sel of [$("selEsc"), $("selEscTop")]) {
    sel.innerHTML = Object.entries(s.escenarios).map(([k, e]) => `<option value="${k}">${esc(e.nombre)} (${esc(e.franja)})</option>`).join("");
    sel.value = S.escenario;
  }
  if (!leerLS("motor")) S.motor = s.motor_defecto;
  pintarEscenario(); pintarMotores(); pintarFuentes(); pintarReglas();
  $("interObj").innerHTML = Object.entries(s.intersecciones).map(([k, n]) =>
    `<div class="tile"><div class="h-2 w-2 rounded-full bg-emerald-500 mx-auto mb-1"></div><b>${k}</b><div class="text-slate-500">${esc(n)}</div></div>`).join("");
  if (s.ciclo_actual && !S.ciclo) { S.ciclo = s.ciclo_actual; conectar(S.ciclo.id); }
}
function pintarEscenario() {
  const e = S.sis.escenarios[S.escenario];
  $("perfilChip").textContent = "Perfil: " + S.escenario;
  $("perfilTit").textContent = "Perfil: " + e.nombre;
  $("perfilTxt").textContent = e.perfil; $("franja").textContent = e.franja;
  $("selEsc").value = $("selEscTop").value = S.escenario;
}
function pintarMotores() {
  const m = S.sis.motores;
  const ops = [
    ["gemini", "Gemini 2.5 Flash", m.gemini ? "Razonamiento real · clave configurada" : "Falta GEMINI_API_KEY en el servidor", m.gemini],
    ["stub", "Guion fijo (respaldo)", "Reproducible y sin costo. No demuestra razonamiento", true],
    ["ollama", "Ollama qwen2.5:7b", m.ollama ? "Modelo local" : "Requiere OLLAMA_HOST", m.ollama],
  ];
  if (!ops.find(o => o[0] === S.motor && o[3])) S.motor = m.gemini ? "gemini" : "stub";
  $("motores").innerHTML = ops.map(([k, t, d, ok]) =>
    `<button data-motor="${k}" class="motor ${S.motor === k ? "sel" : ""}" ${ok ? "" : "disabled"}><b class="block text-[13px]">${t}</b><span class="text-slate-500">${d}</span></button>`).join("");
  document.querySelectorAll("[data-motor]").forEach(b => b.onclick = () => { S.motor = b.dataset.motor; guardar("motor", S.motor); pintarMotores(); });
  $("chipMotor").textContent = "Motor: " + ({ gemini: "Gemini", stub: "guion fijo", ollama: "Ollama" }[S.motor]);
  $("chipMotor").className = "chip " + (S.motor === "stub" ? "bg-amber-50 text-amber-700" : "bg-violet-50 text-violet-700");
}
function pintarFuentes() {
  const f = S.sis.fuentes, ok = f.filter(x => x.ok).length;
  $("fuentes").innerHTML = f.map(x => `
    <div class="flex items-center gap-3 rounded-lg border ${x.ok ? "border-slate-200 bg-white" : "border-red-200 bg-red-50"} p-3">
      <div class="h-10 w-10 rounded-lg grid place-items-center ${x.ok ? "bg-navy-800 text-white" : "bg-red-600 text-white"}">${x.ok ? "▦" : "!"}</div>
      <div class="flex-1 min-w-0"><div class="flex items-center gap-2"><b class="${x.ok ? "" : "text-red-800"}">${esc(x.archivo)}</b>
        <span class="chip ${x.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-100 text-red-700"}">${x.ok ? "✓ Validado" : "⊗ Formato no reconocido"}</span></div>
        <div class="text-xs ${x.ok ? "text-slate-500" : "text-red-700"}">${x.kb ? x.kb + " KB · " : ""}${esc(x.detalle)}</div></div></div>`).join("");
  $("chipFuentes").textContent = `${ok} de ${f.length} verificadas`;
  $("chipFuentes").className = "chip " + (S.sis.listo ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700");
  const corriendo = S.ciclo && S.ciclo.estado === "en_ejecucion";
  $("btnIniciar").disabled = !S.sis.listo || corriendo;
  $("estadoListo").textContent = corriendo ? "CICLO EN CURSO" : S.sis.listo ? "LISTO PARA INICIAR" : "BLOQUEADO";
  $("estadoListo").className = "font-bold text-sm " + (S.sis.listo ? "text-emerald-700" : "text-red-700");
  $("iconListo").className = "h-10 w-10 rounded-full grid place-items-center text-lg " + (S.sis.listo ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700");
  $("iconListo").textContent = S.sis.listo ? "✓" : "!";
  $("msgListo").textContent = `${ok} de ${f.length} fuentes verificadas · reglas duras bloqueadas en modo solo lectura`;
  $("msgListo2").textContent = corriendo ? "Hay un ciclo en ejecución: síguelo en la vista Ejecución."
    : S.sis.listo ? "Todo listo. El botón lanza crew.kickoff(inputs={escenario}) en un trabajador aparte."
    : "El inicio está deshabilitado hasta que todas las fuentes validen. Corrige o restablece el archivo con error.";
}
function pintarReglas() {
  const n = S.sis.normativa || {};
  const valor = { R1: `${n.verde_veh_min_s}–${n.verde_veh_max_s} s`, R2: `${n.ciclo_max_s} s`, R3: `${n.velocidad_cruce_peatonal_m_s} m/s`,
                  R4: `${n.ambar_s} + ${n.todo_rojo_s} s`, R5: `${n.espera_max_cruce_peatonal_s} s`, R0: "4 nodos", R6: `${n.espera_max_carril_s} s` };
  const fila = r => `<div class="regla"><div class="ic">${r.regla}</div><div class="flex-1"><b class="text-[13px]">${r.regla} · ${esc(r.nombre)}</b>
      <div class="text-xs text-slate-500">${esc(r.descripcion)}</div></div><div class="text-right"><b>${valor[r.regla] || ""}</b>
      <div class="text-[10px] text-slate-500">${r.grupo === "duras" ? "Regla dura" : "Complementaria"}</div></div></div>`;
  $("reglas").innerHTML = S.sis.reglas.filter(r => r.grupo === "duras").map(fila).join("");
  $("reglasC").innerHTML = S.sis.reglas.filter(r => r.grupo !== "duras").map(fila).join("");
}
$("selEsc").onchange = $("selEscTop").onchange = e => { S.escenario = e.target.value; pintarEscenario(); };
$("operador").value = leerLS("operador") || "";
$("operador").oninput = e => { guardar("operador", e.target.value); $("avatar").textContent = (e.target.value || "OP").split(" ").map(x => x[0]).join("").slice(0, 2).toUpperCase(); };
$("operador").oninput({ target: $("operador") });

async function subir(nombre, contenido) {
  try { const r = await api("/api/fuentes", { method: "POST", body: JSON.stringify({ nombre, contenido }) });
    toast(r.ok ? `${nombre} validado` : `${nombre}: formato no reconocido`, r.ok ? "ok" : "error"); }
  catch (e) { toast(e.message, "error"); }
  await cargarSistema();
}
$("archivo").onchange = e => { const f = e.target.files[0]; if (!f) return; f.text().then(t => subir(f.name, t)); e.target.value = ""; };
$("drop").ondragover = e => { e.preventDefault(); };
$("drop").ondrop = e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) f.text().then(t => subir(f.name, t)); };
$("btnInvalido").onclick = async () => subir("aforo_campo.csv", await (await fetch("/web/datos_prueba/aforo_campo.csv")).text());
$("btnRestablecer").onclick = async () => { await api("/api/fuentes/restablecer", { method: "POST" }); await cargarSistema(); toast("Fuentes restablecidas"); };

$("btnIniciar").onclick = async () => {
  try {
    const r = await api("/api/ciclos", { method: "POST", body: JSON.stringify({ escenario: S.escenario, motor: S.motor }) });
    S.ciclo = { id: r.ciclo_id, numero: r.numero, estado: "en_ejecucion", tareas: [], hechas: 0 }; S.eventos = []; S.res = null;
    conectar(r.ciclo_id); location.hash = "#ejecucion";
  } catch (e) { toast(e.message, "error"); }
};

// ---------------- vista 2: ejecución (SSE) ----------------
function conectar(id) {
  if (S.sse) S.sse.close();
  S.eventos = [];
  $("log").innerHTML = "";
  const es = new EventSource(`/api/ciclos/${id}/eventos`);
  S.sse = es;
  es.onmessage = m => { const ev = JSON.parse(m.data); S.eventos.push(ev); agregarLinea(ev); };
  es.addEventListener("estado", m => { S.ciclo = JSON.parse(m.data); pintarEjecucion(); });
  es.addEventListener("fin", () => { es.close(); S.sse = null; pintarEjecucion(); cargarSistema();
    if (S.ciclo.estado === "terminado") toast("Ciclo concluido. Revisa los resultados y autoriza.");
    else if (S.ciclo.estado === "error") toast("El ciclo terminó con error", "error"); });
  es.onerror = () => { if (S.ciclo && S.ciclo.estado !== "en_ejecucion") es.close(); };
}
const CLASE = { veto: "veto", hallazgo: "veto", aprobado: "ok", publicado: "ok", listo: "ok", razonamiento: "razon", resultado: "res", error: "veto" };
const GRUPO = { herramienta: "herr", resultado: "herr", delegacion: "herr", razonamiento: "razon", veto: "veto", hallazgo: "veto", error: "veto" };
function lineaHTML(ev) {
  const marca = ev.tipo === "veto" ? ' <b class="text-red-400">[VETO]</b>' : ev.tipo === "delegacion" ? ' <b class="text-sky-300">[DELEGA]</b>' : "";
  const hora = new Date(ev.t * 1000).toLocaleTimeString("es-MX", { hour12: false });
  return `<div class="ln ${CLASE[ev.tipo] || ""}" data-g="${GRUPO[ev.tipo] || "otro"}"><span class="t">[${hora}]</span> <b class="tag-${ev.agente}">[${esc(ev.agente)}]</b>${marca} ${esc(ev.texto)}</div>`;
}
function agregarLinea(ev) {
  const log = $("log"), abajo = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  log.insertAdjacentHTML("beforeend", lineaHTML(ev));
  aplicarFiltro(log.lastElementChild);
  if (abajo) log.scrollTop = log.scrollHeight;
  if (["veto", "hallazgo", "aprobado", "publicado"].includes(ev.tipo)) pintarEjecucion();
}
function aplicarFiltro(el) { el.classList.toggle("hidden", S.filtro !== "todo" && el.dataset.g !== S.filtro); }
document.querySelectorAll("[data-f]").forEach(b => b.onclick = () => {
  S.filtro = b.dataset.f; document.querySelectorAll("[data-f]").forEach(x => x.classList.toggle("sel", x === b));
  $("log").querySelectorAll(".ln").forEach(aplicarFiltro);
});
const ESTADO_T = { pendiente: ["En espera", "bg-slate-100 text-slate-500"], en_curso: ["En curso…", "bg-emerald-100 text-emerald-700 pulso"],
                   completada: ["✓ Completada", "bg-emerald-50 text-emerald-700"], veto: ["⛨ Veto emitido", "bg-red-600 text-white"], error: ["Error", "bg-red-100 text-red-700"] };
function pintarEjecucion() {
  const c = S.ciclo;
  if (!c) return;
  $("eNum").textContent = "#" + c.numero;
  const pct = Math.round((c.hechas || 0) / 6 * 100);
  $("eBar").style.width = pct + "%";
  $("eProg").textContent = `${pct} % completado · ${Math.round(c.segundos || 0)} s transcurridos`;
  const en = (c.tareas || []).findIndex(t => t.estado === "en_curso");
  const chip = { en_ejecucion: [`● Ejecutando · tarea ${en + 1 || "…"} de 6`, "bg-emerald-50 text-emerald-700"], terminado: ["✓ Concluido", "bg-emerald-600 text-white"],
                 error: ["Error", "bg-red-100 text-red-700"], detenido: ["Detenido por el operador", "bg-slate-200"] }[c.estado] || ["—", ""];
  $("eChip").textContent = chip[0]; $("eChip").className = "chip " + chip[1];
  $("btnVerRes").disabled = c.estado !== "terminado"; $("btnIrRes").classList.toggle("hidden", c.estado !== "terminado");
  $("btnDetener").disabled = c.estado !== "en_ejecucion";
  $("eError").classList.toggle("hidden", !c.error); $("eError").textContent = c.error ? "Error: " + c.error : "";
  const vetos = S.eventos.filter(e => e.tipo === "veto" && e.datos && e.datos.violaciones);
  const hall = S.eventos.find(e => e.tipo === "hallazgo");
  $("pipeline").innerHTML = (c.tareas || []).map((t, i) => {
    const [txt, cls] = ESTADO_T[t.estado] || ESTADO_T.pendiente;
    let extra = "";
    if (i === 2 && (t.estado === "veto" || vetos.length)) {
      const v = vetos.find(x => x.datos.archivo === "propuesta_v1") || vetos[0];
      const r3 = hall ? hall.datos.violaciones.find(x => x.includes("(R3")) : null;
      if (v) extra = `<div class="mt-3 rounded-lg bg-red-50 border border-red-200 p-3 text-xs text-red-800">
        <b>⚠ Violación de reglas duras detectada</b>
        ${r3 ? `<div class="mt-1">La configuración vigente v1.0 ya incumple: ${esc(r3)}.</div>` : ""}
        <div class="mt-1">La propuesta ${esc(v.datos.archivo)} incumple:</div>
        <ul class="list-disc ml-4">${v.datos.violaciones.slice(0, 5).map(x => `<li>${esc(x)}</li>`).join("")}${v.datos.violaciones.length > 5 ? `<li>… y ${v.datos.violaciones.length - 5} más</li>` : ""}</ul>
        <div class="mt-1 font-semibold">No se autoriza excepción por desempeño. Se devuelve al Ingeniero.</div></div>`;
    }
    return `<div class="tarea ${t.estado}"><div class="flex items-center justify-between gap-2"><b class="text-sm">Tarea ${t.n} · ${esc(t.titulo)}</b><span class="chip ${cls}">${txt}</span></div>
      <div class="flex gap-3 mt-2 items-start"><span class="agente shrink-0">${esc(t.agente)}</span>
      <span class="text-xs text-slate-600">${t.resumen ? esc(t.resumen) + ' <i class="text-violet-600">· salida del LLM</i>' : ""}</span></div>${extra}</div>`;
  }).join("");
  const inter = S.sis ? S.sis.intersecciones : {};
  const estadoNodo = k => {
    if (hall && hall.datos.violaciones.some(x => x.startsWith(k + ":") && x.includes("(R3"))) return c.hechas >= 4 ? ["R3 corregida", "bg-red-50 text-red-700"] : ["R3 incumplida en v1.0", "bg-red-50 text-red-700"];
    if (vetos.some(v => v.datos.violaciones.some(x => x.startsWith(k + ":")))) return c.hechas >= 4 ? ["Corregida en v2", "bg-amber-50 text-amber-700"] : ["Con observación", "bg-amber-50 text-amber-700"];
    return c.hechas >= 5 ? ["Cumple", "bg-emerald-50 text-emerald-700"] : ["En espera", "bg-slate-50 text-slate-500"];
  };
  $("eInter").innerHTML = Object.entries(inter).map(([k, n]) => { const [t, cl] = estadoNodo(k);
    return `<div class="rounded-lg p-2 ${cl}"><b>${k} ${esc(n)}</b><div>${t}</div></div>`; }).join("");
  pintarFuentes();
}
$("btnDetener").onclick = () => S.ciclo && api(`/api/ciclos/${S.ciclo.id}/detener`, { method: "POST" });
$("btnVerRes").onclick = $("btnIrRes").onclick = () => { location.hash = "#resultados"; };

// ---------------- vista 3: resultados y autorización ----------------
async function cargarResultados() {
  const c = S.ciclo;
  if (!c || c.estado !== "terminado") { $("rVacio").classList.remove("hidden"); $("rCont").classList.add("hidden");
    $("rVacio").innerHTML = c && c.estado === "en_ejecucion" ? `El ciclo #${c.numero} sigue en ejecución. <a href="#ejecucion" class="text-inst underline">Ver avance</a>.`
      : `Aún no hay resultados. Inicia un ciclo en <a href="#configuracion" class="text-inst underline">Configuración</a>.`; return; }
  try { S.res = await api(`/api/ciclos/${c.id}/resultados`); } catch (e) { toast(e.message, "error"); return; }
  pintarResultados();
}
function pintarResultados() {
  const r = S.res; $("rVacio").classList.add("hidden"); $("rCont").classList.remove("hidden");
  const dec = r.ciclo.decision;
  $("rNum").textContent = "#" + r.ciclo.numero;
  $("rHuella").textContent = `${r.version} · huella ${r.huella}`;
  $("rBanTit").textContent = dec?.accion === "autorizada" ? `Publicada por ${dec.operador}` : dec?.accion === "rechazada" ? `Rechazada por ${dec.operador}` : "Configuración lista para publicarse";
  $("rBanTxt").textContent = dec?.accion === "autorizada" ? `configuracion_liberada.json escrito el ${dec.fecha}. Aplícala al simulador en la vista Mapa.`
    : dec?.accion === "rechazada" ? `Motivo: ${dec.motivo || "—"}. Lanza un ciclo nuevo desde Configuración.`
    : `Plan propuesto ${r.version}${r.ajustada ? " (ajustado por el operador y revalidado)" : ""} aprobado contra R0–R6. Pendiente de autorización del operador.`;
  $("rBanner").className = "rounded-xl p-4 flex items-center gap-3 border " + (dec?.accion === "rechazada" ? "bg-red-50 border-red-200" : "bg-emerald-100 border-emerald-200");
  ["btnAprobar", "btnAprobar2", "btnAjustar", "btnRechazar"].forEach(b => $(b).disabled = !!dec);
  $("btnReporte").href = `/api/ciclos/${r.ciclo.id}/reporte`;
  const r3 = r.hallazgo_vigente.r3[0];
  $("rHallTxt").innerHTML = r3 ? `<b>Aviso:</b> ${esc(r3)}. En total la configuración vigente v1.0 tiene ${r.hallazgo_vigente.violaciones.length} violaciones (R3–R6). La propuesta ${esc(r.version)} las corrige.` : "La configuración vigente no presenta violaciones de despeje peatonal.";
  $("rHallazgo").classList.toggle("hidden", !r.hallazgo_vigente.violaciones.length);
  const k = r.kpi, pctv = (a, b) => b ? Math.round((a - b) / b * 100) : 0;
  const kp = (lbl, val, unid, chip, chipCls, pie) => `<div class="card"><div class="flex justify-between gap-2"><div class="lbl">${lbl}</div><span class="chip ${chipCls}">${chip}</span></div>
     <div class="mt-2"><span class="text-4xl font-extrabold text-slate-900">${val}</span> <span class="text-slate-500">${unid}</span></div><div class="text-xs text-slate-500 mt-2">${pie}</div></div>`;
  $("rKpis").innerHTML =
    kp("Espera peatonal media (v1.0)", k.espera_peat_sigma, "s", `${pctv(k.espera_peat_sigma, k.espera_peat_fijo)} % vs plan fijo`, "bg-orange-50 text-orange-700", "Promedio del circuito; oculta el +64 % de I3 (ver tabla)") +
    kp("Spillback (v1.0)", k.spill_sigma, "ev/h", `${pctv(k.spill_sigma, k.spill_fijo)} % vs plan fijo`, "bg-emerald-50 text-emerald-700", "Tramo corto I2→I3, 14 vehículos de capacidad") +
    kp("Ciclo común propuesto", k.ciclo_comun ?? "—", "s", "Habilita coordinación", "bg-inst-50 text-inst", `Antes: ${k.ciclos_antes.join("–")} s · desfases ${k.desfases.join(" / ")} s`) +
    kp("Reglas duras cumplidas", `${r.duras_cumplidas}`, "/ 5", r.dictamen.veredicto, r.dictamen.veredicto === "APROBADO" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700", "Verificación determinista, en Python");
  const p = r.reparto_i3, seg = (v, c, t) => `<div style="flex:${v}" class="${c} grid place-items-center">${t} · ${v} s</div>`;
  $("rReparto").innerHTML = seg(p.principal, "bg-emerald-700", "Verde principal") + seg(p.secundario, "bg-navy-700", "Verde secundario") + seg(p.peatonal, "bg-sky-600", "Fase peatonal") + seg(p.perdidas, "bg-amber-400 text-slate-900", "Ámbar + todo-rojo");
  $("rRepLeg").innerHTML = `<span>Ciclo ${p.ciclo} s</span><span>Despeje mínimo I3: 14.0 s</span>`;
  const varTxt = x => x && x.variacion_pct != null ? `<b class="${x.variacion_pct > 0 ? "text-red-600" : "text-emerald-700"}">${x.variacion_pct > 0 ? "+" : ""}${x.variacion_pct} %</b><div class="text-[11px] text-slate-500">${x.media_sigma} s vs ${x.media_base} s · p=${x.p_wilcoxon}</div>` : "—";
  $("rTabla").innerHTML = r.comparativa.map(f => `<tr class="${f.reglas_vigente.includes("R3") ? "bg-orange-50/60" : ""}">
    <td class="td"><b>${f.id} · ${esc(f.nombre)}</b><div class="text-xs text-slate-500">cruce de ${f.ancho_cruce_m} m</div></td>
    <td class="td text-slate-600">${esc(f.vigente)}</td><td class="td font-semibold">${esc(f.propuesto)}</td>
    <td class="td text-right">${varTxt(f.peat)}</td><td class="td text-right">${varTxt(f.veh)}</td>
    <td class="td">${f.reglas_vigente.length ? `<span class="chip bg-red-50 text-red-700">incumple ${f.reglas_vigente.join(", ")}</span>` : '<span class="chip bg-emerald-50 text-emerald-700">cumple</span>'}</td></tr>`).join("");
  $("rReglasChip").textContent = `${r.por_regla.filter(x => x.cumple).length}/7 CUMPLE`;
  $("rReglasChip").className = "chip " + (r.dictamen.veredicto === "APROBADO" ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700");
  const vetoR = new Set(S.eventos.filter(e => e.tipo === "veto" && e.datos?.violaciones).flatMap(e => e.datos.violaciones.map(v => (v.match(/\((R\d)/) || [])[1])));
  $("rReglas").innerHTML = r.por_regla.map(x => `<div class="regla ${x.cumple ? "" : "!bg-red-50 !border-red-200"}">
      <div class="ic ${x.cumple ? "!bg-emerald-50 !text-emerald-700" : "!bg-red-100 !text-red-700"}">${x.cumple ? "✓" : "✗"}</div>
      <div class="flex-1"><b class="text-[13px]">${x.regla}: ${esc(x.nombre)}</b> ${vetoR.has(x.regla) ? '<span class="chip bg-slate-900 text-white">vetó v1</span>' : ""}
      <div class="text-xs text-slate-500">${esc(x.descripcion)}${x.grupo !== "duras" ? " · complementaria" : ""}</div>
      ${x.violaciones.map(v => `<div class="text-xs text-red-700">${esc(v)}</div>`).join("")}</div>
      <span class="text-[11px] font-bold ${x.cumple ? "text-emerald-700" : "text-red-700"}">${x.cumple ? "CUMPLE" : "INCUMPLE"}</span></div>`).join("") +
    `<div class="text-xs text-slate-500 flex justify-between pt-2"><span>🔒 Triple barrera: Auditor · publicador · servidor</span><code>configuracion_aprobada.json</code></div>`;
  $("rJust").innerHTML = Object.entries(r.justificacion || {}).map(([a, b]) => `<div><dt class="font-semibold text-slate-800">${esc(a)}</dt><dd class="text-slate-600">${esc(b)}</dd></div>`).join("") || "<i>Sin justificación</i>";
  $("rPubTxt").textContent = r.liberada ? `Hoy está liberada ${r.liberada.version} (por ${r.liberada.liberacion?.por || "—"}, ${r.liberada.liberacion?.fecha || ""}).` : "Aún no hay configuración liberada. Los cuatro controladores tomarán la que autorices en su siguiente ciclo de decisión.";
}

function autorizarModal() {
  const r = S.res;
  modal("Autorizar publicación a la capa de tiempo real", `
    <div class="rounded-lg bg-amber-50 border border-amber-200 p-3 text-sm text-amber-900"><b>Acción irreversible sobre infraestructura física.</b> Los controladores de las 4 intersecciones aplicarán esta configuración en su siguiente ciclo de decisión.</div>
    <div class="grid grid-cols-2 gap-3 mt-4 text-sm">
      <div class="tile"><div class="lbl">Versión revisada</div><b>${esc(r.version)}</b></div>
      <div class="tile"><div class="lbl">Huella (lo que viste en pantalla)</div><b class="font-mono">${r.huella}</b></div>
      <div class="tile"><div class="lbl">Dictamen del validador</div><b class="text-emerald-700">${r.dictamen.veredicto} · ${r.duras_cumplidas}/5 reglas duras</b></div>
      <div class="tile"><div class="lbl">Operador en turno</div><b>${esc(operador())}</b></div></div>
    <label class="flex gap-2 mt-4 text-sm"><input type="checkbox" id="chkConf"> Revisé el diagnóstico, el dictamen R0–R6 y el costo asumido (menos verde vehicular principal).</label>
    <p class="text-xs text-slate-500 mt-3">El servidor vuelve a validar la propuesta (tercera barrera) y rechaza la autorización si la versión cambió desde que la revisaste (HTTP 409).</p>`,
    [{ txt: "Cancelar" }, { txt: "Autorizar y publicar", cls: "btn-ok", fn: async () => {
      if (!$("chkConf").checked) { toast("Marca la casilla de revisión", "error"); return; }
      try {
        const p = await api(`/api/ciclos/${S.ciclo.id}/autorizar`, { method: "POST", body: JSON.stringify({ version_revisada: r.huella, operador: operador() }) });
        modal("Configuración publicada", `<div class="text-center py-4"><div class="h-14 w-14 rounded-full bg-emerald-600 text-white grid place-items-center text-2xl mx-auto">✓</div>
          <p class="mt-3 font-semibold">${esc(p.version)} escrita en <code>${p.archivo}</code></p><p class="text-sm text-slate-500">Huella ${p.huella} · ${p.fecha} · autorizó ${esc(operador())}</p></div>`,
          [{ txt: "Cerrar" }, { txt: "Aplicar ahora en el simulador del campus →", cls: "btn-dark", fn: async () => {
            cerrarModal(); location.hash = "#mapa"; setTimeout(() => simCmd({ accion: "plan", nombre: "supervision" }), 400); simCmd({ accion: "play" }); } }]);
        S.ciclo.decision = { accion: "autorizada" }; await cargarResultados();
      } catch (e) {
        const v = e.cuerpo?.detail?.violaciones;
        toast(e.status === 409 ? "La propuesta cambió desde que la revisaste. Recarga los resultados." : e.message, "error");
        if (v) modal("La autorización fue rechazada por el servidor", `<ul class="list-disc ml-5 text-sm text-red-700">${v.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`, [{ txt: "Cerrar" }]);
      } } }]);
}
$("btnAprobar").onclick = $("btnAprobar2").onclick = autorizarModal;
$("btnRechazar").onclick = () => modal("Rechazar y solicitar corrección", `<label class="lbl">Motivo (queda en la bitácora)</label>
    <textarea id="motivo" class="input mt-1 h-24" placeholder="Ej.: el aumento de espera vehicular en I1 no es aceptable en semana de exámenes"></textarea>`,
  [{ txt: "Cancelar" }, { txt: "Rechazar", cls: "btn-danger", fn: async () => {
    await api(`/api/ciclos/${S.ciclo.id}/rechazar`, { method: "POST", body: JSON.stringify({ motivo: $("motivo").value, operador: operador() }) });
    cerrarModal(); toast("Propuesta rechazada y registrada", "info"); S.ciclo.decision = { accion: "rechazada" }; cargarResultados(); } }]);
$("btnAjustar").onclick = () => {
  const planes = S.res.configuracion.planes_por_interseccion, campos = ["verde_principal_s", "verde_secundario_s", "verde_peatonal_s", "ciclo_s"];
  modal("Ajustar la propuesta", `<p class="text-sm text-slate-600 mb-3">Modifica los tiempos. Al guardar, <code>validador_reglas_duras</code> decide si el ajuste es aceptable; el operador no puede saltarse una regla dura.</p>
    <table class="w-full text-sm"><thead class="text-[11px] uppercase text-slate-500"><tr><th class="th">Nodo</th><th class="th">Verde principal</th><th class="th">Secundario</th><th class="th">Peatonal</th><th class="th">Ciclo</th></tr></thead><tbody>
    ${Object.entries(planes).map(([k, p]) => `<tr><td class="td font-semibold">${k}</td>${campos.map(c => `<td class="td"><input type="number" data-k="${k}" data-c="${c}" value="${p[c]}" class="input !py-1 w-20"></td>`).join("")}</tr>`).join("")}
    </tbody></table><div id="ajErr" class="mt-3 text-sm"></div><p class="text-xs text-slate-500 mt-2">Prueba: baja el verde peatonal de I3 a 10 s y verás el veto por R3.</p>`,
    [{ txt: "Cancelar" }, { txt: "Validar y guardar ajuste", cls: "btn-dark", fn: async () => {
      const nuevo = {};
      document.querySelectorAll("[data-k]").forEach(i => { (nuevo[i.dataset.k] ||= {})[i.dataset.c] = +i.value; });
      try { const r = await api(`/api/ciclos/${S.ciclo.id}/ajustar`, { method: "POST", body: JSON.stringify({ planes_por_interseccion: nuevo, operador: operador() }) });
        cerrarModal(); toast(`Ajuste aprobado por el validador · nueva huella ${r.huella}`); cargarResultados(); }
      catch (e) { const v = e.cuerpo?.detail?.violaciones || [e.message];
        $("ajErr").innerHTML = `<div class="rounded-lg bg-red-50 border border-red-200 p-3 text-red-800"><b>⛨ Veto del validador</b><ul class="list-disc ml-5">${v.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>`; } } }]);
};

// ---------------- vista 4: mapa en vivo y auditoría ----------------
const simCmd = d => api("/api/sim/cmd", { method: "POST", body: JSON.stringify(d) }).catch(e => toast(e.message, "error"));
let mapaIniciado = false, accPx = {}, interPx = {};
async function iniciarMapa() {
  if (mapaIniciado) return;
  try { S.red = await api("/api/sim/red"); } catch (e) { $("simAviso").textContent = e.message; setTimeout(iniciarMapa, 2500); return; }
  mapaIniciado = true; $("simAviso").classList.add("hidden");
  $("nSel").innerHTML = ["I1", "I2", "I3", "I4"].map(k => `<button data-n="${k}" class="plan text-xs">${k}</button>`).join("");
  document.querySelectorAll("[data-n]").forEach(b => b.onclick = () => { S.nodo = b.dataset.n; pintarNodo(); });
  sondear(); animar();
}
async function sondear() {
  if (location.hash === "#mapa") { try { S.sim = await api("/api/sim/estado"); pintarSim(); } catch (_) {} }
  setTimeout(sondear, location.hash === "#mapa" ? 150 : 1000);
}
function ajustarCanvas(c) { const r = c.getBoundingClientRect(), d = devicePixelRatio || 1; c.width = r.width * d; c.height = r.height * d; c.getContext("2d").setTransform(d, 0, 0, d, 0, 0); return r; }
function animar() { if (location.hash === "#mapa") dibujar(); requestAnimationFrame(animar); }
function dibujar() {
  const cv = $("mapa"), cx = cv.getContext("2d"), R = S.red; if (!R) return;
  const r = ajustarCanvas(cv), [x0, y0, x1, y1] = R.bbox, pad = 30;
  const s = Math.min((r.width - 2 * pad) / (x1 - x0), (r.height - 2 * pad) / (y1 - y0));
  const ox = (r.width - s * (x1 - x0)) / 2, oy = (r.height - s * (y1 - y0)) / 2;
  const X = x => ox + (x - x0) * s, Y = y => r.height - oy - (y - y0) * s;
  cx.fillStyle = "#0f1a2a"; cx.fillRect(0, 0, r.width, r.height);
  cx.strokeStyle = "rgba(255,255,255,.04)"; cx.lineWidth = 1;
  for (let gx = 0; gx < r.width; gx += 40) { cx.beginPath(); cx.moveTo(gx, 0); cx.lineTo(gx, r.height); cx.stroke(); }
  for (let gy = 0; gy < r.height; gy += 40) { cx.beginPath(); cx.moveTo(0, gy); cx.lineTo(r.width, gy); cx.stroke(); }
  cx.lineCap = cx.lineJoin = "round"; cx.strokeStyle = "#2c3e57"; cx.lineWidth = Math.max(4, 3.4 * s);
  for (const c of R.calles) { cx.beginPath(); c.forEach(([x, y], i) => i ? cx.lineTo(X(x), Y(y)) : cx.moveTo(X(x), Y(y))); cx.stroke(); }
  cx.fillStyle = "#2c3e57";
  for (const n of R.nodos) { cx.beginPath(); n.forEach(([x, y], i) => i ? cx.lineTo(X(x), Y(y)) : cx.moveTo(X(x), Y(y))); cx.closePath(); cx.fill(); }
  accPx = {};
  for (const [k, a] of Object.entries(R.accesos)) { accPx[k] = [X(a.x), Y(a.y)]; cx.fillStyle = "#c0392b"; cx.beginPath(); cx.arc(X(a.x), Y(a.y), 6, 0, 7); cx.fill(); }
  const E = S.sim;
  if (E) {
    const L = Math.max(3, 2.2 * s);
    for (const [x, y, c] of E.luces) { cx.fillStyle = c; cx.fillRect(X(x) - L / 2, Y(y) - L / 2, L, L); }
    const col = ["#e2e8f0", "#f59e0b", "#ef4444", "#38bdf8"], lw = Math.max(3, 1.8 * s), ll = Math.max(6, 4.5 * s);
    for (const [x, y, ang, t] of E.vehiculos) { cx.save(); cx.translate(X(x), Y(y)); cx.rotate(ang * Math.PI / 180);
      cx.fillStyle = t === 3 ? ((Date.now() / 250 | 0) % 2 ? "#38bdf8" : "#fff") : col[t]; cx.fillRect(-lw / 2, -ll / 2, lw * (t === 3 ? 1.6 : 1), ll); cx.restore(); }
  }
  interPx = {};
  cx.font = "bold 12px Inter";
  for (const [k, n] of Object.entries(R.intersecciones)) {
    const px = X(n.x), py = Y(n.y); interPx[k] = [px, py];
    const nd = E?.nodos?.[k], obs = S.res?.hallazgo_vigente?.r3?.some(v => v.startsWith(k + ":"));
    const color = nd?.todo_rojo ? "#ef4444" : nd?.preempcion ? "#38bdf8" : obs && E?.plan?.nombre !== "supervision" ? "#f59e0b" : "#10b981";
    cx.strokeStyle = color; cx.lineWidth = k === S.nodo ? 3 : 2; cx.beginPath(); cx.arc(px, py, k === S.nodo ? 16 : 12, 0, 7); cx.stroke();
    cx.fillStyle = "rgba(15,26,42,.85)"; cx.fillRect(px + 12, py + 4, 26, 18); cx.fillStyle = color; cx.fillText(k, px + 16, py + 17);
  }
}
$("mapa").onclick = e => {
  const r = $("mapa").getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  for (const [k, [px, py]] of Object.entries(interPx)) if (Math.hypot(px - x, py - y) < 22) { S.nodo = k; pintarNodo(); return; }
  for (const [k, [px, py]] of Object.entries(accPx)) if (Math.hypot(px - x, py - y) < 16) {
    simCmd(e.shiftKey ? { accion: "ambulancia", acceso: k } : { accion: "autos", acceso: k, n: 5 }); return; }
};
function pintarSim() {
  const E = S.sim; if (!E || E.t === undefined) return;
  const m = E.metricas, f = t => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  $("simReloj").textContent = f(E.t);
  $("simChip").textContent = E.corriendo ? "● Simulación SUMO · en curso" : "Simulación en pausa";
  $("simChip").className = "chip " + (E.corriendo ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600");
  $("sPlay").textContent = E.corriendo ? "⏸ Pausa" : "▶ Iniciar";
  document.querySelectorAll("[data-plan]").forEach(b => b.classList.toggle("sel", b.dataset.plan === E.plan.nombre));
  const kpi = (l, v) => `<div class="py-2 border-r last:border-r-0"><div class="text-[10px] uppercase text-slate-500">${l}</div><b class="text-base">${v}</b></div>`;
  $("simKpis").innerHTML = kpi("En red", m.en_red) + kpi("Detenidos", m.detenidos) + kpi("Espera prom.", m.completados ? m.espera_prom_s + " s" : "–") +
    kpi("Salidas/min", m.salidas_min || "–") + kpi("Ambulancia", m.amb_ultimo_s != null ? Math.round(m.amb_ultimo_s) + " s" : "–") + kpi("Plan", E.plan.nombre === "supervision" ? (E.plan.version || "supervisión") : "fijo");
  $("simPlan").innerHTML = `Plan en ejecución: <b>${E.plan.nombre === "supervision" ? "Supervisión " + esc(E.plan.version) : "fijo de la red"}</b>${E.plan.liberada ? "" : " · aún no hay configuración liberada"}`;
  $("simLog").textContent = E.log.join("\n");
  pintarNodo();
}
function pintarNodo() {
  const k = S.nodo, nd = S.sim?.nodos?.[k], inter = S.sis?.intersecciones || {}, norma = S.sis?.normativa || {};
  document.querySelectorAll("[data-n]").forEach(b => b.classList.toggle("sel", b.dataset.n === k));
  $("nTit").textContent = `${k} · ${inter[k] || ""}`;
  const res = S.res, ancho = norma.anchos_cruce_m?.[k], despeje = ancho ? (ancho / (norma.velocidad_cruce_peatonal_m_s || 1)).toFixed(1) : "—";
  const lib = res?.liberada || null, plan = (lib || res?.configuracion)?.planes_por_interseccion?.[k], vig = res?.vigente?.planes_por_interseccion?.[k];
  $("nSub").textContent = `Cruce de ${ancho ?? "—"} m · despeje mínimo ${despeje} s`;
  const dato = (l, v, s) => `<div class="tile"><div class="lbl">${l}</div><b class="text-lg">${v}</b><div class="text-[11px] text-slate-500">${s || ""}</div></div>`;
  $("nDatos").innerHTML = nd ? dato("Fase actual", `Fase ${nd.fase}`, `cambia en ${nd.restante_s} s`) +
    dato("Fila vehicular", `${nd.fila_veh} veh`, "detenidos en accesos") +
    dato("Verde peatonal", plan ? `${plan.verde_peatonal_s} s` : "—", plan ? (lib ? "configuración liberada" : "propuesta") : "sin ciclo") +
    dato("Vigente v1.0", vig ? `${vig.verde_peatonal_s} s` : "—", "verde peatonal") : '<div class="text-xs text-slate-500 col-span-2">Esperando al simulador…</div>';
  const r3 = res?.hallazgo_vigente?.violaciones?.filter(v => v.startsWith(k + ":")) || [];
  $("nRegla").innerHTML = r3.length ? `<b>Reglas aplicadas en ${k}</b><ul class="list-disc ml-4 mt-1">${r3.map(v => `<li>${esc(v)}</li>`).join("")}</ul>
    <div class="mt-1 text-slate-600">${plan ? `La propuesta lleva el verde peatonal a ${plan.verde_peatonal_s} s y el ciclo a ${plan.ciclo_s} s.` : ""}</div>` :
    `<span class="text-slate-500">${res ? "La configuración vigente cumple las reglas en este nodo." : "Corre un ciclo de supervisión para ver el dictamen por nodo."}</span>`;
  $("nChip").textContent = nd?.todo_rojo ? "Todo-rojo manual" : nd?.preempcion ? "Preempción" : r3.length ? (S.sim?.plan?.nombre === "supervision" ? "Veto corregido" : "Con observación") : "Cumple";
  $("nChip").className = "chip " + (nd?.todo_rojo ? "bg-red-100 text-red-700" : r3.length ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-700");
}
$("sPlay").onclick = () => simCmd({ accion: "toggle" });
$("sReset").onclick = () => simCmd({ accion: "reiniciar" });
$("sRafaga").onclick = () => simCmd({ accion: "rafaga", n: 40 });
$("sAmb").onclick = () => simCmd({ accion: "ambulancia", acceso: "sur" });
$("sPre").onchange = e => simCmd({ accion: "preempcion", valor: e.target.checked ? 1 : 0 });
$("sVel").oninput = e => { $("sVelT").textContent = e.target.value + "×"; simCmd({ accion: "velocidad", valor: +e.target.value }); };
$("sDem").oninput = e => { $("sDemT").textContent = e.target.value; simCmd({ accion: "demanda", valor: +e.target.value }); };
document.querySelectorAll("[data-plan]").forEach(b => b.onclick = () => simCmd({ accion: "plan", nombre: b.dataset.plan }));
$("aExt").onclick = () => simCmd({ accion: "extender", interseccion: S.nodo, valor: 5 });
$("aRojo").onclick = () => simCmd({ accion: "todo_rojo", interseccion: S.nodo, valor: 15 });
$("aFijo").onclick = () => simCmd({ accion: "plan", nombre: "fijo" });

async function cargarBitacora() {
  try { S.bit = await api("/api/bitacora"); } catch (_) { return; }
  if (!S.res && S.ciclo && S.ciclo.estado === "terminado")
    api(`/api/ciclos/${S.ciclo.id}/resultados`).then(r => { S.res = r; }).catch(() => {});
  pintarBitacora();
}
function pintarBitacora() {
  const q = $("bBuscar").value.toLowerCase(), v = (S.bit?.vetos || []).filter(x => !q || JSON.stringify(x).toLowerCase().includes(q));
  $("bChip").textContent = `${S.bit?.vetos.length || 0} registros`;
  const res = x => x.resultado === "Hallazgo" ? '<span class="chip bg-orange-50 text-orange-700">⚠ Hallazgo</span>' : '<span class="chip bg-red-50 text-red-700">⊘ Veto automático</span>';
  $("bTabla").innerHTML = v.map(x => `<tr><td class="td font-mono">${esc(x.ts)}</td><td class="td">#${x.numero}</td><td class="td text-xs">${esc(x.tarea)}<div class="text-slate-500">${esc(x.archivo)}</div></td>
    <td class="td font-semibold">${esc(x.interseccion)}</td><td class="td"><span class="agente">${esc(x.agente)}</span></td>
    <td class="td font-semibold text-amber-800">${esc(x.regla)}</td><td class="td text-xs">${esc(x.parametro)}</td><td class="td">${res(x)}</td></tr>`).join("")
    || '<tr><td class="td text-slate-500" colspan="8">Sin registros. Corre un ciclo de supervisión.</td></tr>';
  $("dTabla").innerHTML = (S.bit?.decisiones || []).map(d => `<tr><td class="td font-mono text-xs">${esc(d.ts)}</td><td class="td">#${d.numero}</td>
    <td class="td"><span class="chip ${d.accion === "autorizada" ? "bg-emerald-50 text-emerald-700" : d.accion === "rechazada" ? "bg-red-50 text-red-700" : "bg-inst-50 text-inst"}">${esc(d.accion)}</span></td>
    <td class="td">${esc(d.operador)}</td><td class="td">${esc(d.version)}</td><td class="td font-mono text-xs">${esc(d.huella)}</td><td class="td text-xs">${esc(d.motivo)}</td></tr>`).join("")
    || '<tr><td class="td text-slate-500" colspan="7">Sin decisiones registradas.</td></tr>';
}
$("bBuscar").oninput = pintarBitacora;

// ---------------- arranque ----------------
(async () => {
  S.motor = leerLS("motor") || "stub";
  await cargarSistema();
  vista();
  setInterval(() => { if (location.hash === "#mapa") cargarBitacora(); }, 5000);
})();
