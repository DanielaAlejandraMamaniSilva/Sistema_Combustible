from django.shortcuts import render, redirect,get_object_or_404
from .models import Usuario, Vehiculo, Asignacion, Bitacora, Area, TipoCombustible, AjusteSistema, InventarioCombustible
from django.contrib.auth.decorators import login_required,user_passes_test
from django.contrib.auth import get_user_model
from django.db.models import Avg, Sum, F, ExpressionWrapper, DecimalField, Count
from .forms import UsuarioCreationForm, UserChangeForm, VehiculoForm, AsignacionForm
from django.contrib import messages
from django.core.management import call_command
from django.contrib.sessions.models import Session
from django.utils import timezone
from django.contrib.admin.models import LogEntry,ADDITION, CHANGE, DELETION
from django.contrib.contenttypes.models import ContentType
import openpyxl
from django.db.models import Q
from django.http import HttpResponse
from decimal import Decimal
import io
from datetime import datetime, timedelta

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

@login_required
def dashboard_chofer(request):
    asignacion = Asignacion.objects.filter(chofer=request.user, esta_activo=True).first()
    vehiculo = asignacion.vehiculo if asignacion else None

    if request.method == 'POST' and vehiculo:
        try:            
            vale = request.POST.get('nro_vale')
            cantidad = request.POST.get('cantidad')
            km_llegada = int(request.POST.get('km_llegada'))
            motivo = request.POST.get('motivo')
            
            km_inicial = vehiculo.kilometraje_actual
            
            if km_llegada <= km_inicial:
                messages.error(request, "El KM de llegada debe ser mayor al KM de salida.")
            else:
                Bitacora.objects.create(
                    vehiculo=vehiculo,
                    chofer=request.user,
                    km_inicial=km_inicial,
                    km_final=km_llegada,
                    destino=motivo,
                    objetivo_comision=motivo,
                    nro_vale_combustible=vale,
                    cantidad_litros=cantidad,
                    costo_total=float(cantidad) * 3.74,
                    nro_factura="S/N"
                )
                messages.success(request, "Bitácora registrada con éxito.")
                return redirect('dashboard')
        except Exception as e:
            messages.error(request, f"Error al registrar: {e}")

    historial = Bitacora.objects.filter(chofer=request.user).order_by('-fecha')[:5]
    
    ultima_eficiencia = "0"
    if historial.exists():
        ult = historial[0]
        distancia = ult.km_final - ult.km_inicial
        if ult.cantidad_litros > 0:
            ultima_eficiencia = round(distancia / float(ult.cantidad_litros), 1)

    context = {
        'asignacion': asignacion,
        'vehiculo': vehiculo,
        'historial': historial,
        'km_actual': vehiculo.kilometraje_actual if vehiculo else 0,
        'ultima_eficiencia': ultima_eficiencia,
    }
    return render(request, 'dashboard_chofer.html', context)

Usuario = get_user_model()

@login_required
def dashboard_activos(request):
    if request.user.rol not in ['activos', 'admin', 'superadmin']:
        return redirect('dashboard')

    total_vehiculos = Vehiculo.objects.count()
    vehiculos_ok = Vehiculo.objects.filter(estado='operacional').count()
    
    porcentaje = 0
    if total_vehiculos > 0:
        porcentaje = int((vehiculos_ok / total_vehiculos) * 100)

    pendientes_validacion = Bitacora.objects.filter(estado_validacion='pendiente').count()
    pendientes_acta = Asignacion.objects.filter(esta_activo=True, documento_acta='').count()
    total_pendientes = pendientes_validacion + pendientes_acta
    asignaciones = Asignacion.objects.filter(esta_activo=True).select_related('vehiculo', 'chofer')

    context = {
        'total_vehiculos': total_vehiculos,
        'porcentaje': porcentaje,
        'total_pendientes': total_pendientes,
        'asignaciones': asignaciones,
        'fecha_actual': timezone.now(),
    }
    return render(request, 'dashboard_activos.html', context)

