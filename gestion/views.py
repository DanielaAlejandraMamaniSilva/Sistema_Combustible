from django.shortcuts import render
from .models import Vehiculo, Asignacion
from django.contrib.auth import get_user_model

User = get_user_model()

def dashboard_activos(request):
    total_vehiculos = Vehiculo.objects.count()
    porcentaje = 0
    if total_vehiculos > 0:
        operacionales = Vehiculo.objects.filter(estado='operacional').count()
        porcentaje = int((operacionales / total_vehiculos) * 100)
    
    asignaciones = Asignacion.objects.all()
    conductores = User.objects.filter(rol='chofer')

    return render(request, 'dashboard_activos.html', {
        'total_vehiculos': total_vehiculos,
        'porcentaje': porcentaje,
        'asignaciones': asignaciones,
        'conductores': conductores,
    })