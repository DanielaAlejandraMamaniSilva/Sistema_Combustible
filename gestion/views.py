from django.shortcuts import render, redirect
from .models import Vehiculo, Asignacion, Bitacora
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Avg, Sum, F

@login_required
def dashboard_view(request):

    if request.user.rol == 'chofer':
        return dashboard_chofer(request)
    
    elif request.user.rol == 'bienes':
        return dashboard_bienes(request)
    
    elif request.user.rol in ['activos']:
        return dashboard_activos(request)

    elif request.user.rol in ['admin']:
        return dashboard_admin(request)

    elif request.user.rol in ['superadmin']:
        return dashboard_superadmin(request)
        
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

@login_required
def dashboard_admin(request):
    bitacoras = Bitacora.objects.all()
    consumo_avg = 14.2
    km_totales = bitacoras.aggregate(total=Sum(F('km_final') - F('km_inicial')))['total'] or 0
    alertas_count = Bitacora.objects.filter(estado_validacion='anomalia').count()
    
    monitoreo_choferes = Usuario.objects.filter(rol='chofer')[:3]
    
    registros = Bitacora.objects.select_related('vehiculo', 'chofer').order_by('-fecha')[:10]

    context = {
        'consumo_avg': consumo_avg,
        'km_totales': km_totales,
        'alertas_count': alertas_count,
        'monitoreo_choferes': monitoreo_choferes,
        'registros': registros,
    }
    return render(request, 'dashboard_admin.html', context)

@login_required
def dashboard_superadmin(request):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')

    total_usuarios = Usuario.objects.count()
    total_vehiculos = Vehiculo.objects.count()
    
    catalogos = [
        {'nombre': 'Flota de Vehículos', 'total': f"{total_vehiculos} Vehículos", 'cambio': 'Hoy, 09:12 AM', 'estado': 'Sincronizado'},
        {'nombre': 'Áreas / Secretarías', 'total': '12 Unidades', 'cambio': 'Ayer', 'estado': 'Sincronizado'},
        {'nombre': 'Tipos de Combustible', 'total': '2 Activos', 'cambio': '12 Oct 2023', 'estado': 'Editable'},
    ]

    context = {
        'total_usuarios': total_usuarios,
        'total_vehiculos': total_vehiculos,
        'catalogos': catalogos,
        'uptime': '42 días 14h',
        'db_instance': 'SOV-ARC-PROD-01',
        'version': 'v4.8.2-POTOSI-SA'
    }
    return render(request, 'dashboard_superadmin.html', context)