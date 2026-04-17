from django.shortcuts import render, redirect
from .models import Vehiculo, Asignacion, Bitacora
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model

@login_required
def dashboard_view(request):
    # 1. Si el usuario es CHOFER
    if request.user.rol == 'chofer':
        asignacion = Asignacion.objects.filter(chofer=request.user, esta_activo=True).first()
        context = {
            'asignacion': asignacion,
        }
        return render(request, 'dashboard_chofer.html', context)
    
    else:
        total_vehiculos = Vehiculo.objects.count()
        # Lógica de porcentaje...
        asignaciones = Asignacion.objects.filter(esta_activo=True)
        conductores = Usuario.objects.filter(rol='chofer')
        
        context = {
            'total_vehiculos': total_vehiculos,
            'asignaciones': asignaciones,
            'conductores': conductores,
            'porcentaje': 80, # Ejemplo
        }
        return render(request, 'dashboard_activos.html', context)

def dashboard_chofer(request):
    asignacion = Asignacion.objects.filter(chofer=request.user, esta_activo=True).first()
    
    historial = Bitacora.objects.filter(chofer=request.user).order_by('-fecha')[:5]
    
    context = {
        'asignacion': asignacion,
        'historial': historial,
        'km_actual': asignacion.vehiculo.kilometraje_actual if asignacion else 0,
    }
    return render(request, 'dashboard_chofer.html', context)

Usuario = get_user_model()

def dashboard_activos(request):
    total_vehiculos = Vehiculo.objects.count()
    porcentaje = 0
    if total_vehiculos > 0:
        operacionales = Vehiculo.objects.filter(estado='operacional').count()
        porcentaje = int((operacionales / total_vehiculos) * 100)
    
    asignaciones = Asignacion.objects.filter(esta_activo=True).select_related('vehiculo', 'chofer')
    conductores = Usuario.objects.filter(rol='chofer')

    return render(request, 'dashboard_activos.html', {
        'total_vehiculos': total_vehiculos,
        'porcentaje': porcentaje,
        'asignaciones': asignaciones,
        'conductores': conductores,
    })