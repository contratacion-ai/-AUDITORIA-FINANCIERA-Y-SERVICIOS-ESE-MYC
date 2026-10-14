"""Genera las imágenes del asistente de instalación con la identidad de la ESE.

Uso: python instalador/recursos.py <carpeta_destino>

Produce (formatos que exige NSIS Modern UI 2):
  asistente.bmp   164 x 314  (páginas de bienvenida y finalización)
  encabezado.bmp  150 x 57   (encabezado de las demás páginas)
  icono.ico       16-256 px  (instalador, accesos directos y desinstalador)

Parte del logo oficial (rips_auditoria/web/static/logo_ese.png) y del
símbolo «MC» (icono_ese.png). Colores: verde #18A020 y vino #A01038.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = RAIZ / "rips_auditoria" / "web" / "static"
VERDE, VERDE_OSC, VINO = (24, 160, 32), (14, 107, 22), (160, 16, 56)


def _fuente(tam, negrita=True):
    for nombre in ("DejaVuSans-Bold.ttf" if negrita else "DejaVuSans.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"):
        for base in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation", "C:/Windows/Fonts"):
            p = Path(base) / nombre
            if p.exists():
                return ImageFont.truetype(str(p), tam)
    return ImageFont.load_default()


def _sobre_blanco(img: Image.Image) -> Image.Image:
    fondo = Image.new("RGB", img.size, "white")
    fondo.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
    return fondo


def _centrado(draw, y, texto, fuente, color, ancho):
    w = draw.textlength(texto, font=fuente)
    draw.text(((ancho - w) / 2, y), texto, font=fuente, fill=color)


def asistente(destino: Path) -> None:
    w, h = 164, 314
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    mc = Image.open(ESTATICOS / "icono_ese.png").convert("RGB").resize((132, 132), Image.LANCZOS)
    im.paste(mc, ((w - 132) // 2, 18))
    logo = Image.open(ESTATICOS / "logo_ese.png").convert("RGBA")
    logo = logo.resize((148, round(logo.height * 148 / logo.width)), Image.LANCZOS)
    im.paste(_sobre_blanco(logo), (8, 158))
    # banda inferior verde con filete vino
    d.rectangle([0, 214, w, h], fill=VERDE)
    d.rectangle([0, 214, w, 220], fill=VINO)
    _centrado(d, 236, "CARGUE RIPS", _fuente(17), "white", w)
    _centrado(d, 262, "Res. 3374 de 2000", _fuente(11, False), "white", w)
    _centrado(d, 280, "Auditoría de Facturación", _fuente(11, False), "white", w)
    im.save(destino / "asistente.bmp")


def encabezado(destino: Path) -> None:
    w, h = 150, 57
    im = Image.new("RGB", (w, h), "white")
    logo = Image.open(ESTATICOS / "logo_ese.png").convert("RGBA")
    escala = min((w - 8) / logo.width, (h - 12) / logo.height)
    logo = logo.resize((round(logo.width * escala), round(logo.height * escala)), Image.LANCZOS)
    im.paste(_sobre_blanco(logo), ((w - logo.width) // 2, (h - 6 - logo.height) // 2))
    d = ImageDraw.Draw(im)
    d.rectangle([0, h - 5, w * 0.62, h], fill=VERDE)
    d.rectangle([w * 0.62, h - 5, w, h], fill=VINO)
    im.save(destino / "encabezado.bmp")


def icono(destino: Path) -> None:
    (destino / "icono.ico").write_bytes((ESTATICOS / "icono_ese.ico").read_bytes())


if __name__ == "__main__":
    salida = Path(sys.argv[1] if len(sys.argv) > 1 else "build/recursos")
    salida.mkdir(parents=True, exist_ok=True)
    asistente(salida)
    encabezado(salida)
    icono(salida)
    print(f"Recursos del instalador en {salida}")
