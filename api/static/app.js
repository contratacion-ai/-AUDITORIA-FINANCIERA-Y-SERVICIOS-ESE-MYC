// Interfaz local de carga y análisis. Toda la lógica de cálculo está en el backend (rips_auditoria).
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nf = (v, d = 0) => (v == null || isNaN(v)) ? '—' : Number(v).toLocaleString('es-CO', {maximumFractionDigits: d});
const F = v => (v == null || isNaN(v)) ? '—' : '$' + nf(Math.round(v));
const FM = v => '$' + nf(v / 1e6, 1) + ' M';
const MONEY = /valor|costo|tarifa|glosa|acumulado|promedio_mes|^20\d\d-/i;

function tabla(rows, cols) {
  if (!rows || !rows.length) return '<p class="nota">Sin datos.</p>';
  cols = cols || Object.keys(rows[0]);
  const td = (c, v) => typeof v === 'number' ? `<td class="n">${MONEY.test(c) ? F(v) : nf(v, 2)}</td>`
                                               : `<td class="${esc(v)}">${esc(v)}</td>`;
  return `<div class="scroll"><table><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>` +
    rows.map(r => `<tr>${cols.map(c => td(c, r[c])).join('')}</tr>`).join('') + '</tbody></table></div>';
}
async function api(url, opt) {
  const r = await fetch(url, opt);
  if (!r.ok) { const t = await r.text(); throw new Error(t || r.statusText); }
  return r.json();
}
const JSONB = b => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
$('bCerrar').onclick = async e => {
  e.stopPropagation();
  if (!confirm('¿Cerrar la aplicación? Los datos quedan guardados.')) return;
  try { await api('/api/apagar', {method: 'POST'}); } catch (_) {}
  document.body.innerHTML = '<main><h2>La aplicación se cerró. Puede cerrar esta pestaña.</h2></main>';
};
document.querySelectorAll('.tb:not(.cerrar)').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tb,.sec').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); $('s-' + b.dataset.s).classList.add('on');
  ({base: cargarBase, maestros: cargarMaestros, analisis: cargarFiltros, referencia: cargarReferencia}[b.dataset.s] || (() => {}))();
});

// ---------------------------------------------------------------- 1. carga
let seleccion = [];   // [{file, ruta}]
function agregar(files) {
  for (const f of files) seleccion.push({file: f, ruta: f.webkitRelativePath || f._ruta || f.name});
  $('nSel').textContent = `${seleccion.length} archivos seleccionados`;
}
$('fArch').onchange = e => agregar(e.target.files);
$('fCarp').onchange = e => agregar(e.target.files);
$('bLimpiarSel').onclick = () => { seleccion = []; $('nSel').textContent = '0 archivos seleccionados'; };
async function leerEntrada(entry, ruta) {      // carpetas arrastradas
  if (entry.isFile) return new Promise(res => entry.file(f => { f._ruta = ruta + f.name; res([f]); }));
  const lector = entry.createReader(); let todos = [];
  const leer = () => new Promise(res => lector.readEntries(res));
  let lote; while ((lote = await leer()).length) todos = todos.concat(lote);
  const out = await Promise.all(todos.map(e => leerEntrada(e, ruta + entry.name + '/')));
  return out.flat();
}
const dr = $('drop');
['dragover', 'dragenter'].forEach(ev => dr.addEventListener(ev, e => { e.preventDefault(); dr.classList.add('on'); }));
['dragleave', 'drop'].forEach(ev => dr.addEventListener(ev, e => { e.preventDefault(); dr.classList.remove('on'); }));
dr.addEventListener('drop', async e => {
  const items = [...e.dataTransfer.items].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (items.length) { for (const it of items) agregar(await leerEntrada(it, '')); }
  else agregar(e.dataTransfer.files);
});
$('bCargar').onclick = async () => {
  if (!seleccion.length) return;
  const fd = new FormData();
  seleccion.forEach(s => { fd.append('archivos', s.file, s.file.name); fd.append('rutas', s.ruta); });
  fd.append('hospital', $('mHosp').value); fd.append('municipio', $('mMun').value);
  fd.append('periodo', $('mPer').value); fd.append('cod_entidad', $('mEps').value); fd.append('anio', $('mAnio').value);
  fd.append('modo', $('mModo').value);
  $('msgCarga').innerHTML = '⏳ Procesando y depurando…';
  try {
    const r = await api('/api/lotes', {method: 'POST', body: fd});
    seleccion = []; $('nSel').textContent = '0 archivos seleccionados';
    const b = r.base || {nuevas: [], reemplazadas: [], omitidas: []};
    $('msgCarga').innerHTML = `<span class="ok">✔ Carga ${esc(r.id)} procesada. Base actualizada: ${b.nuevas.length} hospital-mes nuevos,
      ${b.reemplazadas.length} reemplazados. ${r.duplicados_eliminados} registros repetidos depurados.</span>`;
    $('resCarga').innerHTML = detalleLote(r);
  } catch (e) { $('msgCarga').innerHTML = `<span class="err">✖ ${esc(e.message)}</span>`; }
};
function detalleLote(r) {
  return `<h3>Índice hospital-mes</h3>${tabla(r.indice)}
    <h3>Remisiones (corrija hospital, municipio, mes o EPS y guarde: la base se actualiza)</h3>${tablaRemisiones(r)}
    <h3>Incidencias de validación</h3>${tabla(r.incidencias)}
    <p><a href="/api/lotes/${esc(r.id)}/incidencias.xlsx">⬇ Descargar validación completa (Excel)</a></p>
    <h3>Archivos no procesados</h3>${tabla(r.archivos_no_procesados)}`;
}
function tablaRemisiones(r) {
  const cols = ['envio_id', 'hospital', 'municipio', 'periodo', 'cod_entidad', 'carpeta_envio', 'modalidad_declarada', 'formato', 'fuente_municipio', 'fuente_periodo'];
  const ed = ['hospital', 'municipio', 'periodo', 'cod_entidad'];
  return `<div class="scroll"><table data-lote="${esc(r.id)}"><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>` +
    r.remisiones.map(x => `<tr data-env="${esc(x.envio_id)}">${cols.map(c => ed.includes(c)
      ? `<td><input data-c="${c}" value="${esc(x[c])}" size="${c === 'hospital' ? 26 : 9}"></td>` : `<td>${esc(x[c])}</td>`).join('')}</tr>`).join('') +
    `</tbody></table></div><button class="sec2" onclick="guardarCorrecciones(this)">Guardar correcciones</button>`;
}
async function guardarCorrecciones(btn) {
  const t = btn.previousElementSibling.querySelector('table'); const lid = t.dataset.lote; const cambios = [];
  t.querySelectorAll('tbody tr').forEach(tr => {
    const c = {envio_id: tr.dataset.env};
    tr.querySelectorAll('input').forEach(i => { if (i.value !== i.defaultValue) c[i.dataset.c] = i.value; });
    if (Object.keys(c).length > 1) cambios.push(c);
  });
  if (!cambios.length) return alert('Sin cambios');
  const r = await api(`/api/lotes/${lid}/remisiones`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(cambios)});
  alert(`${r.registros_actualizados} registros actualizados`);
}

