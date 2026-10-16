import datetime as dt
from decimal import Decimal

from django import forms

from .models import Ambulancia, CodigoTraslado, Consumo, Sede, TarifaRuta, Traslado


class VersionForm(forms.ModelForm):
    """Incluye la versión leída para el bloqueo optimista."""

    version_leida = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["version_leida"].initial = self.instance.version
        for f in self.fields.values():
            if not isinstance(f.widget, (forms.CheckboxInput, forms.HiddenInput)):
                f.widget.attrs.setdefault("class", "campo")


class TrasladoForm(VersionForm):
    km_recorridos = forms.DecimalField(
        required=False,
        min_value=0,
        label="Km recorridos (ida y vuelta)",
        help_text="Déjelo vacío para calcularlo con la ruta o la cantidad facturada",
    )

    class Meta:
        model = Traslado
        fields = [
            "fecha_servicio", "sede", "ambulancia", "ruta", "codigo_cups", "descripcion", "cantidad",
            "valor_unitario", "valor_facturado", "km_recorridos", "ambito", "unidad_funcional", "contrato",
            "eps_nombre", "numero_factura", "nro_ingreso", "autorizacion",
        ]
        widgets = {"fecha_servicio": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ambulancia"].queryset = Ambulancia.objects.exclude(estado=Ambulancia.FUERA).select_related("sede")
        self.fields["ruta"].queryset = TarifaRuta.objects.filter(activa=True)
        self.fields["codigo_cups"].widget = forms.Select(
            choices=[(c.codigo, f"{c.codigo} - {c.descripcion[:60]}") for c in CodigoTraslado.objects.filter(activo=True)],
            attrs={"class": "campo"},
        )
        self.fields["ambito"].help_text = "Ámbito RIPS: Urgencias, Hospitalario, Ambulatorio, ..."

    def clean(self):
        datos = super().clean()
        amb = datos.get("ambulancia")
        if amb and datos.get("sede") and amb.sede_id != datos["sede"].pk:
            self.add_error("ambulancia", f"La ambulancia {amb} pertenece a {amb.sede}; cambie la sede o la placa.")
        return datos


class AsignarAmbulanciaForm(forms.Form):
    ambulancia = forms.ModelChoiceField(queryset=Ambulancia.objects.exclude(estado=Ambulancia.FUERA), required=False)
    traslados = forms.CharField(widget=forms.HiddenInput)


class ConsumoForm(VersionForm):
    class Meta:
        model = Consumo
        fields = [
            "ambulancia", "fecha", "tipo", "descripcion", "cantidad", "unidad", "valor", "meses_prorrateo", "odometro",
            "documento_soporte", "proveedor",
        ]
        widgets = {"fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def clean(self):
        datos = super().clean()
        if datos.get("tipo") == Consumo.COMBUSTIBLE and not datos.get("unidad"):
            datos["unidad"] = "gal"
        if datos.get("valor") is not None and datos["valor"] < Decimal(0):
            self.add_error("valor", "El valor no puede ser negativo")
        return datos


class ImportarForm(forms.Form):
    TIPOS = [
        ("facturacion", "Facturación de traslados del HIS (.xlsx)"),
        ("rips", "RIPS JSON (Res. 2275 de 2023)"),
        ("consumos", "Consumos por ambulancia (.csv)"),
        ("tarifas", "Manual tarifario - Anexo 3 Traslados (.xlsx)"),
        ("ceco", "COSTOS AMBULANCIAS - centro de costos (.xlsx)"),
    ]
    tipo = forms.ChoiceField(choices=TIPOS)
    archivo = forms.FileField()
    vigencia = forms.IntegerField(initial=2026, required=False, help_text="Solo para tarifas")


class CampoMes(forms.DateField):
    """Mes en formato AAAA-MM (input type=month)."""

    widget = forms.DateInput(attrs={"type": "month", "class": "campo"}, format="%Y-%m")
    input_formats = ["%Y-%m", "%Y-%m-%d"]

    def to_python(self, value):
        fecha = super().to_python(value)
        return fecha.replace(day=1) if fecha else fecha


class ArchivosMultiples(forms.ClearableFileInput):
    allow_multiple_selected = True


class CampoArchivos(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", ArchivosMultiples(attrs={"accept": ".json,.txt,.zip"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if isinstance(data, (list, tuple)):
            return [super(CampoArchivos, self).clean(d, initial) for d in data]
        return [super().clean(data, initial)]


def _mes_anterior():
    hoy = dt.date.today().replace(day=1)
    return (hoy - dt.timedelta(days=1)).replace(day=1)


class CargaFacturacionForm(forms.Form):
    mes = CampoMes(label="Mes de la facturación", initial=_mes_anterior)
    sede = forms.ModelChoiceField(
        Sede.objects.filter(activa=True), required=False, label="Hospital",
        empty_label="Todos los hospitales del archivo",
        help_text="Si elige un hospital, solo se cargan sus líneas",
    )
    archivo = forms.FileField(label="Reporte de facturación de traslados (.xlsx)", widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}))


class CargaRipsForm(forms.Form):
    mes = CampoMes(label="Mes de los RIPS", initial=_mes_anterior)
    sede = forms.ModelChoiceField(Sede.objects.filter(activa=True), label="Hospital")
    archivos = CampoArchivos(
        label="Archivos RIPS",
        help_text="JSON (Res. 2275 de 2023), archivos planos AF/AT/AP (Res. 3374 de 2000) o un ZIP con ellos. Puede seleccionar varios.",
    )


class CargaConsumosForm(forms.Form):
    archivo = forms.FileField(label="Archivo (.xlsx o .csv)", widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.csv"}))
