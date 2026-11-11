"""Aplicación web de control por sede y hospital - ESE Moreno y Clavijo.

Ejecutar:  streamlit run app.py
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from ese_control import auth, config, db
from ese_control.ui_gestion import (pagina_admin, pagina_cargar, pagina_catalogo, pagina_historial, pagina_informes,
                                    pagina_plantillas)
from ese_control.ui_tablero import pagina_alertas, pagina_tablero

st.set_page_config(page_title=f"Control de gastos · {config.ENTIDAD}", page_icon="🏥", layout="wide")
db.inicializar()


def _primer_usuario():
    st.title(f"Control de servicios, combustible y mantenimientos · {config.ENTIDAD}")
    st.info("Primera ejecución: cree el usuario del área administrativa central. Este paso solo aparece una vez.")
    with st.form("primer_usuario"):
        usuario = st.text_input("Usuario")
        nombre = st.text_input("Nombre completo")
        c1 = st.text_input("Contraseña (mínimo 8 caracteres)", type="password")
        c2 = st.text_input("Repita la contraseña", type="password")
        if st.form_submit_button("Crear usuario central", type="primary"):
            if c1 != c2:
                st.error("Las contraseñas no coinciden.")
            else:
                try:
                    auth.crear(usuario, nombre, c1, "central")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


def _ingreso():
    st.title(f"Control de servicios, combustible y mantenimientos · {config.ENTIDAD}")
    with st.form("ingreso"):
        usuario = st.text_input("Usuario")
        clave = st.text_input("Contraseña", type="password")
        if st.form_submit_button("Ingresar", type="primary"):
            u = auth.autenticar(usuario, clave)
            if u is None:
                st.error("Usuario o contraseña incorrectos.")
            else:
                st.session_state["usuario"] = u
                st.rerun()


def _guia(_u):
    st.markdown((Path(__file__).parent / "docs" / "GUIA_USO.md").read_text(encoding="utf-8"))


if not auth.hay_usuarios():
    _primer_usuario()
    st.stop()
if "usuario" not in st.session_state:
    _ingreso()
    st.stop()

u = st.session_state["usuario"]
paginas = {"Tablero de control": pagina_tablero, "Alertas y análisis": pagina_alertas, "Cargar datos": pagina_cargar,
           "Historial de cargues": pagina_historial, "Plantillas": pagina_plantillas, "Informe en Excel": pagina_informes}
if u.es_central:
    paginas |= {"Catálogo": pagina_catalogo, "Administración": pagina_admin}
paginas["Guía de uso"] = _guia

with st.sidebar:
    st.markdown(f"### {config.ENTIDAD}")
    st.caption("Control de servicios públicos, combustible y mantenimientos")
    pagina = st.radio("Menú", list(paginas), label_visibility="collapsed")
    st.divider()
    st.markdown(f"**{u.nombre}**")
    st.caption("Área administrativa central" if u.es_central else f"Sede: {u.hospital}")
    with st.expander("Cambiar mi contraseña"):
        with st.form("mi_clave", clear_on_submit=True):
            actual = st.text_input("Contraseña actual", type="password")
            nueva = st.text_input("Nueva contraseña", type="password")
            if st.form_submit_button("Cambiar"):
                if auth.autenticar(u.usuario, actual) is None:
                    st.error("La contraseña actual no es correcta.")
                else:
                    try:
                        auth.cambiar_clave(u.usuario, nueva, u.usuario)
                        st.success("Contraseña actualizada.")
                    except ValueError as e:
                        st.error(str(e))
    if st.button("Cerrar sesión"):
        st.session_state.clear()
        st.rerun()

if "aviso" in st.session_state:
    st.success(st.session_state.pop("aviso"))
paginas[pagina](u)