// ---------------------------------------------------------------- 2. base y Drive
async function cargarBase() {
  const [b, f, l] = await Promise.all([api('/api/base'), api('/api/fuentes'), api('/api/lotes')]);
  $('lstFuentes').innerHTML = f.carpetas.length
    ? `<table><thead><tr><th>Carpeta vigilada</th><th></th></tr></thead><tbody>${f.carpetas.map(c =>
        `<tr><td>${esc(c)}</td><td><button class="sec2" data-ruta="${esc(c)}" onclick="quitarFuente(this.dataset.ruta)">Quitar</button></td></tr>`).join('')}</tbody></table>
       <p class="nota">Última sincronización: ${esc(f.ultima_sincronizacion || 'nunca')} · ${f.unidades_registradas} carpetas de mes registradas</p>`
    : '<p class="nota">Aún no hay carpetas de Drive registradas.</p>';
  const s = b.resumen;
  const cols = ['hospital', 'municipio', 'periodo', 'carpetas', 'remisiones', 'facturas', 'facturas_nueva_eps', 'servicios', 'valor_af', 'errores', 'cargado', 'origen'];
  $('resBase').innerHTML = `<div class="kpis">${[['Hospital-mes', s.particiones], ['Hospitales', s.hospitales], ['Meses', s.meses],
      ['Facturas', nf(s.facturas)], ['Servicios', nf(s.servicios)]].map(x => `<div class="kc"><div class="lb">${x[0]}</div><div class="vl">${x[1]}</div></div>`).join('')}</div>` +
    (b.particiones.length ? `<div class="scroll"><table><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}<th>Limpiar</th></tr></thead><tbody>` +
      b.particiones.map(p => `<tr>${cols.map(c => typeof p[c] === 'number' ? `<td class="n">${c === 'valor_af' ? F(p[c]) : nf(p[c])}</td>`
        : c === 'carpetas' ? `<td title="${esc(p[c])}">${(p[c] || '').split(', ').filter(Boolean).length} carpetas</td>`
        : c === 'origen' ? `<td title="${esc(p[c])}">${esc(String(p[c] || '').split(/[\\/]/).slice(-2).join('/'))}</td>`
        : `<td>${esc(p[c])}</td>`).join('')}
        <td><button class="sec2" data-k="${esc(p.clave)}" onclick="borrarParticion(this.dataset.k)">Eliminar</button></td></tr>`).join('') + '</tbody></table></div>'
      : '<p class="nota">La base está vacía. Cargue carpetas o sincronice Drive.</p>');
  $('listaLotes').innerHTML = l.slice(0, 15).map(x => `<button class="sec2" onclick="verLote('${esc(x.id)}')">${esc(x.id)} · ${esc(x.hospitales.join(', '))} · ${esc(x.periodos.join(', '))}</button> `).join('') || '<p class="nota">Sin cargas.</p>';
}
async function verLote(id) { $('detLote').innerHTML = detalleLote(await api('/api/lotes/' + id)); }
async function borrarParticion(k) {
  if (!confirm('¿Eliminar este hospital-mes de la base?')) return;
  await api('/api/base?clave=' + encodeURIComponent(k), {method: 'DELETE'}); cargarBase();
}
async function quitarFuente(r) { await api('/api/fuentes?ruta=' + encodeURIComponent(r), {method: 'DELETE'}); cargarBase(); }
$('bAddFuente').onclick = async () => {
  try { await api('/api/fuentes', JSONB({ruta: $('fRuta').value.trim()})); $('fRuta').value = ''; cargarBase(); }
  catch (e) { $('msgSync').innerHTML = `<span class="err">✖ ${esc(e.message)}</span>`; }
};
async function sincronizar(forzar) {
  $('msgSync').innerHTML = '⏳ Sincronizando carpetas de Drive…';
  try {
    const r = await api('/api/fuentes/sincronizar', JSONB({forzar}));
    $('msgSync').innerHTML = `<span class="ok">✔ ${r.procesadas.length} carpetas de mes actualizadas · ${r.sin_cambios} sin cambios ·
      ${r.errores.length} con error</span>${tabla(r.procesadas)}${r.errores.length ? tabla(r.errores) : ''}
      ${r.ausentes.length ? `<p class="nota">Ya no están en Drive (revise si debe limpiarlas): ${r.ausentes.map(esc).join('; ')}</p>` : ''}`;
    cargarBase();
  } catch (e) { $('msgSync').innerHTML = `<span class="err">✖ ${esc(e.message)}</span>`; }
}
$('bSync').onclick = () => sincronizar(false);
$('bSyncF').onclick = () => sincronizar(true);
$('bVaciar').onclick = async () => {
  if (!confirm('Se borrarán TODOS los RIPS de la base consolidada (los maestros se conservan). ¿Continuar?')) return;
  await api('/api/base/vaciar', JSONB({confirmar: true})); cargarBase();
};

