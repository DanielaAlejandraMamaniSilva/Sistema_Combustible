import django_filters
from .models import Bitacora, Vehiculo, Usuario
from .models import BitacoraActividad
from django import forms

class BitacoraFilter(django_filters.FilterSet):
    # Rango de fechas
    fecha_inicio = django_filters.DateFilter(
        field_name='fecha', 
        lookup_expr='gte',
        label='Desde',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    
    # Configuramos 'fecha_fin' para que sea un calendario
    fecha_fin = django_filters.DateFilter(
        field_name='fecha', 
        lookup_expr='lte',
        label='Hasta',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    # Chofer (Dropdown)
    chofer = django_filters.ModelChoiceFilter(queryset=Usuario.objects.filter(rol='chofer'))
    # Vehículo
    vehiculo = django_filters.ModelChoiceFilter(queryset=Vehiculo.objects.all())
    # Estado
    estado_validacion = django_filters.ChoiceFilter(choices=Bitacora.ESTADOS_VALIDACION)
    # Tipo de Viaje (desde el modelo Viaje relacionado)
    tipo_ruta = django_filters.ChoiceFilter(field_name='viajes__tipo_ruta', choices=Bitacora.TIPO_RUTA_CHOICES)

    class Meta:
        model = Bitacora
        fields = ['fecha', 'chofer', 'vehiculo', 'estado_validacion', 'tipo_ruta']

class ActividadFilter(django_filters.FilterSet):
    fecha = django_filters.DateFromToRangeFilter(
        field_name='fecha_hora', 
        label='Rango de Fecha',
        widget=django_filters.widgets.RangeWidget(attrs={'type': 'date', 'class': 'p-2 border rounded-lg text-xs'})
    )

    class Meta:
        model = BitacoraActividad
        fields = ['usuario', 'categoria', 'rol']