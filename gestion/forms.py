from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from .models import Asignacion, Vehiculo, Usuario, Bitacora, Viaje,Area, JustificacionEdicion, TipoCombustible
from django.forms import inlineformset_factory
from django.utils import timezone


class UsuarioCreationForm(UserCreationForm): 
    class Meta(UserCreationForm.Meta):
        model = Usuario
        fields = ('username', 'first_name', 'last_name', 'email', 'rol', 'ci', 
                  'licencia_conducir', 'categoria_licencia', 'vencimiento_licencia', 'foto')
        widgets = {
            'vencimiento_licencia': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean_licencia_conducir(self):
        licencia = self.cleaned_data.get('licencia_conducir')
        rol = self.cleaned_data.get('rol')

        if rol == 'chofer':
            if not licencia:
                raise forms.ValidationError("La licencia es obligatoria para el rol de Chofer.")
            
            licencia_upper = licencia.upper()
            if not ('B' in licencia_upper or 'C' in licencia_upper):
                raise forms.ValidationError("ERROR: El chofer debe contar con licencia Categoría B o C.")
        return licencia

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({
                'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none focus:ring-2 focus:ring-primary'
            })

class VehiculoForm(forms.ModelForm):
    class Meta:
        model = Vehiculo
        fields = ['placa', 'marca', 'modelo', 'anio', 'tipo', 'color', 
                  'capacidad_tanque', 'rendimiento_km_litro', 'combustible_inicial', 'kilometraje_actual','nro_chasis', 
                   'vencimiento_soat', 'estado', 'foto']
        widgets = {
            'estado': forms.Select(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none focus:ring-2 focus:ring-primary'}),
            'tipo_combustible': forms.Select(attrs={'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none focus:ring-2 focus:ring-primary'}),
            'vencimiento_soat': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            if field not in ['estado', 'tipo_combustible']:
                self.fields[field].widget.attrs.update({
                    'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none focus:ring-2 focus:ring-primary'
                })

class AsignacionForm(forms.ModelForm):
    area = forms.ModelChoiceField(
        queryset=Area.objects.all(), 
        label="Secretaría / Área",
        widget=forms.Select(attrs={'class': 'w-full p-3 bg-slate-50 border-none rounded-xl outline-none focus:ring-2 focus:ring-primary'})
    )
    class Meta:
        model = Asignacion
        fields = ['vehiculo', 'chofer', 'area', 'nro_memorandum', 'documento_acta']
        widgets = {
            'vehiculo': forms.Select(attrs={'class': 'w-full p-4 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:ring-2 focus:ring-primary transition-all font-bold text-slate-700'}),
            'chofer': forms.Select(attrs={'class': 'w-full p-4 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:ring-2 focus:ring-primary transition-all font-bold text-slate-700'}),
            'area': forms.Select(attrs={'class': 'w-full p-4 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:ring-2 focus:ring-primary transition-all font-bold text-slate-700'}),
            'nro_memorandum': forms.TextInput(attrs={'class': 'w-full p-4 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:ring-2 focus:ring-primary transition-all font-bold text-slate-900', 'placeholder': 'Ej: MEMO/GADP/045/2024'}),
        }
    def clean(self):
        cleaned_data = super().clean()
        chofer = cleaned_data.get('chofer')
        vehiculo = cleaned_data.get('vehiculo')

        if chofer and Asignacion.objects.filter(chofer=chofer, esta_activo=True).exists():
            self.add_error('chofer', f"ALERTA: El conductor {chofer.get_full_name().upper()} ya tiene un vehículo bajo su responsabilidad.")
        if vehiculo and Asignacion.objects.filter(vehiculo=vehiculo, esta_activo=True).exists():
            self.add_error('vehiculo', f"ALERTA: El vehículo {vehiculo.placa} ya se encuentra asignado a otro chofer.")

        return cleaned_data
    
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
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        nro_vale = cleaned_data.get("nro_vale_combustible")
        km_llegada = cleaned_data.get('km_final')
        km_salida = cleaned_data.get('km_inicial')
        litros = cleaned_data.get('cantidad_litros')
        
        if km_llegada and km_salida and km_llegada <= km_salida:
            raise forms.ValidationError("El kilometraje de llegada debe ser mayor al de salida.")
            
        if nro_vale and Bitacora.objects.filter(nro_vale_combustible=nro_vale).exists():
            raise forms.ValidationError(f"El vale N° {nro_vale} ya fue registrado anteriormente.")
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
    lugar = forms.CharField(required=False)
    monto = forms.DecimalField()
    comprobante = forms.ImageField(required=False)

class RegistroChoferCompletoForm(forms.ModelForm):
    first_name = forms.CharField(label="Nombre")
    last_name = forms.CharField(label="Apellido")
    area = forms.ModelChoiceField(queryset=Area.objects.all(), label="Secretaría/Área")
    nro_memorandum = forms.CharField(label="Nº Memorándum")
    vehiculo = forms.ModelChoiceField(queryset=Vehiculo.objects.filter(estado='operacional'), label="Vehículo")
    documento_acta = forms.FileField(label="Acta de Entrega (PDF)")

    class Meta:
        model = Usuario
        fields = ['username', 'first_name', 'last_name', 'ci', 'area']

class AreaForm(forms.ModelForm):
    class Meta:
        model = Area
        fields = ['nombre', 'area_especifica', 'estado']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'w-full p-3 bg-slate-50 border-none rounded-xl outline-none focus:ring-2 focus:ring-primary', 
                'placeholder': 'Ej: Secretaría de Salud'
            }),
            'area_especifica': forms.TextInput(attrs={
                'class': 'w-full p-3 bg-slate-50 border-none rounded-xl outline-none focus:ring-2 focus:ring-primary', 
                'placeholder': 'Ej: Administrativa Financiera'
            }),
            'estado': forms.CheckboxInput(attrs={
                'class': 'sr-only peer',
            }),
        }

