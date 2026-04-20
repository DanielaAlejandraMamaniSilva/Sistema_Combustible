from django.shortcuts import render, redirect,get_object_or_404
from .models import Usuario, Vehiculo, Asignacion, Bitacora, Area, TipoCombustible, AjusteSistema
from django.contrib.auth.decorators import login_required,user_passes_test
from django.contrib.auth import get_user_model
from django.db.models import Avg, Sum, F
from .forms import UsuarioCreationForm, UserChangeForm, VehiculoForm
from django.contrib import messages
from django.contrib.sessions.models import Session
from django.utils import timezone
from django.contrib.admin.models import LogEntry,ADDITION, CHANGE, DELETION
from django.contrib.contenttypes.models import ContentType
import openpyxl
from django.db.models import Q
from django.http import HttpResponse
import io
from django.core.management import call_command


@login_required
def dashboard_view(request):
    config, _ = AjusteSistema.objects.get_or_create(id=1)
    if request.user.rol == 'superadmin':
        return dashboard_superadmin(request)
    
    if config.modo_seguro:
        return render(request, 'mantenimiento.html')
    
    if request.user.rol == 'chofer':
        return dashboard_chofer(request)
    
    elif request.user.rol == 'bienes':
        return dashboard_bienes(request)
    
    elif request.user.rol in ['activos']:
        return dashboard_activos(request)

    elif request.user.rol in ['admin']:
        return dashboard_admin(request)
        
    return redirect('login')
Usuario = get_user_model()

def dashboard_chofer(request):
    asignacion = Asignacion.objects.filter(chofer=request.user, esta_activo=True).first()
    
    historial = Bitacora.objects.filter(chofer=request.user).order_by('-fecha')[:5]
    config, _ = AjusteSistema.objects.get_or_create(id=1)
    if config.modo_seguro:
        return render(request, 'mantenimiento.html')
    
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
    sesiones_activas = Session.objects.filter(expire_date__gte=timezone.now()).count()
    salud = "99.8%" 
    total_vehiculos = Vehiculo.objects.count()
    logs = LogEntry.objects.all().select_related('user', 'content_type')[:5]
    config, _ = AjusteSistema.objects.get_or_create(id=1)
    
    catalogos = [
        {'nombre': 'Flota de Vehículos', 'total': f"{total_vehiculos} Vehículos", 'cambio': 'Hoy, 09:12 AM', 'estado': 'Sincronizado'},
        {'nombre': 'Áreas / Secretarías', 'total': '12 Unidades', 'cambio': 'Ayer', 'estado': 'Sincronizado'},
        {'nombre': 'Tipos de Combustible', 'total': '2 Activos', 'cambio': '12 Oct 2023', 'estado': 'Editable'},
    ]

    context = {
        'total_usuarios': total_usuarios,
        'sesiones_activas': sesiones_activas,
        'salud': salud,
        'logs': logs,
        'total_vehiculos': total_vehiculos,
        'catalogos': catalogos,
        'uptime': '42 días 14h',
        'db_instance': 'SOV-ARC-PROD-01',
        'version': 'v4.8.2-POTOSI-SA',
        'modo_seguro_activado': config.modo_seguro,
    }
    return render(request, 'dashboard_superadmin.html', context)

def admin_required(user):
    return user.rol in ['superadmin', 'admin']

@login_required
@user_passes_test(admin_required)
def lista_usuarios(request):
    usuarios = Usuario.objects.all().order_by('-id')
    return render(request, 'usuarios/lista.html', {'usuarios': usuarios})

@login_required
@user_passes_test(admin_required)
def crear_usuario(request):
    if request.method == 'POST':
        form = UsuarioCreationForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('lista_usuarios')
    else:
        form = UsuarioCreationForm()
    return render(request, 'usuarios/form.html', {'form': form})

class UsuarioChangeForm(UserChangeForm):
    password = None 
    class Meta:
        model = Usuario
        fields = ('username', 'first_name', 'last_name', 'email', 'rol', 'ci', 'licencia_conducir', 'vencimiento_licencia')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'})

@login_required
@user_passes_test(admin_required)
def editar_usuario(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk)
    if request.method == 'POST':
        form = UsuarioChangeForm(request.POST, instance=usuario)
        if form.is_valid():
            form.save()
            messages.success(request, f"Usuario {usuario.username} actualizado correctamente.")
            return redirect('lista_usuarios')
    else:
        form = UsuarioChangeForm(instance=usuario)
    return render(request, 'usuarios/form.html', {'form': form, 'editando': True})

@login_required
@user_passes_test(admin_required)
def eliminar_usuario(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk)
    if usuario == request.user:
        messages.error(request, "No puedes eliminarte a ti mismo.")
    else:
        usuario.delete()
        messages.success(request, "Usuario eliminado con éxito.")
    return redirect('lista_usuarios')

@login_required
def lista_vehiculos(request):
    vehiculos = Vehiculo.objects.all()
    return render(request, 'catalogos/vehiculos.html', {'vehiculos': vehiculos})

