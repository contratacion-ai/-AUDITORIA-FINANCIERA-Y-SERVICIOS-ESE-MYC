"""Sincroniza las carpetas de Google Drive registradas y actualiza la base (sin abrir el navegador)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entorno  # noqa: E402

if __name__ == "__main__":
    entorno.preparar()
    entorno.registro()
    try:
        from rips_auditoria import fuentes
        if not fuentes.leer()["carpetas"]:
            entorno.mensaje("Auditoría RIPS", "No hay carpetas de Drive registradas.\n"
                            "Ábralas en la aplicación: pestaña 'Base de datos y Drive'.")
            sys.exit(0)
        r = fuentes.sincronizar()
        texto = (f"Carpetas de mes actualizadas: {len(r['procesadas'])}\nSin cambios: {r['sin_cambios']}\n"
                 f"Con error: {len(r['errores'])}")
        for e in r["errores"][:5]:
            texto += f"\n  · {e['carpeta']}: {e['error']}"
        if r["ausentes"]:
            texto += f"\n\nYa no están en Drive (revise si debe limpiarlas): {len(r['ausentes'])}"
        entorno.mensaje("Auditoría RIPS · Sincronización", texto)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        entorno.mensaje("Auditoría RIPS", f"Error al sincronizar:\n{e}")