// ---------------------------------------------------------------- 3. maestros
async function cargarMaestros() {
  const m = await api('/api/maestros');
  const filas = [{nombre: 'informe', fuente: 'INFORME DE SERVICIOS (catálogo + NT COSTOS + NT por municipio)'}, ...m];
  $('tbMae').querySelector('tbody').innerHTML = filas.map(x => `<tr><td><b>${esc(x.nombre)}</b></td><td>${esc(x.fuente || '—')}</td>
    <td class="n">${nf(x.filas)}</td><td>${esc(x.cargado || '')}</td><td>${esc(x.pendiente_confirmacion || '')}</td>
    <td><input type="file" onchange="subirMaestro('${esc(x.nombre)}', this)"></td>
    <td>${x.nombre !== 'informe' ? `<a href="/api/maestros/plantillas/${esc(x.nombre)}.csv">plantilla</a>` : ''}</td></tr>`).join('');
}
async function subirMaestro(nombre, inp) {
  const fd = new FormData(); fd.append('archivo', inp.files[0]);
  $('msgMae').innerHTML = '⏳ Cargando…';
  try { const r = await api('/api/maestros/' + nombre, {method: 'POST', body: fd});
    const c = r.conciliacion_control; delete r.conciliacion_control;
    $('msgMae').innerHTML = `<pre class="ok">${esc(JSON.stringify(r, null, 1))}</pre>` + (c ? `<h3>Conciliación con el control</h3>${tabla(c)}` : '');
    cargarMaestros();
  } catch (e) { $('msgMae').innerHTML = `<span class="err">✖ ${esc(e.message)}</span>`; }
}

