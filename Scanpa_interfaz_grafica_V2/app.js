/* SCANPA · frontend */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  // Los SSID vienen del aire (cualquiera puede ponerles HTML): siempre se escapan.
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let snap = null;            // último estado recibido del servidor
  let servidorOk = false;
  let ajustesCargados = false;
  let sesionAbierta = null;
  let ultimoTs = null;
  const hist = new Map();     // bssid -> [{t, v}]
  const colores = ["#6fe0ff", "#4da3ff", "#b6a8ff", "#5ee3b0", "#ffd37a"];

  /* ---------- Utilidades ---------- */
  async function api(ruta, { method = "GET", json, body, headers } = {}) {
    const o = { method, headers: { ...(headers || {}) } };
    if (json !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(json); }
    if (body !== undefined) o.body = body;
    const r = await fetch(ruta, o);
    let d = null;
    try { d = await r.json(); } catch { /* sin cuerpo */ }
    if (!r.ok) throw new Error((d && d.error) || `Error ${r.status}`);
    return d;
  }

  function toast(texto, tipo = "") {
    const t = document.createElement("div");
    t.className = "toast " + tipo;
    t.textContent = texto;
    $("#toasts").appendChild(t);
    setTimeout(() => t.remove(), 3800);
  }

  function confirmar(titulo, texto, boton = "Eliminar") {
    return new Promise((res) => {
      const d = $("#dlg");
      $("#dlg-titulo").textContent = titulo;
      $("#dlg-texto").textContent = texto;
      $("button[value=ok]", d).textContent = boton;
      d.returnValue = "cancel";
      d.addEventListener("close", () => res(d.returnValue === "ok"), { once: true });
      d.showModal();
    });
  }

  const hora = (ts) => (ts ? ts.split(" ")[1] || ts : "—");

  /* ---------- Navegación ---------- */
  const titulos = { panel: "Panel en vivo", historial: "Histórico", ajustes: "Ajustes" };
  function ir(vista) {
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + vista));
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === vista));
    $("#titulo-vista").textContent = titulos[vista];
    location.hash = vista;
    if (vista === "historial") cargarSesiones();
    if (vista === "ajustes") { rellenarAjustes(true); cargarPuertos(); }
    if (vista === "panel") requestAnimationFrame(dibujarGrafica);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  $$(".nav-btn").forEach((b) => b.addEventListener("click", () => ir(b.dataset.view)));

  /* ---------- Tiempo real (SSE) ---------- */
  function conectarSSE() {
    const es = new EventSource("/api/eventos");
    es.onmessage = (e) => { servidorOk = true; recibir(JSON.parse(e.data)); };
    es.onerror = () => { servidorOk = false; pintarPill(); };
  }

  function recibir(s) {
    snap = s;
    if (s.estado.ultimo && s.estado.ultimo !== ultimoTs) {
      ultimoTs = s.estado.ultimo;
      const t = Date.now();
      for (const r of s.redes) {
        const a = hist.get(r.bssid) || [];
        a.push({ t, v: r.rssi, ssid: r.ssid });
        if (a.length > 60) a.shift();
        hist.set(r.bssid, a);
      }
      if ($("#view-historial").classList.contains("active")) cargarSesiones(true);
    }
    pintarPerfil();
    pintarPill();
    pintarBotones();
    pintarPanel();
    if (!ajustesCargados) rellenarAjustes(true);
    pintarDeps();
  }

  /* ---------- Perfil / estado ---------- */
  function pintarPerfil() {
    const c = snap.config;
    $$("[data-nombre]").forEach((e) => (e.textContent = c.nombre));
    $$("[data-avatar]").forEach((e) => {
      if (c.avatar) { e.style.backgroundImage = `url("${c.avatar}")`; e.textContent = ""; }
      else { e.style.backgroundImage = ""; e.textContent = (c.nombre || "U").trim().charAt(0).toUpperCase(); }
    });
  }

  function pintarPill() {
    let cls = "", txt = "";
    if (!servidorOk || !snap) { cls = "bad"; txt = "Servidor desconectado"; }
    else {
      const e = snap.estado;
      if (!e.escaneando) { cls = ""; txt = "Detenido"; }
      else if (e.midiendo) { cls = "run"; txt = "Escaneando…"; }
      else if (e.conectado) { cls = "ok"; txt = snap.config.demo ? "Modo demo" : "Placa conectada"; }
      else { cls = "bad"; txt = "Sin placa"; }
    }
    $$("[data-pill]").forEach((p) => { p.className = "pill " + cls + (p.classList.contains("top-pill") ? " top-pill" : ""); $("span", p).textContent = txt; });
  }

  function pintarBotones() {
    const on = snap.estado.escaneando;
    const b = $("#btn-toggle");
    b.classList.toggle("stop", on);
    $("span", b).textContent = on ? "Detener" : "Iniciar";
    $("use", b).setAttribute("href", on ? "#i-stop" : "#i-play");
  }

  $("#btn-toggle").addEventListener("click", async () => {
    try { await api(snap.estado.escaneando ? "/api/escaneo/detener" : "/api/escaneo/iniciar", { method: "POST", json: {} }); }
    catch (e) { toast(e.message, "bad"); }
  });
  $("#btn-ahora").addEventListener("click", async () => {
    try { await api("/api/escaneo/ahora", { method: "POST", json: {} }); } catch (e) { toast(e.message, "bad"); }
  });

  /* ---------- Panel ---------- */
  function seguridadTag(s) {
    const abierta = s.startsWith("Abierta");
    return `<span class="tag ${abierta ? "open" : ""}"><svg class="ic"><use href="#i-${abierta ? "unlock" : "lock"}"/></svg>${esc(s)}</span>`;
  }

  function pintarPanel() {
    const { redes, estado } = snap;
    const q = $("#buscar").value.trim().toLowerCase();
    const banda = $("#filtro-banda").value;
    const lista = redes.filter((r) =>
      (!banda || r.banda === banda) &&
      (!q || [r.ssid, r.bssid, r.fabricante].some((x) => x.toLowerCase().includes(q))));

    // Aviso de error
    const aviso = $("#aviso");
    if (estado.escaneando && estado.error) { aviso.textContent = "⚠ " + estado.error; aviso.classList.remove("hidden"); }
    else aviso.classList.add("hidden");

    // Tabla
    const tb = $("#tabla tbody");
    tb.innerHTML = lista.map((r) => `
      <tr>
        <td class="ssid" data-label="SSID">${esc(r.ssid)}<span class="sub">${r.oculta === "Sí" ? "Red oculta · " : ""}${esc(r.calidad_txt)}</span></td>
        <td data-label="Fabricante">${esc(r.fabricante)}</td>
        <td class="mono" data-label="BSSID">${esc(r.bssid)}</td>
        <td data-label="Canal"><span class="tag ${r.banda === "5 GHz" ? "b5" : ""}">${r.canal} · ${esc(r.banda)}</span></td>
        <td data-label="Señal"><div class="sig"><div class="bar"><i class="q${r.calidad_pct}" style="width:${r.calidad_pct}%"></i></div><span>${r.rssi} dBm</span></div></td>
        <td data-label="Distancia">~${r.distancia_aprox_m} m</td>
        <td data-label="Seguridad">${seguridadTag(r.seguridad)}</td>
      </tr>`).join("");
    $("#vacio").classList.toggle("hidden", lista.length > 0 || redes.length > 0 && false);
    $("#vacio").classList.toggle("hidden", redes.length > 0);
    $(".table-wrap").classList.toggle("hidden", redes.length === 0);

    // Estadísticas
    const n24 = redes.filter((r) => r.banda === "2.4 GHz").length;
    $("#st-total").textContent = redes.length;
    $("#st-bandas").textContent = redes.length ? `${n24} en 2.4 GHz · ${redes.length - n24} en 5 GHz` : "—";
    const mejor = redes[0];
    $("#st-mejor").textContent = mejor ? mejor.ssid : "—";
    $("#st-mejor-d").textContent = mejor ? `${mejor.rssi} dBm · ~${mejor.distancia_aprox_m} m` : "—";
    $("#st-ultimo").textContent = hora(estado.ultimo);
    $("#st-sesion").textContent = estado.sesion ? `Sesión activa · ${estado.n_escaneos} escaneos` : "Sin sesión";

    // Canales
    const cuenta = new Map();
    redes.forEach((r) => cuenta.set(r.canal, (cuenta.get(r.canal) || 0) + 1));
    const canales = [...cuenta.entries()].sort((a, b) => a[0] - b[0]);
    const max = Math.max(1, ...cuenta.values());
    $("#canales").innerHTML = canales.length
      ? canales.map(([c, n]) => `<div class="canal ${c > 14 ? "b5" : ""}"><div class="col" style="height:${Math.max(6, (n / max) * 82)}%"><em>${n}</em></div><small>CH ${c}</small></div>`).join("")
      : `<div class="vacio">Sin datos</div>`;
    if (canales.length) {
      const top = [...canales].sort((a, b) => b[1] - a[1])[0];
      $("#st-canal").textContent = "CH " + top[0];
      $("#st-canal-d").textContent = `${top[1]} red${top[1] > 1 ? "es" : ""}`;
    } else { $("#st-canal").textContent = "—"; $("#st-canal-d").textContent = "—"; }

    dibujarGrafica();
  }
  $("#buscar").addEventListener("input", () => snap && pintarPanel());
  $("#filtro-banda").addEventListener("change", () => snap && pintarPanel());

  /* ---------- Gráfica de señal ---------- */
  function dibujarGrafica() {
    const cv = $("#grafica");
    if (!cv || !snap || !cv.offsetParent) return;
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = 230;
    cv.width = w * dpr; cv.height = h * dpr;
    const g = cv.getContext("2d");
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);

    const pad = { l: 38, r: 10, t: 10, b: 20 };
    const yMin = -100, yMax = -30;
    const Y = (v) => pad.t + ((yMax - v) / (yMax - yMin)) * (h - pad.t - pad.b);

    g.font = "11px system-ui, sans-serif";
    g.fillStyle = "rgba(157,184,221,.85)";
    g.strokeStyle = "rgba(160,205,255,.12)";
    g.lineWidth = 1;
    for (let v = -30; v >= -100; v -= 10) {
      g.beginPath(); g.moveTo(pad.l, Y(v)); g.lineTo(w - pad.r, Y(v)); g.stroke();
      g.fillText(v, 6, Y(v) + 4);
    }

    const top = snap.redes.slice(0, 5);
    const todos = top.flatMap((r) => hist.get(r.bssid) || []);
    const leyenda = $("#leyenda");
    if (!todos.length) { leyenda.innerHTML = ""; return; }
    const t0 = Math.min(...todos.map((p) => p.t)), t1 = Math.max(...todos.map((p) => p.t), t0 + 1);
    const X = (t) => pad.l + ((t - t0) / (t1 - t0)) * (w - pad.l - pad.r);

    top.forEach((r, i) => {
      const pts = hist.get(r.bssid) || [];
      if (!pts.length) return;
      g.strokeStyle = colores[i];
      g.lineWidth = 2;
      g.lineJoin = "round";
      g.shadowColor = colores[i]; g.shadowBlur = 8;
      g.beginPath();
      pts.forEach((p, k) => (k ? g.lineTo(X(p.t), Y(p.v)) : g.moveTo(X(p.t), Y(p.v))));
      if (pts.length === 1) g.lineTo(X(pts[0].t) + 1, Y(pts[0].v));
      g.stroke();
      g.shadowBlur = 0;
    });
    leyenda.innerHTML = top.map((r, i) => `<span><i style="background:${colores[i]}"></i>${esc(r.ssid)}</span>`).join("");
  }
  window.addEventListener("resize", () => dibujarGrafica());

  /* ---------- Histórico ---------- */
  async function cargarSesiones(silencioso = false) {
    try {
      const { sesiones } = await api("/api/sesiones");
      $("#sesiones-vacio").classList.toggle("hidden", sesiones.length > 0);
      $("#lista-sesiones").innerHTML = sesiones.map((s) => `
        <div class="sesion ${s.id === sesionAbierta ? "sel" : ""}" data-id="${s.id}">
          <div class="info">
            <b>${esc(s.inicio)} <span class="badge" ${s.activa ? "" : "hidden"}>EN CURSO</span></b>
            <small>${s.escaneos} escaneos · ${s.redes_unicas} redes distintas · hasta ${esc(hora(s.fin))}</small>
          </div>
          <div class="btns">
            <a class="btn mini" href="/api/sesiones/${s.id}/descargar?formato=csv"><svg class="ic"><use href="#i-download"/></svg>CSV</a>
            <a class="btn mini" href="/api/sesiones/${s.id}/descargar?formato=txt"><svg class="ic"><use href="#i-download"/></svg>TXT</a>
            <button class="btn mini danger" data-del="${s.id}"><svg class="ic"><use href="#i-trash"/></svg>Eliminar</button>
          </div>
        </div>`).join("");
      if (sesionAbierta && !sesiones.some((s) => s.id === sesionAbierta)) cerrarDetalle();
      else if (sesionAbierta && !silencioso) abrirDetalle(sesionAbierta);
    } catch (e) { toast(e.message, "bad"); }
  }

  $("#lista-sesiones").addEventListener("click", async (e) => {
    if (e.target.closest("a")) return;
    const del = e.target.closest("[data-del]");
    if (del) {
      e.stopPropagation();
      if (await confirmar("Eliminar sesión", "Se borrará el histórico y sus ficheros CSV y TXT. No se puede deshacer.")) {
        try { await api("/api/sesiones/" + del.dataset.del, { method: "DELETE" }); toast("Sesión eliminada", "ok"); cargarSesiones(); }
        catch (err) { toast(err.message, "bad"); }
      }
      return;
    }
    const s = e.target.closest(".sesion");
    if (s) abrirDetalle(s.dataset.id);
  });

  async function abrirDetalle(id) {
    try {
      const d = await api("/api/sesiones/" + id);
      sesionAbierta = id;
      $$(".sesion").forEach((x) => x.classList.toggle("sel", x.dataset.id === id));
      $("#detalle").classList.remove("hidden");
      $("#detalle-titulo").textContent = `Sesión ${id.replace("_", " ").replace(/-/g, (m, i) => (i > 9 ? ":" : "-"))}`;
      const lista = [...d.escaneos].reverse();
      const MAX = 60;
      const html = (arr) => arr.map((sc) => `
        <details class="escaneo">
          <summary><b>${esc(sc.ts)}</b><small>${sc.redes.length} redes</small>
            <button class="btn mini danger" data-esc="${sc.id}"><svg class="ic"><use href="#i-trash"/></svg></button></summary>
          <div class="table-wrap"><table><thead><tr><th>SSID</th><th>Fabricante</th><th>BSSID</th><th>Canal</th><th>Señal</th><th>Dist.</th><th>Seguridad</th></tr></thead><tbody>
            ${sc.redes.slice().sort((a, b) => b.rssi - a.rssi).map((r) => `<tr>
              <td class="ssid" data-label="SSID">${esc(r.ssid)}</td><td data-label="Fabricante">${esc(r.fabricante)}</td>
              <td class="mono" data-label="BSSID">${esc(r.bssid)}</td><td data-label="Canal">${r.canal} · ${esc(r.banda)}</td>
              <td data-label="Señal">${r.rssi} dBm</td><td data-label="Distancia">~${r.distancia_aprox_m} m</td>
              <td data-label="Seguridad">${esc(r.seguridad)}</td></tr>`).join("")}
          </tbody></table></div>
        </details>`).join("");
      $("#detalle-lista").innerHTML = html(lista.slice(0, MAX)) +
        (lista.length > MAX ? `<div class="fila end"><button class="btn" id="btn-mas">Mostrar ${lista.length - MAX} más antiguos</button></div>` : "");
      const mas = $("#btn-mas");
      if (mas) mas.addEventListener("click", () => { $("#detalle-lista").innerHTML = html(lista); });
    } catch (e) { toast(e.message, "bad"); }
  }

  function cerrarDetalle() { sesionAbierta = null; $("#detalle").classList.add("hidden"); $$(".sesion").forEach((x) => x.classList.remove("sel")); }
  $("#btn-cerrar-detalle").addEventListener("click", cerrarDetalle);

  $("#detalle-lista").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-esc]");
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    if (await confirmar("Eliminar registro", "Se borrará este escaneo del histórico y de los ficheros CSV/TXT.")) {
      try { await api(`/api/sesiones/${sesionAbierta}/escaneos/${b.dataset.esc}`, { method: "DELETE" }); toast("Registro eliminado", "ok"); await cargarSesiones(); }
      catch (err) { toast(err.message, "bad"); }
    }
  });

  $("#btn-recargar-hist").addEventListener("click", () => cargarSesiones());
  $("#btn-borrar-todo").addEventListener("click", async () => {
    if (await confirmar("Eliminar todo el histórico", "Se borrarán TODAS las sesiones y sus ficheros CSV y TXT. No se puede deshacer.", "Eliminar todo")) {
      try { const r = await api("/api/sesiones", { method: "DELETE" }); toast(`${r.borradas} sesiones eliminadas`, "ok"); cerrarDetalle(); cargarSesiones(); }
      catch (e) { toast(e.message, "bad"); }
    }
  });

  /* ---------- Ajustes ---------- */
  function rellenarAjustes(forzar) {
    if (!snap) return;
    if (ajustesCargados && !forzar) return;
    const c = snap.config;
    $("#ajuste-nombre").value = c.nombre;
    $("#ajuste-puerto").value = c.puerto;
    $("#ajuste-intervalo").value = c.intervalo;
    $("#ajuste-demo").checked = c.demo;
    $("#ajuste-autoiniciar").checked = c.autoiniciar;
    ajustesCargados = true;
  }

  async function cargarPuertos() {
    try {
      const { puertos } = await api("/api/puertos");
      $("#lista-puertos").innerHTML = puertos.map((p) => `<option value="${esc(p.device)}">${esc(p.desc)}</option>`).join("");
      const info = $("#puertos-info");
      if (!puertos.length) info.textContent = "No se ve ningún puerto serie. Conecta la ESP32 por USB (y revisa el driver CP210x/CH340).";
      else info.innerHTML = "Puertos: " + puertos.map((p) => `<b>${esc(p.device)}</b>${p.probable ? " ★" : ""}`).join(" · ") + "  (★ = parece una ESP32)";
      return puertos;
    } catch (e) { toast(e.message, "bad"); return []; }
  }

  $("#btn-detectar").addEventListener("click", async () => {
    const p = await cargarPuertos();
    const esp = p.find((x) => x.probable) || p[0];
    if (esp) { $("#ajuste-puerto").value = esp.device; toast("Puerto seleccionado: " + esp.device, "ok"); }
  });

  $("#btn-guardar").addEventListener("click", async () => {
    try {
      await api("/api/config", { method: "POST", json: {
        nombre: $("#ajuste-nombre").value,
        puerto: $("#ajuste-puerto").value,
        intervalo: $("#ajuste-intervalo").value,
        demo: $("#ajuste-demo").checked,
        autoiniciar: $("#ajuste-autoiniciar").checked,
      } });
      toast("Ajustes guardados", "ok");
    } catch (e) { toast(e.message, "bad"); }
  });

  $("#btn-probar").addEventListener("click", async () => {
    const out = $("#probar-res"), b = $("#btn-probar");
    // Guarda primero el puerto actual para probar lo que se ve en pantalla
    try {
      await api("/api/config", { method: "POST", json: { puerto: $("#ajuste-puerto").value, demo: $("#ajuste-demo").checked } });
      b.disabled = true; out.className = "nota"; out.textContent = "Probando…";
      const r = await api("/api/probar", { method: "POST", json: {} });
      out.className = "nota " + (r.ok ? "ok" : "bad");
      out.textContent = (r.ok ? "✔ " : "✖ ") + r.mensaje;
    } catch (e) { out.className = "nota bad"; out.textContent = "✖ " + e.message; }
    b.disabled = false;
  });

  function pintarDeps() {
    $("#deps").innerHTML = Object.entries(snap.deps).map(([k, ok]) =>
      `<span class="tag ${ok ? "" : "open"}">${ok ? "✔" : "✖"} ${esc(k)}</span>`).join("");
  }

  $("#btn-instalar").addEventListener("click", async () => {
    const b = $("#btn-instalar"), log = $("#instalar-log");
    b.disabled = true; b.textContent = "Instalando…";
    log.classList.remove("hidden"); log.textContent = "Ejecutando pip, puede tardar un momento…";
    try {
      const r = await api("/api/instalar", { method: "POST", json: { forzar: true } });
      log.textContent = r.log;
      toast(r.ok ? "Dependencias listas" : "Falló la instalación", r.ok ? "ok" : "bad");
    } catch (e) { log.textContent = e.message; toast(e.message, "bad"); }
    b.disabled = false; b.textContent = "Instalar / reparar";
  });

  /* Foto de perfil: se recorta a cuadrado y se reduce a 256 px antes de subirla */
  async function reducirImagen(file, lado = 256) {
    const bmp = await createImageBitmap(file);
    const s = Math.min(bmp.width, bmp.height);
    const cv = document.createElement("canvas");
    cv.width = cv.height = lado;
    cv.getContext("2d").drawImage(bmp, (bmp.width - s) / 2, (bmp.height - s) / 2, s, s, 0, 0, lado, lado);
    return new Promise((res, rej) => cv.toBlob((b) => (b ? res(b) : rej(new Error("No se pudo procesar la imagen"))), "image/jpeg", 0.9));
  }

  $("#btn-avatar").addEventListener("click", () => $("#file-avatar").click());
  $("#file-avatar").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const blob = await reducirImagen(f);
      await api("/api/avatar", { method: "POST", body: blob, headers: { "Content-Type": "image/jpeg" } });
      toast("Foto actualizada", "ok");
    } catch (err) { toast(err.message, "bad"); }
    e.target.value = "";
  });
  $("#btn-avatar-quitar").addEventListener("click", async () => {
    try { await api("/api/avatar", { method: "DELETE" }); toast("Foto eliminada", "ok"); } catch (e) { toast(e.message, "bad"); }
  });

  /* ---------- Inicio ---------- */
  const inicial = (location.hash || "#panel").slice(1);
  if (titulos[inicial] && inicial !== "panel") ir(inicial);
  conectarSSE();
})();