@login_required
def dashboard_bienes(request):
    if request.user.rol not in ['bienes', 'admin', 'superadmin']:
        return redirect('dashboard')

    mes_actual = timezone.now().month
    bitacoras_mes = Bitacora.objects.filter(fecha__month=mes_actual)
    
    consumo_diesel = bitacoras_mes.filter(vehiculo__tipo_combustible='Diesel').aggregate(total=Sum('cantidad_litros'))['total'] or 0
    consumo_gasolina = bitacoras_mes.filter(vehiculo__tipo_combustible='Gasolina').aggregate(total=Sum('cantidad_litros'))['total'] or 0

    pendientes = Bitacora.objects.filter(estado_validacion='pendiente').order_by('-fecha')
    total_pendientes = pendientes.count()
    
    ultimos_registros = Bitacora.objects.all().select_related('vehiculo', 'chofer').order_by('-fecha')[:10]

    context = {
        'consumo_diesel': consumo_diesel,
        'consumo_gasolina': consumo_gasolina,
        'total_pendientes': total_pendientes,
        'pendientes': ultimos_registros,
        'total_registros': Bitacora.objects.count(),
    }
    return render(request, 'dashboard_bienes.html', context)

@login_required
def validar_consumo_accion(request, pk, estado):
    if request.user.rol not in ['bienes', 'admin', 'superadmin']:
        return HttpResponse("No permitido", status=403)
    
    registro = get_object_or_404(Bitacora, pk=pk)
    registro.estado_validacion = estado
    registro.save()
    
    messages.success(request, f"Registro {registro.nro_vale_combustible} actualizado a {estado.upper()}")
    return redirect('dashboard')

@login_required
def supervision_combustible(request):
    registros = Bitacora.objects.all().order_by('-fecha')
    return render(request, 'admin_potosi/supervision.html', {'registros': registros})

