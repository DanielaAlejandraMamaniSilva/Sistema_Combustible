# gestion/filters.py
import django_filters
from .models import Bitacora, Vehiculo, Usuario

class BitacoraFilter(django_filters.FilterSet):
    # Rango de fechas
    fecha = django_filters.DateFromToRangeFilter(label="Rango de fechas")
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