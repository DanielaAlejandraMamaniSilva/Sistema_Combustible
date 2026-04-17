from django.shortcuts import render, redirect
from .models import Vehiculo, Asignacion, Bitacora
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Sum

@login_required
def dashboard_view(request):

    if request.user.rol == 'chofer':
        return dashboard_chofer(request)
    
    elif request.user.rol == 'bienes':
        return dashboard_bienes(request)
    
    elif request.user.rol in ['activos', 'admin', 'superadmin']:
        return dashboard_activos(request)

    return redirect('login')
Usuario = get_user_model()

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

@login_required
def dashboard_bienes(request):
    pendientes = Bitacora.objects.filter(estado_validacion='pendiente').order_by('-fecha')
    
    consumo_diesel = Bitacora.objects.filter(vehiculo__tipo_combustible='Diesel').aggregate(Sum('cantidad_litros'))['cantidad_litros__sum'] or 0
    consumo_gasolina = Bitacora.objects.filter(vehiculo__tipo_combustible='Gasolina').aggregate(Sum('cantidad_litros'))['cantidad_litros__sum'] or 0

    context = {
        'pendientes': pendientes,
        'total_pendientes': pendientes.count(),
        'consumo_diesel': consumo_diesel,
        'consumo_gasolina': consumo_gasolina,
    }
    return render(request, 'dashboard_bienes.html', context)