// ---------------------------------------------------------------- 4. análisis
async function cargarFiltros() {
  const b = await api('/api/base');
  const opc = (id, xs) => { $(id).innerHTML = xs.map(x => `<option>${esc(x)}</option>`).join(''); };
  opc('fMun', b.filtros.municipios); opc('fHosp', b.filtros.hospitales); opc('fPer', b.filtros.periodos);
}
const sel = id => [...$(id).selectedOptions].map(o => o.value);
function mostrarResultado(r) {
  const k = r.indicadores.capita, i = r.indicadores;
  const kp = [['Costo CÁPITA', FM(k.total)], ['Promedio/mes', FM(k.promedio_mes)], ['Pacientes (suma mensual)', nf(k.pacientes_suma)],
    ['Asistencias', nf(k.asistencias)], ['Costo/usuario', F(k.costo_usuario_prom)],
    ['Ejecución frecuencias NT', i.ejecucion_nt_pct == null ? '—' : nf(i.ejecucion_nt_pct, 1) + ' %'],
    ['Servicios sobre NT', nf(i.codigos_sobreejecucion)], ['Facturación EVENTO', FM(i.evento_total)],
    ['Hallazgos ALTA', nf(i.hallazgos_alta)], ['Valor en riesgo', FM(i.valor_en_riesgo)]];
  $('resAn').innerHTML = `<div class="kpis">${kp.map(x => `<div class="kc"><div class="lb">${x[0]}</div><div class="vl">${x[1]}</div></div>`).join('')}</div>
    ${r.advertencias.map(a => `<p class="nota err">⚠ ${esc(a)}</p>`).join('')}
    <p><a href="/api/analisis/${esc(r.id)}/informe.xlsx">⬇ Informe Excel</a> · <a href="/api/analisis/${esc(r.id)}/informe.pdf">⬇ Informe PDF</a> ·
       <a href="/api/analisis/${esc(r.id)}/estado.json">⬇ Guardar estado (JSON)</a></p>
    <h3>Frecuencias Nota Técnica Nueva EPS vs RIPS · por municipio y mes</h3>${tabla(r.frecuencias_nt_municipio_mes)}
    <h3>Frecuencias por grupo (acumulado)</h3>${tabla(r.frecuencias_nt_grupo)}
    <h3>Servicios con mayor desviación frente a la NT</h3>${tabla(r.frecuencias_nt_codigo)}
    <h3>CÁPITA · detalle mensual</h3>${tabla(r.detalle_mensual)}<h3>CÁPITA · costos por grupo</h3>${tabla(r.capita_grupos)}
    <h3>EVENTO · facturación mensual</h3>${tabla(r.evento_mensual)}
    <h3>EVENTO · Top 20 por frecuencia</h3>${tabla(r.top20_evento, ['n', 'codigo', 'descripcion', 'grupo_nombre', 'frecuencia', 'valor', 'valor_unitario', 'estado'])}
    <h3>Hallazgos (50 primeros)</h3>${tabla(r.hallazgos)}`;
}
$('bAnalizar').onclick = async () => {
  $('msgAn').innerHTML = '⏳ Analizando…';
  try { const r = await api('/api/analisis', JSONB({municipios: sel('fMun'), hospitales: sel('fHosp'), periodos: sel('fPer'),
      solo_eps_objetivo: $('soloNeps').checked}));
    $('msgAn').innerHTML = `<span class="ok">✔ Resultado ${esc(r.id)}</span>`; mostrarResultado(r);
  } catch (e) { $('msgAn').innerHTML = `<span class="err">✖ ${esc(e.message)}</span>`; }
};
$('fRest').onchange = async e => {
  const fd = new FormData(); fd.append('archivo', e.target.files[0]);
  try { mostrarResultado(await api('/api/analisis/restaurar', {method: 'POST', body: fd})); $('msgAn').innerHTML = '<span class="ok">✔ Estado restaurado</span>'; }
  catch (er) { $('msgAn').innerHTML = `<span class="err">✖ ${esc(er.message)}</span>`; }
};

// ---------------------------------------------------------------- 5. referencia
async function cargarReferencia() {
  const r = await api('/api/referencia');
  const filas = Object.keys(r.control).map(k => ({indicador: k, control_oficial: r.control[k], recalculo: r.calculado[k],
    dashboard_final_historico: r.historico_dashboard_final[k] ?? null}));
  $('resRef').innerHTML = `<p class="nota">Control oficial = libro INFORME DE SERVICIOS 2026 vigente con la cifra actual de
    medicamentos ($236,1 M · 554.587 CUMS). El dashboard FINAL se muestra solo como referencia histórica.</p>
    <h3>Totales de control oficiales vs recálculo</h3>${tabla(filas)}` +
    (r.conciliacion_informe && r.conciliacion_informe.length
      ? `<h3>Conciliación del libro cargado</h3>${tabla(r.conciliacion_informe)}`
      : '<p class="nota">Cargue el libro INFORME DE SERVICIOS en Maestros para ver su conciliación.</p>') +
    `<h3>Participación por grupo (oficial)</h3>${tabla(Object.entries(r.grupos).map(([g, p]) => ({grupo: g, participacion_pct: p})))}`;
}