@login_required
def dashboard_admin(request):
    hoy = timezone.now().date()
    hace_un_mes = hoy - timedelta(days=30)
    inicio_mes_actual = hoy.replace(day=1)
    
    bitacoras_mes = Bitacora.objects.filter(fecha__date__gte=inicio_mes_actual)
    
    stats = bitacoras_mes.aggregate(
        total_distancia=Sum(F('km_final') - F('km_inicial')),
        total_litros=Sum('cantidad_litros')
    )
    
    distancia = stats['total_distancia'] or 0
    litros = stats['total_litros'] or 1 
    consumo_avg = round(distancia / float(litros), 1)

    km_hoy = Bitacora.objects.filter(fecha__date=hoy).aggregate(
        total=Sum(F('km_final') - F('km_inicial'))
    )['total'] or 0

    alertas_count = Bitacora.objects.filter(estado_validacion='anomalia').count()

    limite_ruta = timezone.now() - timedelta(hours=4)
    choferes = Usuario.objects.filter(rol='chofer')
    
    for chofer in choferes:
        ultima_bitacora = Bitacora.objects.filter(chofer=chofer).order_by('-fecha').first()
        if ultima_bitacora and ultima_bitacora.fecha >= limite_ruta:
            chofer.estado_operativo = "EN RUTA"
            chofer.ruta_actual = ultima_bitacora.destino
        else:
            chofer.estado_operativo = "DISPONIBLE"
            chofer.ruta_actual = "En Base"
    
    filtro = request.GET.get('filtro', 'todos')
    registros = Bitacora.objects.select_related('vehiculo', 'chofer').order_by('-fecha')

    if filtro == 'semana':
        hace_una_semana = hoy - timedelta(days=7)
        registros = registros.filter(fecha__date__gte=hace_una_semana)
    
    context = {
        'consumo_avg': consumo_avg,
        'km_totales': km_hoy,
        'alertas_count': alertas_count,
        'monitoreo_choferes': choferes[:3],
        'registros': registros[:10],       
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

@login_required
def reporte_oficial_chofer(request, chofer_id):
    chofer = get_object_or_404(Usuario, id=chofer_id)
    mes_actual = datetime.now().month
    anio_actual = datetime.now().year
    
    vehiculo_asig = Asignacion.objects.filter(chofer=chofer, esta_activo=True).first()
    bitacoras = Bitacora.objects.filter(
        chofer=chofer, 
        fecha__month=mes_actual, 
        fecha__year=anio_actual
    ).order_by('fecha')

    total_cargado = 0
    total_recorrido = 0
    registros_procesados = []
    saldo_anterior = 133.90 # Este dato vendría del mes anterior en un sistema real
    saldo_actual = saldo_anterior

    for b in bitacoras:
        recorrido = b.km_final - b.km_inicial
        consumo_estimado = recorrido / float(vehiculo_asig.vehiculo.rendimiento_km_litro)
        saldo_actual = float(saldo_actual) + float(b.cantidad_litros) - consumo_estimado
        
        registros_procesados.append({
            'fecha': b.fecha,
            'vale': b.nro_vale_combustible,
            'ingreso': b.cantidad_litros,
            'utilizado': round(consumo_estimado, 2),
            'saldo': round(saldo_actual, 2),
            'objeto': b.objetivo_comision,
            'salida': b.km_inicial,
            'llegada': b.km_final,
            'recorrido': recorrido,
        })
        total_cargado += b.cantidad_litros
        total_recorrido += recorrido

    context = {
        'chofer': chofer,
        'vehiculo': vehiculo_asig.vehiculo if vehiculo_asig else None,
        'registros': registros_procesados,
        'mes': "ENERO",
        'anio': anio_actual,
        'total_recorrido': total_recorrido,
        'total_cargado': total_cargado,
        'saldo_anterior': saldo_anterior,
        'saldo_final': round(saldo_actual, 2),
    }
    return render(request, 'reportes/planilla_oficial.html', context)

@login_required
def lista_registros_all(request):
    query = request.GET.get('q', '')
    registros = Bitacora.objects.all().order_by('-fecha')
    if query:
        registros = registros.filter(Q(vehiculo__placa__icontains=query) | Q(chofer__last_name__icontains=query))
    return render(request, 'admin_potosi/registros_list.html', {'registros': registros})

@login_required
def validar_registros(request):
    pendientes = Bitacora.objects.filter(estado_validacion='pendiente').order_by('-fecha')
    return render(request, 'admin_potosi/validar.html', {'pendientes': pendientes})

@login_required
def cambiar_estado_bitacora(request, pk, nuevo_estado):
    bitacora = get_object_or_404(Bitacora, pk=pk)
    bitacora.estado_validacion = nuevo_estado
    bitacora.save()
    messages.success(request, f"Registro {bitacora.nro_vale_combustible} marcado como {nuevo_estado.upper()}")
    return redirect('validar_registros')

@login_required
def monitoreo_tiempo_real(request):
    choferes = Usuario.objects.filter(rol='chofer')
    hoy = datetime.now().date()
    for c in choferes:
        c.en_ruta = Bitacora.objects.filter(chofer=c, fecha__date=hoy).exists()
    return render(request, 'admin_potosi/monitoreo.html', {'choferes': choferes})

@login_required
def seleccionar_chofer_reporte(request):
    choferes = Usuario.objects.filter(rol='chofer')
    return render(request, 'admin_potosi/seleccionar_chofer.html', {'choferes': choferes})

@login_required
def seleccionar_vehiculo_reporte(request):
    # Solo vehículos que tengan al menos una bitácora o todos
    vehiculos = Vehiculo.objects.all()
    return render(request, 'admin_potosi/seleccionar_vehiculo.html', {'vehiculos': vehiculos})

@login_required
def reporte_por_vehiculo(request, vehiculo_id):
    vehiculo = get_object_or_404(Vehiculo, id=vehiculo_id)
    mes_actual = datetime.now().month
    anio_actual = datetime.now().year
    
    bitacoras = Bitacora.objects.filter(
        vehiculo=vehiculo,
        fecha__month=mes_actual, 
        fecha__year=anio_actual
    ).order_by('fecha')

    total_recorrido = 0
    total_cargado = 0
    for b in bitacoras:
        total_recorrido += (b.km_final - b.km_inicial)
        total_cargado += b.cantidad_litros

    context = {
        'vehiculo': vehiculo,
        'registros': bitacoras,
        'total_recorrido': total_recorrido,
        'total_cargado': total_cargado,
        'mes': "ABRIL", # Podrías hacerlo dinámico
        'anio': anio_actual,
    }
    return render(request, 'reportes/planilla_oficial.html', context)

@login_required
def crear_asignacion(request):
    if request.user.rol not in ['activos', 'admin', 'superadmin']:
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = AsignacionForm(request.POST, request.FILES)
        if form.is_valid():
            asignacion = form.save()
            messages.success(request, f"Vehículo {asignacion.vehiculo.placa} asignado a {asignacion.chofer.get_full_name()}")
            return redirect('dashboard')
    else:
        form = AsignacionForm()
    
    return render(request, 'catalogos/asignar_form.html', {'form': form})

@login_required
def historial_asignaciones(request):
    asignaciones = Asignacion.objects.all().order_by('-fecha_asignacion')
    return render(request, 'admin_potosi/historial_asignaciones.html', {'asignaciones': asignaciones})

@login_required
def lista_memorandums(request):
    asignaciones = Asignacion.objects.exclude(nro_memorandum='').order_by('-fecha_asignacion')
    return render(request, 'admin_potosi/memorandums.html', {'asignaciones': asignaciones})

@login_required
def lista_actas(request):
    asignaciones = Asignacion.objects.all().order_by('-fecha_asignacion')
    return render(request, 'admin_potosi/actas.html', {'asignaciones': asignaciones})

@login_required
def dashboard_bienes(request):
    if request.user.rol not in ['bienes', 'admin', 'superadmin']:
        return redirect('dashboard')

    mes_actual = timezone.now().month
    bitacoras_mes = Bitacora.objects.filter(fecha__month=mes_actual)
    
    consumo_diesel = bitacoras_mes.filter(vehiculo__tipo_combustible='Diesel').aggregate(total=Sum('cantidad_litros'))['total'] or 0
    consumo_gasolina = bitacoras_mes.filter(vehiculo__tipo_combustible='Gasolina').aggregate(total=Sum('cantidad_litros'))['total'] or 0

    pendientes = Bitacora.objects.filter(estado_validacion='pendiente').order_by('-fecha')
    total_pendientes = pendientes.count()
    
    ultimos_registros = Bitacora.objects.all().select_related('vehiculo', 'chofer').order_by('-fecha')[:10]

    context = {
        'consumo_diesel': consumo_diesel,
        'consumo_gasolina': consumo_gasolina,
        'total_pendientes': total_pendientes,
        'pendientes': ultimos_registros,
        'total_registros': Bitacora.objects.count(),
    }
    return render(request, 'dashboard_bienes.html', context)

@login_required
def validar_consumo_accion(request, pk, estado):
    if request.user.rol not in ['bienes', 'admin', 'superadmin']:
        return HttpResponse("No permitido", status=403)
    
    registro = get_object_or_404(Bitacora, pk=pk)
    registro.estado_validacion = estado
    registro.save()
    
    messages.success(request, f"Registro {registro.nro_vale_combustible} actualizado a {estado.upper()}")
    return redirect('dashboard')

@login_required
def supervision_combustible(request):
    registros = Bitacora.objects.all().order_by('-fecha')
    return render(request, 'admin_potosi/supervision.html', {'registros': registros})

@login_required
def validacion_consumo(request):
    pendientes = Bitacora.objects.filter(estado_validacion='pendiente').order_by('-fecha')
    return render(request, 'admin_potosi/validar.html', {'pendientes': pendientes})

@login_required
def reportes_bienes(request):
    return redirect('reporte_por_chofer')

@login_required
def control_abastecimiento(request):
    ingresos = InventarioCombustible.objects.all().order_by('-ultima_actualizacion')
    return render(request, 'admin_potosi/abastecimiento.html', {'ingresos': ingresos})

@login_required
def nuevo_registro_combustible(request):
    if request.method == 'POST':
        tipo = request.POST.get('tipo')
        cantidad = request.POST.get('cantidad')
        obj, created = InventarioCombustible.objects.get_or_create(tipo=tipo)
        obj.cantidad_total += Decimal(cantidad)
        obj.save()
        messages.success(request, f"Se han cargado {cantidad} Lts de {tipo} al inventario.")
        return redirect('dashboard')
    
    combustibles = TipoCombustible.objects.all()
    return render(request, 'admin_potosi/form_abastecimiento.html', {'combustibles': combustibles})

@login_required
def historial_viajes_chofer(request):
    viajes = Bitacora.objects.filter(chofer=request.user).order_by('-fecha')
    return render(request, 'chofer/historial.html', {'viajes': viajes})

@login_required
def detalle_vehiculo_chofer(request):
    asignacion = Asignacion.objects.filter(chofer=request.user, esta_activo=True).first()
    return render(request, 'chofer/vehiculo.html', {'asignacion': asignacion})

from django.shortcuts import render

def mi_error_404(request, exception):
    return render(request, '404.html', status=404)