@login_required
def exportar_usuarios_excel(request):
    if request.user.rol != 'superadmin':
        return HttpResponse("No autorizado", status=403)
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Usuarios"

    columns = ['Username', 'Nombre', 'Apellido', 'Rol', 'CI', 'Email']
    ws.append(columns)

    usuarios = Usuario.objects.all()
    for u in usuarios:
        ws.append([u.username, u.first_name, u.last_name, u.get_rol_display(), u.ci, u.email])

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="Reporte_Usuarios.xlsx"'
    wb.save(response)
    return response

@login_required
def log_auditoria(request):
    logs = LogEntry.objects.all().select_related('user', 'content_type').order_by('-action_time')
    return render(request, 'usuarios/logs.html', {'logs': logs})

@login_required
def configuracion_global(request):
    config, _ = AjusteSistema.objects.get_or_create(id=1)
    return render(request, 'usuarios/configuracion.html', {
        'modo_seguro': config.modo_seguro,
        'ultima_mod': config.ultima_modificacion
    })
@login_required
def gestion_catalogos(request):
    context = {
        'vehiculos': Vehiculo.objects.all(),
        'areas': Area.objects.all(),
        'combustibles': TipoCombustible.objects.all(),
    }
    return render(request, 'usuarios/catalogos.html', context)

@login_required
def vista_roles(request):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
    
    # Contamos cuántos usuarios hay por cada rol
    conteo_roles = []
    for codigo, nombre in Usuario.ROLES:
        cantidad = Usuario.objects.filter(rol=codigo).count()
        conteo_roles.append({
            'codigo': codigo,
            'nombre': nombre,
            'cantidad': cantidad
        })
        
    return render(request, 'usuarios/roles.html', {'roles_info': conteo_roles})

@login_required
def vista_soporte(request):
    return render(request, 'usuarios/soporte.html')

@login_required
def toggle_modo_seguro(request):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
    
    config, created = AjusteSistema.objects.get_or_create(id=1)
    config.modo_seguro = not config.modo_seguro
    config.save()
    
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def buscar_registros(request):
    query = request.GET.get('q')
    resultados_usuarios = []
    resultados_vehiculos = []
    resultados_bitacoras = []

    if query:
        resultados_usuarios = Usuario.objects.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query) | Q(ci__icontains=query)
        )
        resultados_vehiculos = Vehiculo.objects.filter(
            Q(placa__icontains=query) | Q(marca__icontains=query)
        )
        resultados_bitacoras = Bitacora.objects.filter(
            Q(nro_vale_combustible__icontains=query) | Q(destino__icontains=query)
        )

    return render(request, 'buscar_resultados.html', {
        'query': query,
        'usuarios': resultados_usuarios,
        'vehiculos': resultados_vehiculos,
        'bitacoras': resultados_bitacoras,
    })

@login_required
def forzar_respaldo(request):
    if request.user.rol != 'superadmin':
        return HttpResponse("No autorizado", status=403)
    
    output = io.StringIO()
    call_command('dumpdata', indent=2, stdout=output)
    data = output.getvalue()
    
    content_type = ContentType.objects.get_for_model(AjusteSistema)
    config, _ = AjusteSistema.objects.get_or_create(id=1)

    LogEntry.objects.create(
        user_id=request.user.id,
        content_type_id=content_type.id,
        object_id=config.id,
        object_repr="Respaldo Forzado",
        action_flag=CHANGE, 
        change_message="El usuario generó un respaldo completo del sistema (.json)"
    )
    
    response = HttpResponse(data, content_type="application/json")
    fecha = timezone.now().strftime("%Y_%m_%d-%H_%M")
    response['Content-Disposition'] = f'attachment; filename="Respaldo_Soberania_{fecha}.json"'
    
    return response

@login_required
def crear_vehiculo(request):
    if request.method == 'POST':
        form = VehiculoForm(request.POST)
        if form.is_valid():
            vehiculo = form.save()
            messages.success(request, f"Vehículo {vehiculo.placa} registrado con éxito.")
            return redirect('gestion_catalogos')
    else:
        form = VehiculoForm()
    return render(request, 'catalogos/vehiculo_form.html', {'form': form})

@login_required
def editar_vehiculo(request, pk):
    vehiculo = get_object_or_404(Vehiculo, pk=pk)
    if request.method == 'POST':
        form = VehiculoForm(request.POST, instance=vehiculo)
        if form.is_valid():
            form.save()
            messages.success(request, f"Vehículo {vehiculo.placa} actualizado.")
            return redirect('gestion_catalogos')
    else:
        form = VehiculoForm(instance=vehiculo)
    return render(request, 'catalogos/vehiculo_form.html', {'form': form, 'editando': True})

@login_required
def eliminar_vehiculo(request, pk):
    vehiculo = get_object_or_404(Vehiculo, pk=pk)
    vehiculo.delete()
    messages.success(request, "Vehículo eliminado del catálogo.")
    return redirect('gestion_catalogos')