class UsuarioChangeForm(forms.ModelForm):
    password = None 
    class Meta:
        model = Usuario
        fields = ('username', 'first_name', 'last_name', 'email', 'rol', 'ci', 
                  'licencia_conducir', 'vencimiento_licencia', 'foto')
        widgets = {
            'vencimiento_licencia': forms.DateInput(attrs={'type': 'date'}),
            'foto': forms.FileInput(attrs={'id': 'id_foto', 'accept': 'image/*'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.fields:
            if field_name != 'foto':
                self.fields[field_name].widget.attrs.update({
                    'class': 'w-full p-4 bg-slate-50 border border-slate-200 rounded-2xl outline-none focus:ring-2 focus:ring-primary transition-all font-bold text-slate-700 text-sm'
                })
        
class TraspasoAreaForm(forms.Form):
    nueva_area = forms.ModelChoiceField(
        queryset=Area.objects.filter(estado=True),
        label="Nueva Secretaría / Área",
        widget=forms.Select(attrs={'class': 'w-full p-3 bg-slate-50 border-none rounded-xl focus:ring-2 focus:ring-primary'})
    )
    nro_memorandum = forms.CharField(
        max_length=50, 
        label="Nº de Memorándum de Traspaso",
        widget=forms.TextInput(attrs={'class': 'w-full p-3 bg-slate-50 border-none rounded-xl focus:ring-2 focus:ring-primary'})
    )
    documento_traspaso = forms.FileField(
        label="Memorándum de Designación (PDF)",
        widget=forms.FileInput(attrs={'class': 'w-full p-3 bg-slate-50 border-none rounded-xl'})
    )

class EnmiendaBitacoraForm(forms.ModelForm):
    km_inicial = forms.IntegerField(label="Kilometraje Inicial")
    km_final = forms.IntegerField(label="Kilometraje Final")
    cantidad_litros = forms.DecimalField(label="Cantidad de Litros", max_digits=10, decimal_places=2)
    motivo_enmienda = forms.CharField(
        widget=forms.Textarea(attrs={
            'id': 'id_motivo_enmienda', 
            'placeholder': 'Describa detalladamente el motivo técnico de esta corrección...'
        }),
        label="Motivo de la Enmienda"
    )
    documento_respaldo = forms.FileField(
        widget=forms.FileInput(attrs={'class': 'hidden', 'id': 'id_documento_respaldo'}),
        label="Nota de Respaldo (PDF/Imagen)",
        required=True
    )

    class Meta:
        model = Bitacora
        fields = ['origen', 'destino', 'km_inicial', 'km_final', 'cantidad_litros', 'nro_vale_combustible']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['origen'].widget.attrs.update({'placeholder': 'Lugar de salida'})
        self.fields['destino'].widget.attrs.update({'placeholder': 'Lugar de llegada'})

class TipoCombustibleForm(forms.ModelForm):
    class Meta:
        model = TipoCombustible
        fields = ['nombre']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'w-full p-4 bg-slate-50 border border-slate-100 rounded-2xl outline-none focus:ring-2 focus:ring-primary font-bold text-slate-900'
            }),
        }