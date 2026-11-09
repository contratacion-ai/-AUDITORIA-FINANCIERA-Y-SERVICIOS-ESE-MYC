from django import forms

from .models import Auditoria, Hallazgo


class AuditoriaForm(forms.ModelForm):
    class Meta:
        model = Auditoria
        fields = ["nombre", "tipo", "entidad", "periodo_inicio", "periodo_fin", "responsable", "objetivo", "estado"]
        widgets = {
            "periodo_inicio": forms.DateInput(attrs={"type": "date"}),
            "periodo_fin": forms.DateInput(attrs={"type": "date"}),
            "objetivo": forms.Textarea(attrs={"rows": 3}),
        }


class HallazgoGestionForm(forms.ModelForm):
    class Meta:
        model = Hallazgo
        fields = ["estado", "prioridad", "responsable", "observaciones"]
        widgets = {"observaciones": forms.Textarea(attrs={"rows": 3})}


class HallazgoManualForm(forms.ModelForm):
    class Meta:
        model = Hallazgo
        fields = ["tipo", "titulo", "descripcion", "factura", "sede", "hospital", "mes", "servicio",
                  "valor_diferencia", "prioridad", "responsable", "accion_recomendada"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3}),
                   "accion_recomendada": forms.Textarea(attrs={"rows": 2})}
