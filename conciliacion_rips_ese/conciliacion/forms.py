from django import forms

from .models import ArchivoCargado, Conciliacion


class MultiplesArchivosInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiplesArchivosField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultiplesArchivosInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        limpiar = super().clean
        if isinstance(data, (list, tuple)):
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


class CargaArchivosForm(forms.Form):
    tipo = forms.ChoiceField(choices=ArchivoCargado.Tipo.choices)
    archivos = MultiplesArchivosField(label="Archivos")
    sede = forms.CharField(required=False, help_text="Opcional: asigna la sede a todo lo cargado")
    hospital = forms.CharField(required=False)
    ambito = forms.CharField(required=False, help_text="Solo facturación: p. ej. AMBULATORIO, URGENCIAS")

    def clean(self):
        datos = super().clean()
        tipo = datos.get("tipo")
        for archivo in datos.get("archivos") or []:
            validar_extension(tipo, archivo.name)
        return datos


class ReemplazoForm(forms.Form):
    archivo = forms.FileField()


class ActualizarArchivoForm(forms.ModelForm):
    class Meta:
        model = ArchivoCargado
        fields = ["sede", "hospital", "ambito", "activo"]
