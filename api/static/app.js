// Interfaz local de carga y análisis. Toda la lógica de cálculo está en el backend (rips_auditoria).
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nf = (v, d = 0) => (v == null || isNaN(v)) ? '—' : Number(v).toLocaleString('es-CO', {maximumFractionDigits: d});
const F = v => (v == null || isNaN(v)) ? '—' : '$' + nf(Math.round(v));
const FM = v => '$' + nf(v / 1e6, 1) + ' M';
const MONEY = /valor|costo|giro|tarifa|glosa|pgp|evento|acumulado|promedio|run_rate|limite|^20\d\d-/i;

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
document.querySelectorAll('.tb').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tb,.sec').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); $('s-' + b.dataset.s).classList.add('on');
  ({lotes: cargarLotes, maestros: cargarMaestros, analisis: listarParaAnalisis, referencia: cargarReferencia}[b.dataset.s] || (() => {}))();
});

// ---------------------------------------------------------------- 1. carga
let seleccion = [];   // [{file, ruta}]
function agregar(files) {
  for (const f of files) seleccion.push({file: f, ruta: f.webkitRelativePath || f._ruta || f.name});
  $('nSel').textContent = `${seleccion.length} archivos seleccionados`;
}
$('fArch').onchange = e => agregar(e.target.files);
$('fCarp').onchange = e => agregar(e.target.files);
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
  $('msgCarga').innerHTML = '⏳ Procesando…';
  try {
    const r = await api('/api/lotes', {method: 'POST', body: fd});
    seleccion = []; $('nSel').textContent = '0 archivos seleccionados';
    $('msgCarga').innerHTML = `<span class="ok">✔ Lote ${esc(r.id)} procesado. ${r.duplicados_eliminados} registros repetidos entre fuentes eliminados.</span>`;
    $('resCarga').innerHTML = detalleLote(r);
  } catch (e) { $('msgCarga').innerHTML = `<span class="err">✖ ${esc(e.message)}</span>`; }
};
function detalleLote(r) {
  return `<h3>Índice hospital-mes</h3>${tabla(r.indice)}
    <h3>Remisiones (corrija hospital, municipio, mes o EPS y guarde)</h3>${tablaRemisiones(r)}
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

// ---------------------------------------------------------------- 2. lotes
async function cargarLotes() {
  const l = await api('/api/lotes');
  $('listaLotes').innerHTML = tabla(l.map(x => ({...x, hospitales: x.hospitales.join(', '), periodos: x.periodos.join(', ')})))
    + l.map(x => `<button class="sec2" onclick="verLote('${esc(x.id)}')">Ver ${esc(x.id)}</button> `).join('');
}
async function verLote(id) { $('detLote').innerHTML = detalleLote(await api('/api/lotes/' + id)); }

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
  try { const r = await api('/api/maestros/' + nombre, {method: 'POST', body: fd});
    $('msgMae').innerHTML = `<pre class="ok">${esc(JSON.stringify(r, null, 1))}</pre>`; cargarMaestros();
  } catch (e) { $('msgMae').innerHTML = `<span class="err">✖ ${esc(e.message)}</span>`; }
}

// ---------------------------------------------------------------- 4. análisis
async function listarParaAnalisis() {
  const l = await api('/api/lotes');
  $('selLotes').innerHTML = l.length ? l.map(x => `<label class="inline"><input type="checkbox" value="${esc(x.id)}" checked>
    ${esc(x.id)} · ${esc(x.hospitales.join(', '))} · ${esc(x.periodos.join(', '))}</label><br>`).join('') : '<p class="nota">Primero cargue RIPS.</p>';
}
function mostrarResultado(r) {
  const k = r.indicadores.capita, i = r.indicadores;
  const kp = [['Costo CAPITA total', FM(k.total)], ['Promedio/mes', FM(k.promedio_mes)], ['Crecimiento reciente', nf(k.crecimiento_pct, 1) + ' %'],
    ['Pacientes (suma mensual)', nf(k.pacientes_suma)], ['Asistencias', nf(k.asistencias)], ['Costo/usuario', F(k.costo_usuario_prom)],
    ['EVENTO total', FM(i.evento_total)], ['Migrables/mes', FM(i.migrables_mes)], ['Hallazgos ALTA', nf(i.hallazgos_alta)], ['Valor en riesgo', FM(i.valor_en_riesgo)]];
  $('resAn').innerHTML = `<div class="kpis">${kp.map(x => `<div class="kc"><div class="lb">${x[0]}</div><div class="vl">${x[1]}</div></div>`).join('')}</div>
    ${r.advertencias.map(a => `<p class="nota err">⚠ ${esc(a)}</p>`).join('')}
    <p><a href="/api/analisis/${esc(r.id)}/informe.xlsx">⬇ Informe Excel</a> · <a href="/api/analisis/${esc(r.id)}/informe.pdf">⬇ Informe PDF</a> ·
       <a href="/api/analisis/${esc(r.id)}/estado.json">⬇ Guardar estado (JSON)</a></p>
    <h3>Detalle mensual CAPITA</h3>${tabla(r.detalle_mensual)}<h3>Costos por grupo</h3>${tabla(r.capita_grupos)}
    <h3>Escenarios</h3>${tabla(r.escenarios)}<h3>Top 20 EVENTO</h3>${tabla(r.top20_evento, ['n', 'codigo', 'descripcion', 'frecuencia', 'valor', 'valor_unitario', 'aplica_capita', 'observacion'])}
    <h3>Hallazgos (50 primeros)</h3>${tabla(r.hallazgos)}`;
}
$('bAnalizar').onclick = async () => {
  const lotes = [...$('selLotes').querySelectorAll('input:checked')].map(i => i.value);
  if (!lotes.length) return;
  $('msgAn').innerHTML = '⏳ Analizando…';
  try { const r = await api('/api/analisis', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({lotes, solo_eps_objetivo: $('soloNeps').checked})});
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
  const filas = Object.keys(r.control).map(k => ({indicador: k, control: r.control[k],
    referencia: r.referencia[k] ?? r.escenarios[{capita_promedio_mes: 'ESC.1 – CAPITA ACTUAL', capita_ampliada_mes: 'ESC.2 – CAPITA AMPLIADA',
      capita_ajustada_mes: 'ESC.3 – CAPITA DEMANDA REAL', pgp2_mes: 'PGP-2 UPC + EVENTO'}[k]] ?? null}));
  $('resRef').innerHTML = `<h3>Totales de control vs recálculo</h3>${tabla(filas)}<h3>Participación por grupo (FINAL)</h3>
    ${tabla(Object.entries(r.grupos).map(([g, p]) => ({grupo: g, participacion_pct: p})))}`;
}
