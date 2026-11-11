"""Páginas de gestión: cargar datos, historial, plantillas, informes, catálogo y administración."""
from __future__ import annotations

import datetime as dt
import io
from pathlib import Path

import pandas as pd
import streamlit as st

from . import analisis, auth, catalogo, cargues, config, db, esquema, informe, plantillas
from .config import COMBUSTIBLE, DESPLAZAMIENTOS, ITEMS, MANTENIMIENTO, MESES, SERVICIOS
from .extraccion.comunes import ADVERTENCIA, ERROR, INFO, nombre_periodo, periodo_anterior, periodo_de_fecha
from .extraccion.lectura import EXTENSIONES
from .ui_comun import ANCHO, cargar, numero, pesos, tabla, version_datos
from .validacion import Opciones

AYUDA_ITEM = {
    SERVICIOS: "Cuadros de energía o acueducto (una fila por cuenta y los meses en columnas), la plantilla de "
               "servicios públicos, o facturas en PDF.",
    COMBUSTIBLE: "Exportación del formulario de tanqueos del parque automotor, control de combustible de plantas "
                 "eléctricas, cuadro de desplazamientos (kilómetros) o la plantilla de combustible.",
    MANTENIMIENTO: "Cuadro de costos de mantenimiento (por vehículo y mes, una hoja por sede) o la plantilla de "
                   "mantenimientos.",
}


