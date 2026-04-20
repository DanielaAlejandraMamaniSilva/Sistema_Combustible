from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from .models import Asignacion, Vehiculo, Usuario

class UsuarioCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = Usuario
        fields = ('username', 'first_name', 'last_name', 'email', 'rol', 'ci', 'licencia_conducir', 'vencimiento_licencia')
        widgets = {
            'rol': forms.Select(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none focus:ring-2 focus:ring-primary'}),
            'vencimiento_licencia': forms.DateInput(attrs={'type': 'date', 'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            if field not in ['rol', 'vencimiento_licencia']:
                self.fields[field].widget.attrs.update({
                    'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none focus:ring-2 focus:ring-primary'
                })

class VehiculoForm(forms.ModelForm):
    class Meta:
        model = Vehiculo
        fields = ['placa', 'marca', 'modelo', 'tipo', 'color', 'nro_chasis', 'kilometraje_actual', 'estado', 'tipo_combustible']
        widgets = {
            'estado': forms.Select(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none focus:ring-2 focus:ring-primary'}),
            'tipo_combustible': forms.Select(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none focus:ring-2 focus:ring-primary'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            if field not in ['estado', 'tipo_combustible']:
                self.fields[field].widget.attrs.update({
                    'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none focus:ring-2 focus:ring-primary'
                })

class AsignacionForm(forms.ModelForm):
    class Meta:
        model = Asignacion
        fields = ['vehiculo', 'chofer', 'nro_memorandum', 'documento_acta']
        widgets = {
            'vehiculo': forms.Select(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'}),
            'chofer': forms.Select(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'}),
            'nro_memorandum': forms.TextInput(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none', 'placeholder': 'Ej: MEMO/ACT/001/2024'}),
            'documento_acta': forms.FileInput(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['chofer'].queryset = Usuario.objects.filter(rol='chofer')