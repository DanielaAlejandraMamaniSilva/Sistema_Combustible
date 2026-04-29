from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from .models import Asignacion, Vehiculo, Usuario, Bitacora, Viaje
from django.forms import inlineformset_factory
from django.utils import timezone

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

class BitacoraForm(forms.ModelForm):
    class Meta:
        model = Bitacora
        fields = ['nro_vale_combustible', 'cantidad_litros', 'km_inicial', 'km_final', 'destino', 'objetivo_comision', 'nro_factura', 'costo_total']
        widgets = {
            'nro_vale_combustible': forms.TextInput(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'}),
            'cantidad_litros': forms.NumberInput(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'}),
            'km_inicial': forms.NumberInput(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none', 'readonly': 'readonly'}),
            'km_final': forms.NumberInput(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'}),
            'destino': forms.TextInput(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'}),
            'objetivo_comision': forms.Textarea(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none', 'rows': 2}),
            'nro_factura': forms.TextInput(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'}),
            'costo_total': forms.NumberInput(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'}),
        }

    def __init__(self, *args, **kwargs):
        # Recibimos el usuario como parámetro al instanciar el form
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        km_llegada = cleaned_data.get('km_final')
        km_salida = cleaned_data.get('km_inicial')
        litros = cleaned_data.get('cantidad_litros')
        
        # Validación de rango
        if km_llegada and km_salida and km_llegada <= km_salida:
            raise forms.ValidationError("El kilometraje de llegada debe ser mayor al de salida.")
            
        # Validación de consumo excesivo (si supera el 50% de lo esperado, alerta)
        distancia = km_llegada - km_salida
        if litros > 0 and distancia > 0:
            rendimiento = distancia / litros
            if rendimiento < 2: 
                raise forms.ValidationError("El consumo reportado es inusualmente alto para este vehículo.")
        
        return cleaned_data

ViajeFormSet = inlineformset_factory(
    Bitacora, 
    Viaje, 
    fields=('origen', 'destino', 'km_inicio', 'km_fin', 'motivo'),
    extra=1,
    can_delete=True
)

class ViajeForm(forms.ModelForm):
    class Meta:
        model = Viaje
        fields = ['origen', 'destino', 'tipo_ruta', 'km_inicio', 'km_fin', 'motivo']
        widgets = {
            'origen': forms.TextInput(attrs={'class': 'w-full p-2 text-xs bg-slate-50 border-none rounded', 'placeholder': 'Origen'}),
            'destino': forms.TextInput(attrs={'class': 'w-full p-2 text-xs bg-slate-50 border-none rounded', 'placeholder': 'Destino'}),
            'tipo_ruta': forms.Select(attrs={'class': 'w-full p-2 text-xs bg-slate-50 border-none rounded'}),
            'km_inicio': forms.NumberInput(attrs={'class': 'w-20 p-2 text-xs bg-slate-50 border-none rounded', 'placeholder': '0.0'}),
            'km_fin': forms.NumberInput(attrs={'class': 'w-20 p-2 text-xs bg-slate-50 border-none rounded', 'placeholder': '0.0'}),
        }

class RegistroGastoForm(forms.Form):
    TIPO_CHOICES = [('vale', 'Vale Combustible'), ('peaje', 'Peaje')]
    tipo = forms.ChoiceField(choices=TIPO_CHOICES, widget=forms.Select(attrs={'class': '...'}))
    # Campos comunes o específicos
    lugar = forms.CharField(required=False)
    monto = forms.DecimalField()
    comprobante = forms.ImageField(required=False)