# --------------------------------------------------------------------------- #
# Cargar datos
# --------------------------------------------------------------------------- #
def pagina_cargar(u):
    st.title("Cargar datos")
    st.caption("Paso 1: elija el ítem, el periodo y el archivo. Paso 2: revise lo que se interpretó y lo que no. "
               "Paso 3: confirme. Nada se guarda hasta que usted confirma.")
    hoy = dt.date.today()
    c1, c2, c3 = st.columns([2, 1.2, 1])
    item = c1.radio("Ítem", list(ITEMS), format_func=ITEMS.get, horizontal=True)
    c1.caption(AYUDA_ITEM[item])
    pdef = periodo_anterior(periodo_de_fecha(hoy))
    mes = c2.selectbox("Mes al que corresponde", range(1, 13), index=int(pdef[5:7]) - 1,
                       format_func=lambda m: MESES[m - 1].capitalize())
    anio = c3.number_input("Año", 2018, hoy.year, int(pdef[:4]))
    periodo = f"{anio:04d}-{mes:02d}"

    alcance = st.radio("¿Qué se carga del archivo?", ["periodo", "todos"], horizontal=True,
                       format_func={"periodo": f"Solo {nombre_periodo(periodo)}",
                                    "todos": "Todos los periodos que trae el archivo (archivo acumulado)"}.get,
                       help="El periodo se toma del contenido (mes de la factura o fecha del suministro), no de la "
                            "fecha en que se recibe el archivo.")
    desde = hasta = None
    if alcance == "periodo" and item != SERVICIOS:
        with st.expander("El corte de este periodo no coincide con el mes calendario"):
            st.caption("Indique las fechas de corte: todo lo que esté dentro del rango se asigna a "
                       f"{nombre_periodo(periodo)}, aunque la fecha caiga en el mes vecino.")
            usar = st.checkbox("Usar fechas de corte")
            d1, d2 = st.columns(2)
            ini = dt.date(anio, mes, 1)
            fin = (ini.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
            desde_i = d1.date_input("Desde", ini, format="DD/MM/YYYY", disabled=not usar)
            hasta_i = d2.date_input("Hasta", fin, format="DD/MM/YYYY", disabled=not usar)
            if usar:
                desde, hasta = desde_i, hasta_i

    archivo = st.file_uploader("Archivo", type=[e.strip(".") for e in EXTENSIONES],
                               help="Excel (.xlsx, .xls), CSV o PDF. Máximo 50 MB.")
    if archivo and st.button("Leer y validar", type="primary"):
        op = Opciones(periodo=periodo, alcance=alcance, corte_desde=desde, corte_hasta=hasta, hospital=u.ambito, hoy=hoy)
        with st.spinner("Leyendo y validando…"):
            st.session_state["prep"] = cargues.preparar(archivo.name, archivo.getvalue(), item, op)
    prep = st.session_state.get("prep")
    if prep is None:
        return
    if not archivo or archivo.name != prep.archivo:
        st.session_state.pop("prep", None)
        return
    st.divider()
    _revision(u, prep)


def _revision(u, prep: cargues.Preparacion):
    st.subheader(f"Revisión de «{prep.archivo}»")
    n_err, n_adv = prep.conteo(ERROR), prep.conteo(ADVERTENCIA)
    valor = sum(d["valor"].sum() for i, d in prep.resultados.items() if i != DESPLAZAMIENTOS)
    k = st.columns(5)
    k[0].metric("Filas leídas", numero(sum(prep.filas_leidas.values())))
    k[1].metric("Filas que se cargan", numero(prep.total_validas))
    k[2].metric("Filas rechazadas", numero(sum(prep.rechazadas.values())))
    k[3].metric("Advertencias", numero(n_adv))
    k[4].metric("Valor a cargar", pesos(valor, True))
    if prep.valor_rechazado:
        st.error(f"Las filas rechazadas suman {pesos(prep.valor_rechazado)} que **no** entrarán al control hasta "
                 "corregir el archivo o el catálogo.")

    if prep.tablas:
        with st.expander("Qué se reconoció en el archivo"):
            for t in prep.tablas:
                nombre = ITEMS.get(t.item, "Desplazamientos (kilómetros recorridos)")
                st.markdown(f"**Hoja «{t.hoja}»** → {nombre}: {t.descripcion}.")
                if t.columnas_usadas:
                    st.caption("Columnas usadas: " + " · ".join(f"{v} → {k}" for k, v in t.columnas_usadas.items()))
                if t.columnas_ignoradas:
                    st.caption("Columnas que no se usan: " + ", ".join(t.columnas_ignoradas))

    pdf_texto = [t for t in prep.tablas if t.formato == "pdf_texto"]
    if pdf_texto:
        st.warning("La factura se leyó del texto del PDF. Corrija aquí lo que haga falta y pulse **Revalidar**.")
        t = pdf_texto[0]
        cols = ["periodo", "codigo", "concepto", "cantidad", "unidad", "valor", "total_a_pagar", "documento"]
        editado = st.data_editor(t.datos[cols], num_rows="dynamic", **ANCHO, column_config={
            "concepto": st.column_config.SelectboxColumn("servicio", options=esquema.SERVICIOS_OPCIONES)})
        if st.button("Revalidar"):
            for c in cols:
                t.datos[c] = editado[c].values if len(editado) == len(t.datos) else t.datos[c]
            cargues.validar_tablas(prep, prep.tablas, conservar=prep.inc_extraccion)
            st.rerun()

    inc = prep.incidencias_df()
    if len(inc):
        st.markdown(f"**Lo que no se pudo interpretar o requiere revisión** · {n_err} errores · {n_adv} advertencias "
                    f"· {prep.conteo(INFO)} notas")
        sev = st.multiselect("Mostrar", [ERROR, ADVERTENCIA, INFO], default=[ERROR, ADVERTENCIA, INFO],
                             label_visibility="collapsed")
        tabla(inc[inc["severidad"].isin(sev)].drop(columns=["archivo"]), height=min(360, 60 + 35 * len(inc)))
        buf = io.BytesIO()
        inc.to_excel(buf, index=False)
        st.download_button("Descargar el reporte de incidencias (Excel)", buf.getvalue(),
                           f"incidencias_{Path(prep.archivo).stem}.xlsx")
    else:
        st.success("El archivo se interpretó completo, sin errores ni advertencias.")

    if prep.total_validas == 0:
        st.error("No hay filas válidas para cargar. Corrija el archivo según el reporte y vuelva a intentarlo.")
        return

    nombres = db.consulta("SELECT sede_id, hospital, sede FROM sedes")
    for item, d in prep.resultados.items():
        d2 = d.merge(nombres, on="sede_id", how="left")
        titulo = ITEMS.get(item, "Desplazamientos")
        with st.expander(f"Vista previa de {titulo.lower()} · {len(d)} registros", expanded=True):
            if item == DESPLAZAMIENTOS:
                res = d2.groupby(["periodo", "hospital"]).agg(Viajes=("km", "size"), Km=("km", "sum")).reset_index()
                tabla(res.rename(columns={"periodo": "Periodo", "hospital": "Hospital"}), entero=("Viajes", "Km"))
            else:
                res = d2.groupby(["periodo", "hospital", "categoria"]).agg(
                    Registros=("valor", "size"), Valor=("valor", "sum")).reset_index()
                tabla(res.rename(columns={"periodo": "Periodo", "hospital": "Hospital", "categoria": "Categoría"}),
                      moneda=("Valor",), entero=("Registros",))
                cols = ["periodo", "fecha", "hospital", "sede", "categoria", "concepto", "referencia", "cantidad", "unidad",
                        "valor", "total_a_pagar", "hoja", "fila"]
                st.caption("Primeros 200 registros tal como quedarán guardados:")
                tabla(d2[cols].head(200), moneda=("valor", "total_a_pagar"), height=260)

    if len(prep.cargas_previas):
        ult = prep.cargas_previas.iloc[-1]
        st.warning(f"Este mismo archivo (contenido idéntico) ya fue cargado el {ult['fecha_carga']} por "
                   f"{ult['usuario']} (cargue #{ult['cargue_id']}).")
    modo = st.radio("Si ya existen datos vigentes de las mismas sedes y periodos", ["reemplazar", "complementar"],
                    horizontal=True, format_func={"reemplazar": "Reemplazarlos por los de este archivo (recomendado)",
                                                  "complementar": "Conservarlos y agregar estos (complemento)"}.get)
    if len(prep.reemplazos):
        r = prep.reemplazos
        ids = ", ".join(f"#{c}" for c in sorted(r["cargue_id"].unique()))
        if modo == "reemplazar":
            st.info(f"Se reemplazarán **{numero(r['registros'].sum())} registros vigentes** ({pesos(r['valor'].sum())}) "
                    f"de los cargues {ids}, en {r['periodo'].nunique()} periodo(s) y {r['sede_id'].nunique()} sede(s). "
                    "Los registros anteriores no se borran: quedan en el historial como «reemplazados».")
        else:
            st.warning(f"Ya hay {numero(r['registros'].sum())} registros vigentes de los cargues {ids} para las mismas "
                       "sedes y periodos. Al complementar, ambos quedan vigentes: verifique que no se dupliquen.")
    obs = st.text_input("Observación del cargue (opcional)", placeholder="Ej.: corte al 3 de noviembre, incluye ajuste de…")
    if st.button(f"Confirmar y guardar {numero(prep.total_validas)} registros", type="primary"):
        ids = cargues.confirmar(prep, u.usuario, modo, obs)
        st.session_state.pop("prep", None)
        st.session_state["aviso"] = ("Cargue guardado: " + ", ".join(f"#{i}" for i in ids)
                                     + ". Ya está disponible en el tablero y queda en el historial de cargues.")
        st.rerun()


# --------------------------------------------------------------------------- #
# Historial
# --------------------------------------------------------------------------- #
def pagina_historial(u):
    st.title("Historial de cargues")
    st.caption("Qué archivo ingresó, cuándo, quién lo cargó, a qué periodos corresponde y qué reemplazó.")
    car = cargues.listar(hospital=u.ambito)
    if car.empty:
        st.info("Aún no hay cargues.")
        return
    c1, c2 = st.columns(2)
    f_item = c1.multiselect("Ítem", sorted(car["item_nombre"].unique()))
    f_est = c2.multiselect("Estado", sorted(car["estado"].unique()))
    v = car
    if f_item:
        v = v[v["item_nombre"].isin(f_item)]
    if f_est:
        v = v[v["estado"].isin(f_est)]
    if v.empty:
        st.info("Ningún cargue coincide con el filtro.")
        return
    tabla(v[["cargue_id", "item_nombre", "archivo_nombre", "fecha_carga", "usuario", "ambito", "periodos", "estado",
             "reemplazo_de", "filas_cargadas", "filas_rechazadas", "advertencias", "valor_total"]].rename(columns={
                 "cargue_id": "Cargue", "item_nombre": "Ítem", "archivo_nombre": "Archivo", "fecha_carga": "Fecha de carga",
                 "usuario": "Usuario", "ambito": "Ámbito", "periodos": "Periodos", "estado": "Estado",
                 "reemplazo_de": "Reemplazó a", "filas_cargadas": "Cargadas", "filas_rechazadas": "Rechazadas",
                 "advertencias": "Advertencias", "valor_total": "Valor"}), moneda=("Valor",), height=320)

    st.subheader("Detalle de un cargue")
    cid = st.selectbox("Cargue", v["cargue_id"], format_func=lambda i: f"#{i} · {v.set_index('cargue_id').loc[i, 'archivo_nombre']}")
    if cid is None:
        return
    c = car.set_index("cargue_id").loc[cid]
    st.markdown(f"**#{cid} · {c['item_nombre']}** · {c['estado']} · cargado el {c['fecha_carga']} por **{c['usuario']}** "
                f"({c['ambito']}) · modo: {c['modo']} · registros: {c['vigentes']} vigentes, {c['reemplazados']} "
                f"reemplazados, {c['eliminados']} eliminados")
    st.caption(f"Huella SHA-256 del archivo: {c['archivo_sha256']}" + (f" · Observación: {c['observacion']}" if c["observacion"] else ""))
    c1, c2 = st.columns(2)
    with c1:
        inc = cargues.incidencias_de(int(cid))
        with st.expander(f"Incidencias registradas al cargar · {len(inc)}"):
            tabla(inc)
        ruta = Path(c["archivo_ruta"]) if c["archivo_ruta"] else None
        if ruta and ruta.exists():
            st.download_button("Descargar el archivo original", ruta.read_bytes(), c["archivo_nombre"])
    with c2:
        if c["vigentes"]:
            with st.form(f"eliminar_{cid}"):
                st.markdown("**Eliminar este cargue**")
                st.caption("Los registros dejan de estar vigentes pero se conservan en el historial como «eliminados».")
                motivo = st.text_input("Motivo (obligatorio)")
                restaurar = st.checkbox("Restaurar los datos que este cargue había reemplazado",
                                        disabled=not c["reemplazo_de"])
                seguro = st.checkbox("Confirmo que deseo eliminarlo")
                if st.form_submit_button("Eliminar cargue"):
                    if not seguro or not motivo.strip():
                        st.error("Escriba el motivo y marque la confirmación.")
                    else:
                        try:
                            st.session_state["aviso"] = cargues.eliminar(int(cid), u.usuario, motivo, restaurar, u.ambito)
                            st.rerun()
                        except (ValueError, PermissionError) as e:
                            st.error(str(e))
        else:
            st.caption("Este cargue ya no tiene registros vigentes.")
    if u.es_central:
        with st.expander("Bitácora de eventos"):
            tabla(cargues.bitacora().rename(columns=str.capitalize), height=320)


# --------------------------------------------------------------------------- #
# Plantillas e informes
# --------------------------------------------------------------------------- #
def pagina_plantillas(u):
    st.title("Plantillas de recolección")
    st.caption("Una plantilla por ítem, con la misma estructura para todas las sedes: listas desplegables, "
               "instrucciones y el catálogo de códigos de sede vigente.")
    hospital = u.ambito
    if u.es_central:
        hs = sorted(db.consulta("SELECT DISTINCT hospital FROM sedes WHERE activo=1")["hospital"])
        sel = st.selectbox("Catálogo incluido en la plantilla", ["Todas las sedes"] + hs)
        hospital = None if sel == "Todas las sedes" else sel
    cols = st.columns(3)
    for col, (item, nombre) in zip(cols, plantillas.NOMBRE_ARCHIVO.items()):
        with col:
            st.subheader(ITEMS[item])
            st.download_button("Descargar plantilla", plantillas.generar(item, hospital), nombre, key=f"pl_{item}", **ANCHO)
            campos = pd.DataFrame([{"Columna": c.titulo, "Obligatoria": "Sí" if c.obligatorio else "No"}
                                   for c in esquema.PLANTILLAS[item]])
            tabla(campos, height=460)


def pagina_informes(u):
    st.title("Informe en Excel")
    st.caption("Libro con una hoja de resumen, una por ítem, una por hospital (con sus sedes), alertas, análisis, "
               "detalle de registros y trazabilidad de cargues.")
    df, _, _ = cargar(version_datos(), u.ambito)
    if df.empty:
        st.info("Aún no hay datos cargados.")
        return
    pers = analisis.periodos(df)
    c1, c2 = st.columns(2)
    if len(pers) > 1:
        desde, hasta = c1.select_slider("Periodos", options=pers, value=(pers[0], pers[-1]), format_func=nombre_periodo)
    else:
        desde = hasta = pers[0]
    hospital = u.ambito
    if u.es_central:
        sel = c2.selectbox("Alcance", ["Consolidado de la ESE"] + sorted(df["hospital"].unique()))
        hospital = None if sel == "Consolidado de la ESE" else sel
    if st.button("Generar informe", type="primary"):
        with st.spinner("Generando…"):
            st.session_state["informe"] = (informe.generar(desde=desde, hasta=hasta, hospital=hospital),
                                           f"Informe_control_{desde}_a_{hasta}.xlsx")
    if "informe" in st.session_state:
        datos, nombre = st.session_state["informe"]
        st.download_button(f"Descargar {nombre}", datos, nombre)


# --------------------------------------------------------------------------- #
# Catálogo y administración (solo área central)
# --------------------------------------------------------------------------- #
def pagina_catalogo(u):
    st.title("Catálogo de sedes, códigos y vehículos")
    st.caption("Aquí se define a qué sede y hospital corresponde cada código interno, cuenta de servicio o placa. "
               "Para actualizarlo: descargue el Excel, edítelo y súbalo de nuevo. No requiere cambios en el programa.")
    t = catalogo.tablas_actuales()
    pend = sum((d["validado"] == "NO").sum() for d in t.values())
    if pend:
        st.warning(f"Hay {pend} filas marcadas «validado = NO»: fueron deducidas de los archivos y deben confirmarse.")
    st.download_button("Descargar catálogo vigente (Excel)", catalogo.exportar_excel(), "Catalogo_sedes_codigos.xlsx")
    tabs = st.tabs([f"{n} · {len(d)}" for n, d in t.items()])
    for tab, (nombre, d) in zip(tabs, t.items()):
        with tab:
            st.caption(catalogo.AYUDA_HOJAS[nombre])
            tabla(d, height=360)
    st.subheader("Actualizar catálogo")
    nuevo = st.file_uploader("Excel del catálogo (hojas Sedes, Codigos y Vehiculos)", type=["xlsx"])
    if nuevo:
        tablas, errores = catalogo.leer_excel(nuevo.getvalue())
        if errores:
            st.error("El archivo tiene errores y no se puede aplicar:")
            st.markdown("\n".join(f"- {e}" for e in errores))
            return
        actuales = set(t["Sedes"]["sede_id"])
        nuevas = set(tablas["Sedes"]["sede_id"].astype(str).str.strip())
        st.info(f"El archivo trae {len(tablas['Sedes'])} sedes, {len(tablas['Codigos'])} códigos y "
                f"{len(tablas['Vehiculos'])} vehículos. Sedes nuevas: {len(nuevas - actuales)}. Sedes que quedarán "
                f"inactivas por no venir en el archivo: {len(actuales - nuevas)}.")
        if st.button("Aplicar catálogo", type="primary"):
            st.session_state["aviso"] = catalogo.aplicar(tablas, u.usuario, nuevo.name)
            st.rerun()
        st.caption("Las filas rechazadas antes por código no reconocido entran al volver a cargar su archivo.")


def pagina_admin(u):
    st.title("Administración")
    t1, t2 = st.tabs(["Usuarios", "Parámetros de validación y alertas"])
    with t1:
        us = auth.listar()
        tabla(us.assign(activo=us["activo"].map({1: "Sí", 0: "No"})).rename(columns=str.capitalize))
        c1, c2 = st.columns(2)
        with c1, st.form("nuevo_usuario", clear_on_submit=True):
            st.markdown("**Crear usuario**")
            usuario = st.text_input("Usuario")
            nombre = st.text_input("Nombre completo")
            rol = st.radio("Rol", ["sede", "central"], horizontal=True,
                           format_func={"sede": "Sede (solo su hospital)", "central": "Central (toda la ESE)"}.get)
            hs = sorted(db.consulta("SELECT DISTINCT hospital FROM sedes WHERE activo=1")["hospital"])
            hospital = st.selectbox("Hospital o centro (para el rol sede)", hs)
            clave = st.text_input("Contraseña inicial", type="password")
            if st.form_submit_button("Crear"):
                try:
                    auth.crear(usuario, nombre, clave, rol, hospital, u.usuario)
                    st.session_state["aviso"] = f"Usuario «{usuario.strip().lower()}» creado."
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        with c2, st.form("gestion_usuario"):
            st.markdown("**Restablecer contraseña o desactivar**")
            quien = st.selectbox("Usuario", us["usuario"])
            nueva = st.text_input("Nueva contraseña (deje vacío para no cambiarla)", type="password")
            estado = st.radio("Estado", ["Activo", "Inactivo"], horizontal=True)
            if st.form_submit_button("Guardar"):
                try:
                    if nueva:
                        auth.cambiar_clave(quien, nueva, u.usuario)
                    auth.activar(quien, estado == "Activo", u.usuario)
                    st.session_state["aviso"] = f"Usuario «{quien}» actualizado."
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    with t2:
        st.caption("Umbrales que usan la validación de archivos y las alertas. Se aplican de inmediato.")
        p = db.consulta("SELECT clave, valor, descripcion FROM parametros ORDER BY rowid")
        ed = st.data_editor(p, hide_index=True, disabled=["clave", "descripcion"], **ANCHO,
                            column_config={"clave": "Parámetro", "valor": st.column_config.NumberColumn("Valor"),
                                           "descripcion": "Para qué sirve"})
        if st.button("Guardar parámetros"):
            with db.transaccion() as con:
                for f in ed.itertuples():
                    con.execute("UPDATE parametros SET valor=? WHERE clave=?", (float(f.valor), f.clave))
                db.registrar_evento(con, u.usuario, "parametros_actualizados", "")
            st.session_state["aviso"] = "Parámetros guardados."
            st.rerun()
        st.caption(f"Base de datos: {config.RUTA_BD} · Archivos originales: {config.DIR_ARCHIVOS}. "
                   "Para la copia de seguridad basta con copiar la carpeta de datos completa.")
