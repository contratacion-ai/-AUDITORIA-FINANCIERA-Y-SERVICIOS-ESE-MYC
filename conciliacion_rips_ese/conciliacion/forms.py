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


class ConciliacionForm(forms.ModelForm):
    class Meta:
        model = Conciliacion
        fields = ["auditoria", "nombre", "descripcion", "separador_decimal", "ignorar_ceros_factura",
                  "tolerancia_valor", "top_n"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 2})}


AUTO = "AUTO"


def tipo_automatico(archivo) -> str:
    """Clasifica el archivo por su contenido (no por la extensión). Ver ``formatos.clasificar_para_carga``."""
    from rips_conciliacion.formatos import clasificar_para_carga

    contenido = archivo.read()
    archivo.seek(0)
    return clasificar_para_carga(archivo.name, contenido)


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
