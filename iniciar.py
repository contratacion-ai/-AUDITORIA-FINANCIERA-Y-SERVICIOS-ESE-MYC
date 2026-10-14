"""Lanzador del aplicativo (lo usa el instalador de Windows; también funciona con «python iniciar.py»).

- Guarda la base de datos en %LOCALAPPDATA%\\AuditoriaESE_MyC\\data (se conserva al actualizar o reinstalar).
- Si el aplicativo ya está abierto, solo abre el navegador; si el puerto 8000 lo usa otro programa, prueba 8001…8010.
"""
import json, os, socket, sys, threading, urllib.request, webbrowser

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
if BASE not in sys.path: sys.path.insert(0, BASE)
if os.name == "nt" and "AUDIT_DATA_DIR" not in os.environ:
    os.environ["AUDIT_DATA_DIR"] = os.path.join(os.environ.get("LOCALAPPDATA", BASE), "AuditoriaESE_MyC", "data")

def _nuestro(puerto):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/health", timeout=2) as r: return json.load(r).get("ok") is True
    except Exception: return False

def _libre(puerto):
    with socket.socket() as s:
        try: s.bind(("127.0.0.1", puerto)); return True
        except OSError: return False

def main():
    for puerto in range(int(os.environ.get("AUDIT_PORT", "8000")), 8011):
        if _nuestro(puerto):
            webbrowser.open(f"http://localhost:{puerto}"); return
        if _libre(puerto): break
    else:
        input("No hay un puerto libre entre 8000 y 8010. Cierre otros programas y vuelva a intentar. [Enter]"); return
    url = f"http://localhost:{puerto}"
    print("=" * 66, "  AUDITORIA DE FACTURACION, RIPS, COSTOS Y MEDICAMENTOS",
          "  ESE Departamental de Primer Nivel Moreno y Clavijo", "=" * 66,
          f"  Aplicativo en: {url}", f"  Datos en:      {os.environ.get('AUDIT_DATA_DIR', os.path.join(BASE, 'data'))}",
          "  NO cierre esta ventana mientras use el aplicativo.", "  Para salir: cierre esta ventana (o Ctrl+C).", "=" * 66, sep="\n")
    threading.Timer(2.5, webbrowser.open, args=(url,)).start()
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=puerto, log_level="warning")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        print(f"\n[!] Error al iniciar: {type(e).__name__}: {e}"); input("Presione Enter para cerrar…")
