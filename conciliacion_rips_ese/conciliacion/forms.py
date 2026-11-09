from django import forms

from .models import ArchivoCargado, Conciliacion


class MultiplesArchivosInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiplesArchivosField(forms.FileField):
    default_error_messages = {"required": "Seleccione al menos un archivo para cargar."}

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultiplesArchivosInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        limpiar = super().clean
        if isinstance(data, (list, tuple)):
            if not data:
                raise forms.ValidationError(self.error_messages["required"], code="required")
            return [limpiar(d, initial) for d in data]
        return [limpiar(data, initial)]


EXTENSIONES = {
    ArchivoCargado.Tipo.RIPS: {".txt", ".json", ".zip", ".rar"},
    ArchivoCargado.Tipo.FACTURACION: {".csv", ".txt", ".xlsx", ".xlsm", ".xls"},
    ArchivoCargado.Tipo.CATALOGO: {".csv", ".txt", ".xlsx", ".xls"},
    ArchivoCargado.Tipo.EQUIVALENCIAS: {".csv", ".txt", ".xlsx", ".xls"},
}


def validar_extension(tipo, nombre):
    from pathlib import PurePosixPath

    ext = PurePosixPath(nombre).suffix.lower()
    if ext not in EXTENSIONES[tipo]:
        raise forms.ValidationError(
            f"'{nombre}': extensión {ext or '(sin extensión)'} no permitida para {tipo}. "
            f"Permitidas: {', '.join(sorted(EXTENSIONES[tipo]))}"
        )


class ConciliacionForm(forms.ModelForm):
    class Meta:
        model = Conciliacion
        fields = ["auditoria", "nombre", "descripcion", "separador_decimal", "ignorar_ceros_factura",
                  "tolerancia_valor", "top_n"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 2})}


AUTO = "AUTO"


def tipo_automatico(archivo) -> str:
    """Clasifica un archivo por su nombre y contenido.

    ZIP/RAR/JSON -> RIPS. TXT -> RIPS si tiene estructura RIPS (3374/2275); si no, facturación delimitada.
    CSV/XLSX/XLS -> facturación, salvo que el nombre indique catálogo de sedes o equivalencias.
    """
    from pathlib import PurePosixPath

    from rips_conciliacion.deteccion import detectar
    from rips_conciliacion.modelos import VERSION_NO_DETERMINADA

    nombre = archivo.name
    ext = PurePosixPath(nombre).suffix.lower()
    base = PurePosixPath(nombre).stem.lower()
    if ext in (".zip", ".rar", ".json"):
        return ArchivoCargado.Tipo.RIPS
    if ext == ".txt":
        inicio = archivo.read(256 * 1024)
        archivo.seek(0)
        if detectar(nombre, inicio).version != VERSION_NO_DETERMINADA:
            return ArchivoCargado.Tipo.RIPS
        return ArchivoCargado.Tipo.FACTURACION
    if "catalogo" in base or "catálogo" in base or "sedes" in base:
        return ArchivoCargado.Tipo.CATALOGO
    if "equivalen" in base or "homolog" in base:
        return ArchivoCargado.Tipo.EQUIVALENCIAS
    return ArchivoCargado.Tipo.FACTURACION


class CargaArchivosForm(forms.Form):
    tipo = forms.ChoiceField(
        choices=[(AUTO, "Detectar automáticamente (recomendado)")] + list(ArchivoCargado.Tipo.choices),
        initial=AUTO,
    )
    archivos = MultiplesArchivosField(label="Archivos")
    sede = forms.CharField(required=False, help_text="Opcional: asigna la sede a todo lo cargado")
    hospital = forms.CharField(required=False)
    ambito = forms.CharField(required=False, help_text="Solo facturación: p. ej. AMBULATORIO, URGENCIAS")

    def clean(self):
        datos = super().clean()
        tipo = datos.get("tipo")
        clasificados = []
        for archivo in datos.get("archivos") or []:
            tipo_archivo = tipo_automatico(archivo) if tipo == AUTO else tipo
            validar_extension(tipo_archivo, archivo.name)
            clasificados.append((tipo_archivo, archivo))
        datos["clasificados"] = clasificados
        return datos


class ReemplazoForm(forms.Form):
    archivo = forms.FileField()


class ActualizarArchivoForm(forms.ModelForm):
    class Meta:
        model = ArchivoCargado
        fields = ["tipo", "sede", "hospital", "ambito", "activo"]


class InicioRapidoForm(forms.Form):
    nombre = forms.CharField(label="Nombre de la conciliación", max_length=200,
                             help_text="Por ejemplo: RIPS vs facturación enero 2025")
    auditoria = forms.ModelChoiceField(label="Auditoría a la que pertenece", required=False,
                                       queryset=None, empty_label="(Nueva auditoría)")
    nueva_auditoria = forms.CharField(label="Nombre de la nueva auditoría", required=False, max_length=200,
                                      help_text="Solo si eligió (Nueva auditoría). Si lo deja vacío se usa "
                                                "'Auditoría general <año>'.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from auditoria.models import Auditoria

        self.fields["auditoria"].queryset = Auditoria.objects.all()
