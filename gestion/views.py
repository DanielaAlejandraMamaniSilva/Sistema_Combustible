from django.shortcuts import render, redirect, get_object_or_404
from .models import Usuario, Vehiculo, Asignacion, Bitacora, Area, TipoCombustible, AjusteSistema, InventarioCombustible, Peaje, Viaje, ValeCombustible, RegistroMantenimiento,BitacoraActividad, JustificacionEdicion, LogAuditoria
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import get_user_model
from django.forms.models import model_to_dict
from django.db.models import Avg, Sum, F, ExpressionWrapper, DecimalField, Count
from .forms import UsuarioCreationForm, UserChangeForm, VehiculoForm, AsignacionForm, BitacoraForm, ViajeFormSet, RegistroChoferCompletoForm, AreaForm, TraspasoAreaForm, EnmiendaBitacoraForm
from django.contrib import messages
from .utils import obtener_ruta_dijkstra, calcular_distancia, buscar_coordenadas, obtener_ip, generar_sql_insert, generar_respaldo_sql,evaluar_tipo_bitacora, registrar_auditoria
from django.utils.crypto import get_random_string
from django.apps import apps
from .services import procesar_bitacoras_periodo
from .filters import BitacoraFilter
from django.core.management import call_command
from django.contrib.sessions.models import Session
from xhtml2pdf import pisa
from django.core.paginator import Paginator
import django_filters
from django.template.loader import get_template
from django.utils import timezone
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE, DELETION
from django.contrib.contenttypes.models import ContentType
import openpyxl
from .utils import obtener_ruta_dijkstra, calcular_distancia, buscar_coordenadas
from django.forms import modelformset_factory
from django.db import connection, transaction
from django.http import JsonResponse, HttpResponseForbidden
from django.db.models import Q
from django.http import HttpResponse
from decimal import Decimal
import json
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

# ==========================================
# SECCIÓN CHOFER (CORREGIDA Y LIMPIA)
# ==========================================

@login_required
def dashboard_chofer(request):
    asignacion = Asignacion.objects.filter(chofer=request.user, esta_activo=True).first()
    vehiculo = asignacion.vehiculo if asignacion else None

    viaje_activo = Bitacora.objects.filter(chofer=request.user, estado_viaje='en_curso').first()

    if request.method == 'POST' and vehiculo:
        accion = request.POST.get('accion') 
        
        # ---------------------------------------------------------
        # ACCIÓN 1: INICIAR VIAJE
        # ---------------------------------------------------------
        if accion == 'iniciar':
            motivo = request.POST.get('motivo')
            responsable = request.POST.get('responsable')
            
            km_salida = int(request.POST.get('km_salida', 0))
            if vehiculo.kilometraje_actual > 0:
                km_salida = vehiculo.kilometraje_actual
                
            Bitacora.objects.create(
                vehiculo=vehiculo,
                chofer=request.user,
                estado_viaje='en_curso',
                hora_salida=timezone.now().time(), 
                km_inicial=km_salida,
                km_final=km_salida, 
                destino=motivo,
                objetivo_comision=motivo,
                responsable_viaje=responsable
            )
            messages.success(request, "Viaje iniciado. ¡Conduce con precaución!")
            return redirect('dashboard')

        # ---------------------------------------------------------
        # ACCIÓN 2: FINALIZAR VIAJE
        # ---------------------------------------------------------
        elif accion == 'finalizar' and viaje_activo:
            km_llegada = int(request.POST.get('km_llegada', 0))
            
            if km_llegada <= viaje_activo.km_inicial:
                messages.error(request, "El KM de llegada debe ser mayor al de salida.")
            else:
                viaje_activo.km_final = km_llegada
                viaje_activo.hora_llegada = timezone.now().time() 
                viaje_activo.estado_viaje = 'finalizado'
                
                si_recargo = request.POST.get('toggle_combustible')
                if si_recargo == 'on':
                    viaje_activo.nro_vale_combustible = request.POST.get('nro_vale')
                    cantidad = request.POST.get('cantidad') or 0
                    viaje_activo.cantidad_litros = cantidad
                    viaje_activo.costo_total = float(cantidad) * 3.74
                
                viaje_activo.save()
                messages.success(request, "Viaje finalizado y bitácora guardada con éxito.")
                return redirect('dashboard')

    historial = Bitacora.objects.filter(chofer=request.user, estado_viaje='finalizado').order_by('-fecha')[:5]
    
    context = {
        'asignacion': asignacion,
        'vehiculo': vehiculo,
        'viaje_activo': viaje_activo,
        'historial': historial,
        'km_actual': vehiculo.kilometraje_actual if vehiculo else 0,
    }
    return render(request, 'dashboard_chofer.html', context)

@login_required
def historial_viajes_chofer(request):
    viajes = Bitacora.objects.filter(chofer=request.user).order_by('-fecha')
    return render(request, 'chofer/historial.html', {'viajes': viajes})

@login_required
def detalle_vehiculo_chofer(request):
    asignacion = Asignacion.objects.filter(chofer=request.user, esta_activo=True).first()
    return render(request, 'chofer/vehiculo.html', {'asignacion': asignacion})

from django.http import JsonResponse
from .utils import obtener_ruta_dijkstra

@login_required
def calcular_ruta_ajax(request):
    origen = request.GET.get('origen')
    destino = request.GET.get('destino')
    km = obtener_ruta_dijkstra(origen, destino)
    
    # Asumimos rendimiento de 5 km/l según tu configuración
    consumo = round(km / 5.0, 2)
    return JsonResponse({'distancia': km, 'consumo': consumo})

@login_required
@transaction.atomic
def registrar_bitacora_completa(request):
    asignacion = Asignacion.objects.filter(chofer=request.user, esta_activo=True).first()
    
    if request.method == 'POST':
        form = BitacoraForm(request.POST)
        viaje_formset = ViajeFormSet(request.POST)
        
        if form.is_valid() and viaje_formset.is_valid():
            bitacora = form.save(commit=False)
            bitacora.vehiculo = asignacion.vehiculo
            bitacora.chofer = request.user
            
            # --- Lógica de Alerta de Consumo ---
            distancia_total = sum(v.km_fin - v.km_inicio for v in viaje_formset.save(commit=False))
            rendimiento = distancia_total / bitacora.cantidad_litros if bitacora.cantidad_litros > 0 else 0
            
            if rendimiento < asignacion.vehiculo.rendimiento_km_litro * Decimal(0.8): # Margen 20%
                bitacora.estado_validacion = 'anomalia'
                messages.warning(request, "Alerta: El consumo reportado excede los parámetros normales.")
            
            bitacora.save()
            
            # Guardar formset
            viajes = viaje_formset.save(commit=False)
            for viaje in viajes:
                viaje.bitacora = bitacora
                viaje.save()

            messages.success(request, "Bitácora y viajes registrados con éxito.")
            return redirect('dashboard')
            
    else:
        form = BitacoraForm(initial={'km_inicial': asignacion.vehiculo.kilometraje_actual if asignacion else 0})
        viaje_formset = ViajeFormSet()
        
    return render(request, 'chofer/bitacora_completa.html', {'form': form, 'formset': viaje_formset})

@login_required
def lista_vales_peajes_chofer(request):
    vales = Bitacora.objects.filter(chofer=request.user).exclude(nro_vale_combustible='')
    peajes = Peaje.objects.filter(chofer=request.user).order_by('-fecha')
    return render(request, 'chofer/vales_peajes_lista.html', {'vales': vales, 'peajes': peajes})

@login_required
def registrar_gasto_chofer(request):
    if request.user.rol != 'chofer':
        return redirect('dashboard')
        
    if request.method == 'POST':
        tipo = request.POST.get('tipo_registro', 'peaje')
        if tipo == 'peaje':
            Peaje.objects.create(
                chofer=request.user,
                lugar=request.POST.get('lugar'),
                monto=request.POST.get('monto'),
                comprobante=request.FILES.get('comprobante')
            )
            messages.success(request, "Peaje registrado exitosamente.")
        return redirect('lista_vales_peajes') # Redirige a la lista
        
    return render(request, 'chofer/registro_gasto.html')


# ==========================================
# RESTO DEL CÓDIGO (INTACTO)
# ==========================================

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
    conductores = Usuario.objects.filter(rol='chofer').prefetch_related('asignacion_set')

    context = {
        'total_vehiculos': total_vehiculos,
        'porcentaje': porcentaje,
        'total_pendientes': total_pendientes,
        'asignaciones': asignaciones,
        'conductores': conductores,
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
    if request.user.rol not in['bienes', 'admin', 'superadmin']:
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
    ultimos_registros = Bitacora.objects.all().select_related('vehiculo', 'chofer').order_by('-fecha')[:5]
    enmiendas_pendientes = Bitacora.objects.filter(solicitud_correccion=True).count()
    total_usuarios = Usuario.objects.count()
    ahora = timezone.now()
    errores_recientes = LogAuditoria.objects.filter(
        accion='error', 
        fecha_hora__gte=timezone.now() - timedelta(days=7)
    ).count()
    salud_calculada = max(100 - (errores_recientes * 5), 0)
    anomalias_pendientes = Bitacora.objects.filter(estado_validacion='anomalia').count()
    if anomalias_pendientes == 0:
        nivel_auditoria = "Nominal"
        color_auditoria = "text-slate-900"
    elif anomalias_pendientes < 5:
        nivel_auditoria = "Observado"
        color_auditoria = "text-amber-600"
    else:
        nivel_auditoria = "Crítico"
        color_auditoria = "text-red-700"
    total_vehiculos = Vehiculo.objects.count()
    logs = LogEntry.objects.all().select_related('user', 'content_type')[:5]
    config, _ = AjusteSistema.objects.get_or_create(id=1)
    tiempo_limite = ahora - timedelta(minutes=5)
    sesiones_reales = Usuario.objects.filter(ultima_actividad__gte=tiempo_limite).count()
    if sesiones_reales == 0:
        sesiones_reales = 1
    catalogos = [
        {'nombre': 'Flota de Vehículos', 'total': f"{Vehiculo.objects.count()} Unidades", 'estado': 'Sincronizado'},
        {'nombre': 'Áreas / Secretarías', 'total': f"{Area.objects.count()} Registradas", 'estado': 'Sincronizado'},
        {'nombre': 'Tipos de Combustible', 'total': f"{TipoCombustible.objects.count()} Insumos", 'estado': 'Editable'},
    ]

    context = {
        'total_usuarios': total_usuarios,
        'sesiones_activas': sesiones_reales,
        'salud': f"{salud_calculada}%",
        'nivel_auditoria': nivel_auditoria,
        'color_auditoria': color_auditoria,
        'anomalias_count': anomalias_pendientes,
        'logs': LogAuditoria.objects.all().select_related('usuario').order_by('-fecha_hora')[:5],
        'total_vehiculos': total_vehiculos,
        'catalogos': catalogos,
        'uptime': '42 días 14h',
        'db_instance': 'POSTGRES-PROD-01',
        'version': 'v4.8.2-POTOSI-GADP',
        'modo_seguro_activado': config.modo_seguro,
        'ultimos_registros': ultimos_registros,
        'enmiendas_pendientes': enmiendas_pendientes,
    }
    return render(request, 'dashboard_superadmin.html', context)

def admin_required(user):
    return user.rol in ['superadmin', 'admin']

@login_required
def lista_usuarios(request):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
    
    rol_filtrado = request.GET.get('rol')
    activos = Usuario.objects.filter(estado='activo').order_by('-id')
    inactivos = Usuario.objects.exclude(estado='activo').order_by('-fecha_baja')
    
    if rol_filtrado:
        activos = activos.filter(rol=rol_filtrado)
        inactivos = inactivos.filter(rol=rol_filtrado)

    paginator = Paginator(activos, 10) 
    page_number = request.GET.get('page')
    usuarios_obj = paginator.get_page(page_number)

    # 3. Paginación Bajas (Usa el parámetro 'page_bajas')
    paginator_inactivos = Paginator(inactivos, 10)
    page_inactivos = request.GET.get('page_bajas')
    obj_inactivos = paginator_inactivos.get_page(page_inactivos)
    
    return render(request, 'usuarios/lista.html', {
        'usuarios': usuarios_obj,
        'usuarios_inactivos': obj_inactivos,
        'rol_actual': rol_filtrado,
    })

CAMPOS_IGNORADOS = [
    'password', 'last_login', 'is_superuser', 'is_staff', 
    'is_active', 'date_joined', 'groups', 'user_permissions'
]

@login_required
@user_passes_test(admin_required)
def crear_usuario(request):
    if request.method == 'POST':
        form = UsuarioCreationForm(request.POST, request.FILES) 
        if form.is_valid():
            usuario = form.save()
            datos = model_to_dict(usuario)
            datos_limpios = {k: v for k, v in datos.items() if k not in CAMPOS_IGNORADOS}
            LogAuditoria.objects.create(
                usuario=request.user,
                usuario_nombre=request.user.username,
                rol=request.user.get_rol_display(),
                accion='creacion',
                tabla='Usuario',
                descripcion=f"Creó al usuario: {usuario.username}",
                valor_nuevo=json.dumps(model_to_dict(usuario, exclude=['password', 'foto']), default=str),
                ip=obtener_ip(request)
            )
            messages.success(request, "Usuario creado exitosamente.")
            return redirect('lista_usuarios')
    else:
        form = UsuarioCreationForm()
    return render(request, 'usuarios/form.html', {'form': form, 'editando': False})

class UsuarioChangeForm(UserChangeForm):
    password = None 
    class Meta:
        model = Usuario
        fields = ('username', 'first_name', 'last_name', 'email', 'rol', 'ci', 'licencia_conducir', 'vencimiento_licencia', 'foto')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'w-full p-3 bg-slate-50 border rounded-xl outline-none'})

@login_required
@user_passes_test(admin_required)
def editar_usuario(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk)
    campos_a_auditar = [f.name for f in usuario._meta.fields if f.name != 'password']
    valor_anterior = model_to_dict(usuario, fields=campos_a_auditar)
    
    if request.method == 'POST':
        form = UsuarioChangeForm(request.POST, request.FILES, instance=usuario)
        if form.is_valid():
            usuario_editado = form.save()
            valor_nuevo = model_to_dict(usuario_editado, fields=campos_a_auditar)
            cambios_antes = {}
            cambios_despues = {}
            CAMPOS_IGNORADOS = ['last_login', 'date_joined', 'password', 'is_superuser', 'is_staff']
            for campo, valor in valor_nuevo.items():
                if campo not in CAMPOS_IGNORADOS:
                    if valor_anterior.get(campo) != valor:
                        cambios_antes[campo] = valor_anterior.get(campo)
                        cambios_despues[campo] = valor
            
            if cambios_despues:
                LogAuditoria.objects.create(
                    usuario=request.user,
                    usuario_nombre=request.user.username,
                    rol=request.user.get_rol_display(),
                    accion='modificacion',
                    tabla='Usuario',
                    descripcion=f"Editó perfil de: {usuario.username}",
                    ip=obtener_ip(request),
                    valor_anterior=json.dumps(cambios_antes, default=str),
                    valor_nuevo=json.dumps(cambios_despues, default=str)
                )

            messages.success(request, "Usuario actualizado y cambios auditados.")
            return redirect('lista_usuarios')
    else:
        form = UsuarioChangeForm(instance=usuario)
        
    return render(request, 'usuarios/form.html', {
        'form': form, 
        'editando': True, 
        'u_edit': usuario
    })

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

    columns =['Username', 'Nombre', 'Apellido', 'Rol', 'CI', 'Email']
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
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
    
    logs = LogAuditoria.objects.all().order_by('-fecha_hora')
    # --- MOTOR DE FILTROS ---
    u_filtro = request.GET.get('usuario')
    a_filtro = request.GET.get('accion')
    r_filtro = request.GET.get('rol')
    f_inicio = request.GET.get('fecha_inicio')
    f_fin = request.GET.get('fecha_fin')

    if u_filtro: logs = logs.filter(usuario_nombre__icontains=u_filtro)
    if a_filtro: logs = logs.filter(accion=a_filtro)
    if r_filtro: logs = logs.filter(rol=r_filtro)
    if f_inicio and f_fin: logs = logs.filter(fecha_hora__date__range=[f_inicio, f_fin])
    
    paginator = Paginator(logs, 20) 
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    context = {
        'page_obj': page_obj,
        'total_logs': logs.count(),
        'logs': logs,
        'acciones': LogAuditoria.ACCIONES,
        'roles': Usuario.ROLES
    }
    return render(request, 'usuarios/logs.html', context)

@login_required
def configuracion_global(request):
    config, _ = AjusteSistema.objects.get_or_create(id=1)
    return render(request, 'usuarios/configuracion.html', {
        'modo_seguro': config.modo_seguro,
        'ultima_mod': config.ultima_modificacion
    })

@login_required
def gestion_catalogos(request):
    combustibles = TipoCombustible.objects.annotate(
        total_vehiculos=Count('vehiculos')
    ).order_by('nombre')
    vehiculos = Vehiculo.objects.all().order_by('placa')
    areas = Area.objects.all().order_by('nombre')
    
    context = {
        'vehiculos': vehiculos,
        'areas': areas,
        'combustibles': combustibles,
    }
    return render(request, 'usuarios/catalogos.html', context)

@login_required
def crear_combustible(request):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = TipoCombustibleForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Nuevo insumo registrado exitosamente.")
            return redirect('gestion_catalogos')
    else:
        form = TipoCombustibleForm()
        
    return render(request, 'catalogos/combustible_form.html', {'form': form})

@login_required
def vista_roles(request):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
    
    # Definición de capacidades por rol para dar sentido a la interfaz
    roles_data = [
        {
            'nombre': 'Super Administrador',
            'codigo': 'superadmin',
            'nivel': 'Nivel 5 - Control Total',
            'color': 'text-red-700',
            'bg': 'bg-red-50',
            'descripcion': 'Acceso absoluto al núcleo del sistema, gestión de base de datos y auditoría forense.',
            'permisos': ['Gestión de Usuarios', 'Backups SQL', 'Configuración Global', 'Enmienda de Registros'],
            'usuarios': Usuario.objects.filter(rol='superadmin').count()
        },
        {
            'nombre': 'Administrador',
            'codigo': 'admin',
            'nivel': 'Nivel 4 - Supervisión',
            'color': 'text-blue-700',
            'bg': 'bg-blue-50',
            'descripcion': 'Responsable de la validación final de bitácoras y generación de reportes quincenales.',
            'permisos': ['Reportes Oficiales', 'Monitoreo Real', 'Validación Técnica', 'Visualización Global'],
            'usuarios': Usuario.objects.filter(rol='admin').count()
        },
        {
            'nombre': 'Encargado de Activos',
            'codigo': 'activos',
            'nivel': 'Nivel 3 - Gestión de Flota',
            'color': 'text-amber-700',
            'bg': 'bg-amber-50',
            'descripcion': 'Control legal de vehículos, emisión de memorándums y gestión de kardex de choferes.',
            'permisos': ['Asignación de Vehículos', 'Registro de Memos', 'Baja de Secretarías', 'Kardex de Chofer'],
            'usuarios': Usuario.objects.filter(rol='activos').count()
        },
        {
            'nombre': 'Bienes y Servicios',
            'codigo': 'bienes',
            'nivel': 'Nivel 3 - Control de Insumos',
            'color': 'text-emerald-700',
            'bg': 'bg-emerald-50',
            'descripcion': 'Administración del inventario de combustible y supervisión de vales de carga.',
            'permisos': ['Inventario de Combustible', 'Validación de Vales', 'Control de Abastecimiento'],
            'usuarios': Usuario.objects.filter(rol='bienes').count()
        },
        {
            'nombre': 'Chofer',
            'codigo': 'chofer',
            'nivel': 'Nivel 1 - Operativo',
            'color': 'text-slate-700',
            'bg': 'bg-slate-50',
            'descripcion': 'Personal operativo encargado del registro diario de rutas y consumo en campo.',
            'permisos': ['Registro de Bitácora', 'Cálculo de Rutas', 'Registro de Peajes'],
            'usuarios': Usuario.objects.filter(rol='chofer').count()
        },
    ]
        
    return render(request, 'usuarios/roles.html', {'roles_info': roles_data})

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
    resultados_usuarios =[]
    resultados_vehiculos = []
    resultados_bitacoras =[]

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
    # Ejemplo en la función de backup:
    BitacoraActividad.objects.create(
        usuario=request.user,
        usuario_texto=request.user.username,
        rol=request.user.get_rol_display(),
        categoria='sistema',
        accion='DESCARGA DE BACKUP',
        descripcion='El superusuario descargó un respaldo completo de la base de datos.',
        ip=obtener_ip(request)
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
    anio = int(request.GET.get('anio', datetime.now().year))
    periodo = request.GET.get('periodo', 'mensual')
    mes_raw = request.GET.get('mes', '1')
    mes_map = {'ENERO': 1, 'FEBRERO': 2, 'MARZO': 3, 'ABRIL': 4, 'MAYO': 5, 'JUNIO': 6, 
               'JULIO': 7, 'AGOSTO': 8, 'SEPTIEMBRE': 9, 'OCTUBRE': 10, 'NOVIEMBRE': 11, 'DICIEMBRE': 12}
    registros, totales, saldo_final = procesar_bitacoras_periodo(chofer, anio, mes)
    
    if mes_raw.isdigit():
        mes = int(mes_raw)
    else:
        mes = mes_map.get(mes_raw.upper(), datetime.now().month)

    periodo = request.GET.get('periodo', 'mensual')
    vehiculo_asig = Asignacion.objects.filter(chofer=chofer, esta_activo=True).first()
    
    # Filtros de Bitácora
    bitacoras = Bitacora.objects.filter(chofer_id=chofer_id, chofer=chofer, fecha__year=anio, fecha__month=mes).exclude(estado_validacion='rechazado').order_by('fecha')
    if periodo == 'quincena1':
        bitacoras = bitacoras.filter(fecha__day__lte=15)
    elif periodo == 'quincena2':
        bitacoras = bitacoras.filter(fecha__day__gt=15)
    bitacoras = bitacoras.order_by('fecha')

    total_cargado = 0
    total_recorrido = 0
    total_utilizado = 0
    registros_procesados = []
    saldo_anterior = Decimal('133.90')
    saldo_actual = saldo_anterior

    for b in bitacoras:
        motivo_final = evaluar_tipo_bitacora(b)
        recorrido = b.km_final - b.km_inicial
        rendimiento = vehiculo_asig.vehiculo.rendimiento_km_litro if vehiculo_asig else Decimal('5.0')
        consumo_estimado = Decimal(recorrido) / rendimiento
        saldo_actual = saldo_actual + b.cantidad_litros - consumo_estimado
        
        registros_procesados.append({
            'fecha': b.fecha,
            'vale': b.nro_vale_combustible,
            'ingreso': b.cantidad_litros,
            'utilizado': round(consumo_estimado, 2),
            'saldo': round(saldo_actual, 2),
            'objeto': motivo_final,
            'salida': b.km_inicial,
            'llegada': b.km_final,
            'recorrido': recorrido,
        })
        total_cargado += b.cantidad_litros
        total_utilizado += consumo_estimado
        total_recorrido += recorrido
    
    context = {
        'registros': registros,
        'total_recorrido': totales['recorrido'],
        'total_cargado': totales['cargado'],
        'total_utilizado': round(totales['utilizado'], 2),
        'user': request.user,
        'chofer': chofer,
        'vehiculo': vehiculo_asig.vehiculo if vehiculo_asig else None,
        'registros': registros_procesados,
        'mes': "ENERO",
        'anio': anio,
        'km_inicial_mes': bitacoras.first().km_inicial if bitacoras.exists() else 0,
        'saldo_anterior': saldo_anterior,
        'saldo_final': round(saldo_actual, 2),
    }

    # --- LÓGICA DE EXPORTACIÓN PDF ---
    tipo = request.GET.get('tipo', 'html')
    if tipo == 'pdf':
        # USAMOS EL TEMPLATE LIMPIO
        template = get_template('reportes/planilla_pdf_solo.html')
        html = template.render(context)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="Reporte_Oficial.pdf"'
        
        # Generar PDF
        pisa_status = pisa.CreatePDF(html, dest=response)
        if pisa_status.err:
            return HttpResponse('Error al generar PDF', status=500)
        return response
    
    # Si no es PDF, renderiza el normal con el sidebar
    return render(request, 'reportes/planilla_oficial.html', context)

@login_required
def cerrar_periodo_chofer(request, chofer_id, anio, mes):
    # Bloqueamos todas las bitácoras del mes para ese chofer
    Bitacora.objects.filter(
        chofer_id=chofer_id, 
        fecha__year=anio, 
        fecha__month=mes
    ).update(reporte_cerrado=True)
    
    messages.success(request, f"Periodo {mes}/{anio} cerrado para el chofer. Los registros ya no pueden ser modificados.")
    return redirect('reporte_por_chofer')

@login_required
def lista_registros_all(request):
    if request.user.rol not in ['bienes', 'admin', 'superadmin']:
        return redirect('dashboard')
    f = BitacoraFilter(request.GET, queryset=Bitacora.objects.all().select_related('vehiculo', 'chofer').order_by('-fecha'))
    
    # 2. Paginamos el queryset FILTRADO (f.qs)
    paginator = Paginator(f.qs, 10) 
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'admin_potosi/registros_list.html', {
        'filter': f,
        'page_obj': page_obj    
    })

@login_required
def validar_registros(request):
    pendientes = Bitacora.objects.filter(estado_validacion='pendiente').order_by('-fecha')
    return render(request, 'admin_potosi/validar.html', {'pendientes': pendientes})

@login_required
def monitoreo_tiempo_real(request):
    if request.user.rol not in ['admin', 'superadmin', 'activos']:
        return redirect('dashboard')

    choferes = Usuario.objects.filter(rol='chofer')
    for c in choferes:
        # Buscamos si tiene un viaje en curso
        viaje_actual = Bitacora.objects.filter(chofer=c, estado_viaje='en_curso').first()
        
        if viaje_actual:
            c.estado_actual = "En Viaje"
            c.color = "bg-red-500"
            c.ubicacion = f"{viaje_actual.origen} a {viaje_actual.destino}"
            c.ultima_hora = viaje_actual.hora_salida
        else:
            # Si no hay viaje en curso, buscamos el último finalizado
            ultimo_viaje = Bitacora.objects.filter(chofer=c, estado_viaje='finalizado').order_by('-fecha').first()
            c.estado_actual = "Disponible"
            c.color = "bg-green-500"
            c.ubicacion = f"Último destino: {ultimo_viaje.destino}" if ultimo_viaje else "En Base"
            c.ultima_hora = ultimo_viaje.hora_llegada if ultimo_viaje else "--:--"

    return render(request, 'admin_potosi/monitoreo.html', {'choferes': choferes})

@login_required
def seleccionar_chofer_reporte(request):
    choferes = Usuario.objects.filter(rol='chofer')
    return render(request, 'admin_potosi/seleccionar_chofer.html', {'choferes': choferes})

@login_required
def seleccionar_vehiculo_reporte(request):
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
        'mes': "ABRIL",
        'anio': anio_actual,
    }
    return render(request, 'reportes/planilla_oficial.html', context)

@login_required
@transaction.atomic 
def crear_asignacion(request):
    if request.user.rol not in ['activos', 'admin', 'superadmin']:
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = AsignacionForm(request.POST, request.FILES)
        
        if form.is_valid():
            vehiculo = form.cleaned_data['vehiculo']
            chofer = form.cleaned_data['chofer']
            area_seleccionada = form.cleaned_data['area']

            if Asignacion.objects.filter(chofer=chofer, esta_activo=True).exists():
                messages.error(request, f"ERROR DE AUDITORÍA: EL CONDUCTOR {chofer.get_full_name().upper()} YA TIENE UN VEHÍCULO BAJO SU RESPONSABILIDAD.")
                return render(request, 'catalogos/asignar_form.html', {'form': form})

            if Asignacion.objects.filter(vehiculo=vehiculo, esta_activo=True).exists():
                messages.error(request, f"ERROR DE ACTIVOS: EL VEHÍCULO {vehiculo.placa} YA SE ENCUENTRA ASIGNADO A OTRO CONDUCTOR.")
                return render(request, 'catalogos/asignar_form.html', {'form': form})

            chofer.area = area_seleccionada
            chofer.save()
            
            asignacion = form.save(commit=False)
            asignacion.area = area_seleccionada 
            asignacion.save()
            
            messages.success(request, f"ASIGNACIÓN EXITOSA: Vehículo {vehiculo.placa} vinculado a {chofer.get_full_name()} en {area_seleccionada.nombre}")
            return redirect('dashboard')
            
        else:
            messages.error(request, "POR FAVOR CORRIJA LOS ERRORES EN EL FORMULARIO.")
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
def vista_vales_peajes(request):
    bitacoras = Bitacora.objects.exclude(nro_vale_combustible='').order_by('-fecha')
    peajes = Peaje.objects.all().order_by('-fecha')
    
    return render(request, 'admin_potosi/vales_peajes.html', {
        'bitacoras': bitacoras,
        'peajes': peajes
    })

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
def cambiar_estado_bitacora(request, pk, nuevo_estado):
    if request.user.rol not in['admin', 'superadmin', 'bienes','activos']:
        return redirect('dashboard')
        
    bitacora = get_object_or_404(Bitacora, pk=pk)
    bitacora.estado_validacion = nuevo_estado
    bitacora.save()
    identificador = bitacora.nro_vale_combustible if bitacora.nro_vale_combustible else f"Vehículo {bitacora.vehiculo.placa}"

    messages.success(request, f"Registro {identificador} marcado como {nuevo_estado.upper()} exitosamente.")
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def lista_vales_peajes(request):
    if request.user.rol != 'chofer':
        return redirect('dashboard')
        
    vales = Bitacora.objects.filter(chofer=request.user).exclude(nro_vale_combustible='')
    peajes = Peaje.objects.filter(chofer=request.user).order_by('-fecha')
    
    return render(request, 'chofer/vales_peajes_lista.html', {'vales': vales, 'peajes': peajes})

@login_required
def registrar_peaje(request):
    # Validamos que solo el chofer pueda registrar peajes
    if request.user.rol != 'chofer':
        return redirect('dashboard')
        
    if request.method == 'POST':
        # Guardamos el peaje con la foto del comprobante
        Peaje.objects.create(
            chofer=request.user,
            lugar=request.POST.get('lugar'),
            monto=request.POST.get('monto'),
            comprobante=request.FILES.get('comprobante') 
        )
        messages.success(request, "Peaje registrado exitosamente.")
        return redirect('lista_vales_peajes') 
        
    return render(request, 'chofer/registro_peaje_form.html')

@login_required
def detalle_validacion(request, bitacora_id):
    if request.user.rol not in ['admin', 'superadmin', 'bienes']:
        return redirect('dashboard')
        
    bitacora = get_object_or_404(Bitacora, id=bitacora_id)
    viajes = bitacora.viajes.all()
    peajes = Peaje.objects.filter(chofer=bitacora.chofer, fecha__date=bitacora.fecha.date())
    identificador = bitacora.nro_vale_combustible if bitacora.nro_vale_combustible else f"PLACA {bitacora.vehiculo.placa}"
    
    context = {
        'bitacora': bitacora,
        'identificador': identificador,
        'viajes': viajes,
        'peajes': peajes,
    }
    return render(request, 'admin_potosi/detalle_solicitud.html', context)

@login_required
def procesar_validacion(request, bitacora_id):
    bitacora = get_object_or_404(Bitacora, id=bitacora_id)
    if request.method == 'POST':
        bitacora.estado_validacion = request.POST.get('estado')
        bitacora.observacion_admin = request.POST.get('observacion')
        bitacora.save()
        messages.success(request, f"Registro {bitacora.nro_vale_combustible} procesado exitosamente.")
    return redirect('validar_registros')

@login_required
def reporte_diario_detallado(request, bitacora_id):
    bitacora = get_object_or_404(Bitacora, id=bitacora_id)
    viajes = bitacora.viajes.all().order_by('hora_inicio')
    
    context = {
        'bitacora': bitacora,
        'viajes': viajes,
        'chofer': bitacora.chofer,
        'vehiculo': bitacora.vehiculo,
    }
    return render(request, 'admin_potosi/reporte_diario.html', context)

@login_required
def historial_diario_chofer(request, chofer_id):
    if request.user.rol not in ['admin', 'superadmin', 'activos']:
        return redirect('dashboard')

    chofer = get_object_or_404(Usuario, id=chofer_id)
    hoy = timezone.now().date()
    
    # Obtenemos las bitácoras del día del chofer
    bitacoras = Bitacora.objects.filter(chofer=chofer, fecha__date=hoy).order_by('hora_salida')
    
    return render(request, 'admin_potosi/historial_diario.html', {
        'chofer': chofer,
        'bitacoras': bitacoras,
        'fecha': hoy
    })

@login_required
@transaction.atomic # Si algo falla, se cancela todo
def registrar_chofer_con_asignacion(request):
    if request.method == 'POST':
        form = RegistroChoferCompletoForm(request.POST, request.FILES)
        if form.is_valid():
            # Crear Chofer
            chofer = form.save(commit=False)
            chofer.rol = 'chofer'
            chofer.area = form.cleaned_data['area']
            chofer.set_password('Potosi123') # Contraseña por defecto
            chofer.save()
            
            # Crear Asignación
            Asignacion.objects.create(
                vehiculo=form.cleaned_data['vehiculo'],
                chofer=chofer,
                nro_memorandum=form.cleaned_data['nro_memorandum'],
                documento_acta=form.cleaned_data['documento_acta']
            )
            messages.success(request, "Chofer y Vehículo asignados correctamente.")
            return redirect('dashboard_activos')
            
    else:
        form = RegistroChoferCompletoForm()
    return render(request, 'activos/registro_chofer.html', {'form': form})

@login_required
def lista_secretarias(request):
    if request.user.rol not in['activos', 'admin', 'superadmin']:
        return redirect('dashboard')
    
    secretarias = Area.objects.all().order_by('nombre')
    return render(request, 'activos/secretarias.html', {'secretarias': secretarias})

@login_required
def crear_secretaria(request):
    if request.user.rol not in ['activos', 'admin', 'superadmin']:
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = AreaForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Secretaría registrada exitosamente en el catálogo institucional.")
            return redirect('lista_secretarias')
    else:
        form = AreaForm(initial={'estado': True})
        
    return render(request, 'activos/secretaria_form.html', {'form': form})

@login_required
def dar_baja_chofer(request, asignacion_id):
    asignacion = get_object_or_404(Asignacion, id=asignacion_id)
    
    if request.method == 'POST':
        asignacion.esta_activo = False
        asignacion.fecha_fin = timezone.now().date()
        asignacion.motivo_baja = request.POST.get('motivo')
        asignacion.documento_baja = request.FILES.get('documento_baja')
        asignacion.save()
        
        # Opcional: Limpiar el área del usuario al dar de baja
        asignacion.chofer.area = None
        asignacion.chofer.save()
        
        messages.success(request, "Chofer dado de baja exitosamente.")
        return redirect('historial_asignaciones')
        
    return render(request, 'activos/baja_form.html', {'asignacion': asignacion})

@login_required
def ver_kardex_chofer(request, chofer_id):
    if request.user.rol not in ['activos', 'admin', 'superadmin']:
        return redirect('dashboard')
        
    chofer = get_object_or_404(Usuario, id=chofer_id)
    # Obtenemos todo el historial de cambios de vehículo
    historial = Asignacion.objects.filter(chofer=chofer).order_by('-fecha_asignacion')
    
    context= {
        'chofer': chofer,
        'historial': historial,
        'vehiculo_actual': historial.filter(esta_activo=True).first(),
    }
    return render(request, 'activos/kardex.html', context)

@login_required
def cambio_secretaria(request, chofer_id):
    chofer = get_object_or_404(Usuario, id=chofer_id)
    if request.method == 'POST':
        # 1. Registrar baja de secretaría actual (puedes crear un modelo de 'HistoricoAreas')
        # 2. Registrar alta en nueva secretaría
        nueva_area = request.POST.get('area')
        chofer.area_id = nueva_area
        chofer.save()
        messages.success(request, f"Cambio de secretaría realizado para {chofer.get_full_name()}.")
        return redirect('lista_usuarios')
    
    return render(request, 'activos/cambio_secretaria.html', {'chofer': chofer, 'areas': Area.objects.all()})

@login_required
@transaction.atomic
def enviar_a_mantenimiento(request, vehiculo_id):
    vehiculo = get_object_or_404(Vehiculo, id=vehiculo_id)
    
    if request.method == 'POST':
        # 1. Creamos el registro de taller
        RegistroMantenimiento.objects.create(
            vehiculo=vehiculo,
            fecha_ingreso=request.POST.get('fecha_ingreso'),
            motivo=request.POST.get('motivo'),
            observaciones=request.POST.get('observaciones'),
            encargado_taller=request.POST.get('taller')
        )
        
        # 2. Cambiamos el estado del activo para bloquear asignaciones
        vehiculo.estado = 'mantenimiento'
        vehiculo.save()
        
        # 3. Si el vehículo tenía una asignación activa, la finalizamos (opcional según norma interna)
        Asignacion.objects.filter(vehiculo=vehiculo, esta_activo=True).update(esta_activo=False)
        
        messages.warning(request, f"El vehículo {vehiculo.placa} ha sido enviado a mantenimiento y bloqueado para uso.")
        return redirect('gestion_catalogos')
        
    return render(request, 'activos/mantenimiento_form.html', {'vehiculo': vehiculo})

@login_required
def finalizar_mantenimiento(request, registro_id):
    registro = get_object_or_404(RegistroMantenimiento, id=registro_id)
    if request.method == 'POST':
        registro.fecha_salida = timezone.now().date()
        registro.finalizado = True
        registro.save()
        
        # Liberamos el vehículo
        vehiculo = registro.vehiculo
        vehiculo.estado = 'operacional'
        vehiculo.save()
        
        messages.success(request, f"Vehículo {vehiculo.placa} habilitado nuevamente.")
        return redirect('gestion_catalogos')
    
@login_required
@transaction.atomic
def finalizar_mantenimiento(request, vehiculo_id):
    if request.user.rol not in ['activos', 'admin', 'superadmin']:
        return redirect('dashboard')
        
    vehiculo = get_object_or_404(Vehiculo, id=vehiculo_id)
    
    # Buscamos el registro de mantenimiento que sigue abierto
    registro = RegistroMantenimiento.objects.filter(vehiculo=vehiculo, finalizado=False).last()
    
    if registro:
        registro.fecha_salida = timezone.now().date()
        registro.finalizado = True
        registro.save()
    
    # Devolvemos el vehículo al estado operacional
    vehiculo.estado = 'operacional'
    vehiculo.save()
    
    messages.success(request, f"El vehículo {vehiculo.placa} ha sido habilitado y ya puede ser asignado.")
    return redirect('gestion_catalogos')

@login_required
@transaction.atomic
def dar_baja_asignacion(request, asignacion_id):
    if request.user.rol not in ['activos', 'admin', 'superadmin']:
        return redirect('dashboard')
        
    asignacion = get_object_or_404(Asignacion, id=asignacion_id)
    
    if request.method == 'POST':
        # 1. Registramos los datos de la baja
        asignacion.esta_activo = False
        asignacion.fecha_fin = timezone.now().date()
        asignacion.motivo_baja = request.POST.get('motivo')
        asignacion.documento_baja = request.FILES.get('documento_baja')
        asignacion.save()
        
        # 2. Opcional: El vehículo vuelve a estar disponible (opcional según tu lógica)
        # 3. El chofer queda sin área asignada para que pueda ser re-asignado formalmente
        chofer = asignacion.chofer
        chofer.area = None
        chofer.save()

        messages.success(request, f"Baja procesada. El chofer {chofer.get_full_name()} ha sido liberado para nueva asignación.")
        return redirect('dashboard')
        
    return render(request, 'activos/baja_form.html', {'asignacion': asignacion})

@login_required
def habilitar_vehiculo(request, vehiculo_id):
    vehiculo = get_object_or_404(Vehiculo, id=vehiculo_id)
    # Cerramos el mantenimiento pendiente
    RegistroMantenimiento.objects.filter(vehiculo=vehiculo, finalizado=False).update(
        finalizado=True, 
        fecha_salida=timezone.now().date()
    )
    # Cambiamos estado del vehículo
    vehiculo.estado = 'operacional'
    vehiculo.save()
    messages.success(request, f"Vehículo {vehiculo.placa} habilitado correctamente.")
    return redirect('gestion_catalogos')

@login_required
@transaction.atomic
def traspaso_secretaria(request, chofer_id):
    chofer = get_object_or_404(Usuario, id=chofer_id)
    asignacion_actual = Asignacion.objects.filter(chofer=chofer, esta_activo=True).first()

    if request.method == 'POST':
        form = TraspasoAreaForm(request.POST, request.FILES)
        if form.is_valid():
            # 1. Cerrar la asignación actual (manteniendo el histórico)
            if asignacion_actual:
                asignacion_actual.esta_activo = False
                asignacion_actual.fecha_fin = timezone.now().date()
                asignacion_actual.motivo_baja = "Traspaso de Secretaría"
                asignacion_actual.save()
                vehiculo = asignacion_actual.vehiculo
            else:
                # Si no tenía vehículo, solo necesitamos el dato del área
                vehiculo = None

            # 2. Actualizar el área en el perfil del Chofer
            nueva_area = form.cleaned_data['nueva_area']
            chofer.area = nueva_area
            chofer.save()

            # 3. Crear nueva asignación con el MISMO vehículo pero NUEVA área
            if vehiculo:
                Asignacion.objects.create(
                    vehiculo=vehiculo,
                    chofer=chofer,
                    nro_memorandum=form.cleaned_data['nro_memorandum'],
                    documento_acta=form.cleaned_data['documento_traspaso'],
                    esta_activo=True
                )

            messages.success(request, f"Traspaso exitoso: {chofer.get_full_name()} ahora pertenece a {nueva_area.nombre}")
            return redirect('dashboard')
    else:
        form = TraspasoAreaForm()

    return render(request, 'activos/traspaso_form.html', {'form': form, 'chofer': chofer, 'asig': asignacion_actual})

@login_required
@user_passes_test(lambda u: u.rol == 'superadmin')
def gestion_usuarios_master(request):
    query = request.GET.get('q', '')
    estado_filter = request.GET.get('estado', '')
    
    usuarios = Usuario.objects.all().order_by('-id')
    
    if query:
        usuarios = usuarios.filter(Q(username__icontains=query) | Q(first_name__icontains=query) | Q(ci__icontains=query))
    if estado_filter:
        usuarios = usuarios.filter(estado=estado_filter)

    return render(request, 'superadmin/usuarios_list.html', {'usuarios': usuarios})

@login_required
@user_passes_test(lambda u: u.rol == 'superadmin')
def dar_baja_usuario(request, pk):
    usuario_objetivo = get_object_or_404(Usuario, pk=pk)
    
    if request.method == 'POST':
        motivo = request.POST.get('motivo')
        usuario_objetivo.estado = 'inactivo'
        usuario_objetivo.is_active = False # Bloquea el acceso al sistema
        usuario_objetivo.fecha_baja = timezone.now()
        usuario_objetivo.motivo_baja = motivo
        usuario_objetivo.baja_por = request.user
        usuario_objetivo.save()
        
        messages.warning(request, f"El usuario {usuario_objetivo.username} ha sido dado de BAJA del sistema.")
        return redirect('lista_usuarios')
        
    return render(request, 'usuarios/confirmar_baja.html', {'usuario': usuario_objetivo})

@login_required
@user_passes_test(lambda u: u.rol == 'superadmin')
def reset_password_admin(request, pk):
    user_to_reset = get_object_or_404(Usuario, pk=pk)
    
    caracteres_aleatorios = get_random_string(length=3, allowed_chars='abcdefghjkmnpqrstuvwxyz23456789')
    ci_seguro = user_to_reset.ci[-4:] if user_to_reset.ci and len(user_to_reset.ci) >= 4 else "0000"
    nueva_pass = f"Potosi.{ci_seguro}-{caracteres_aleatorios}"
    
    user_to_reset.set_password(nueva_pass)
    user_to_reset.save()
    LogAuditoria.objects.create(
        usuario=request.user,
        usuario_nombre=request.user.username,
        rol=request.user.get_rol_display(),
        accion='modificacion',
        tabla='Usuario / Seguridad',
        descripcion=f"RESETEO DE CONTRASEÑA: El Superadmin forzó el cambio de credenciales para el usuario {user_to_reset.username}.",
        ip=obtener_ip(request),
        valor_nuevo=json.dumps({
            "tipo_accion": "RESET_PASSWORD_FORCE",
            "target_user": user_to_reset.username,
            "target_id": user_to_reset.id,
            "status": "SUCCESS"
        }, default=str)
    )
    messages.success(request, f"USUARIO REACTIVADO. La nueva contraseña para {user_to_reset.username} es: {nueva_pass}", extra_tags='static')
    return redirect('lista_usuarios')

@login_required
@user_passes_test(lambda u: u.rol == 'superadmin')
def reactivar_usuario(request, pk):
    usuario_objetivo = get_object_or_404(Usuario, pk=pk)
    usuario_objetivo.estado = 'activo'
    usuario_objetivo.is_active = True
    usuario_objetivo.fecha_baja = None
    usuario_objetivo.save()
    messages.success(request, f"El usuario {usuario_objetivo.username} ha sido REACTIVADO.")
    return redirect('lista_usuarios')

@login_required
@transaction.atomic
def enmienda_bitacora_critica(request, pk):
    # Seguridad absoluta: Solo Superadmin
    if request.user.rol != 'superadmin':
        return HttpResponseForbidden("Acceso denegado. Solo la alta dirección puede enmendar registros.")

    bitacora = get_object_or_404(Bitacora, pk=pk)
    # Guardamos el valor anterior para el LOG antes de cambiar nada
    valor_anterior = model_to_dict(bitacora)

    if request.method == 'POST':
        form = EnmiendaBitacoraForm(request.POST, request.FILES, instance=bitacora)
        if form.is_valid():
            # 1. Creamos el respaldo legal de la corrección
            JustificacionEdicion.objects.create(
                superusuario=request.user,
                tabla_afectada='Bitacora',
                registro_id=bitacora.id,
                motivo=form.cleaned_data['motivo_enmienda'],
                documento_respaldo=request.FILES['documento_respaldo'],
                observacion_adicional=f"Se corrigió de {valor_anterior['km_final']} a {bitacora.km_final} KM."
            )

            # 2. Guardamos la bitácora corregida
            form.save()

            # 3. Disparamos el Log de Auditoría Forense
            registrar_auditoria(bitacora, 'modificacion', anterior=valor_anterior)

            messages.success(request, "El registro ha sido enmendado legalmente con su respaldo.")
            return redirect('registros_visualizacion')
    else:
        form = EnmiendaBitacoraForm(instance=bitacora)

    return render(request, 'superadmin/enmienda_form.html', {'form': form, 'bitacora': bitacora})

@login_required
def centro_backups(request):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')

    if request.method == 'POST':
        # Mapeo de opciones a modelos
        mapeo = {
            'usuarios': ['Usuario'],
            'vehiculos': ['Vehiculo'],
            'bitacoras': ['Bitacora', 'Viaje'],
            'combustible': ['ValeCombustible', 'InventarioCombustible'],
            'auditoria': ['LogAuditoria'],
            'secretarias': ['Area'],
        }

        seleccionados = request.POST.getlist('modulos')
        sql_final = "-- BACKUP SOBERANIA POTOSI\n"
        sql_final += f"-- Generado el: {timezone.now()}\n"
        sql_final += f"-- Generado por: {request.user.username}\n\n"

        for item in seleccionados:
            modelos = mapeo.get(item, [])
            for m in modelos:
                sql_final += f"-- Módulo: {item} | Tabla: {m}\n"
                sql_final += generar_sql_insert(m) + "\n\n"

        # Registro en Log de Seguridad
        LogAuditoria.objects.create(
            usuario=request.user,
            usuario_nombre=request.user.username,
            accion='backup',
            descripcion=f"Descarga de Backup SQL de: {', '.join(seleccionados)}",
            ip=obtener_ip(request)
        )

        response = HttpResponse(sql_final, content_type='application/sql')
        filename = f"Backup_Potosi_{timezone.now().strftime('%d_%m_%Y')}.sql"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    return render(request, 'superadmin/backups.html')

@login_required
def backup_total_sql(request):
    if request.user.rol != 'superadmin':
        return HttpResponse("No autorizado", status=403)

    # 1. Obtenemos todos los modelos de la app 'gestion'
    app_config = apps.get_app_config('gestion')
    modelos = [model.__name__ for model in app_config.get_models()]

    # 2. Construcción del encabezado del archivo
    sql_final = "-- ==================================================\n"
    sql_final += "-- BACKUP TOTAL SOBERANÍA POTOSÍ (ESTRUCTURA Y DATOS)\n"
    sql_final += f"-- Generado: {timezone.now()}\n"
    sql_final += f"-- Superusuario: {request.user.username}\n"
    sql_final += "-- ==================================================\n\n"
    
    # Desactivar restricciones de llaves foráneas para permitir la importación limpia
    sql_final += "SET CONSTRAINTS ALL DEFERRED;\n\n"

    # 3. Generamos los INSERT para cada tabla del sistema
    for m in modelos:
        sql_final += f"-- TABLA: {m}\n"
        sql_final += generar_sql_insert(m) + "\n\n"

    # 4. Registrar la acción en el Log de Auditoría Forense
    LogAuditoria.objects.create(
        usuario=request.user,
        usuario_nombre=request.user.username,
        accion='backup',
        tabla='BASE DE DATOS COMPLETA',
        descripcion="EJECUCIÓN DE BACKUP TOTAL DEL SISTEMA (.SQL)",
        ip=obtener_ip(request)
    )

    # 5. Retornar el archivo para descarga inmediata
    response = HttpResponse(sql_final, content_type='application/sql')
    response['Content-Disposition'] = 'attachment; filename="backup_completo_potosi.sql"'
    return response

@login_required
def restaurar_backup(request):
    if request.user.rol != 'superadmin':
        return HttpResponse("No autorizado", status=403)

    if request.method == 'POST' and request.FILES.get('archivo_sql'):
        sql_file = request.FILES['archivo_sql']
        
        if not sql_file.name.endswith('.sql'):
            messages.error(request, "Error: Solo se permiten archivos .sql")
            return redirect('restaurar_backup')

        try:
            # Leer el archivo
            queries = sql_file.read().decode('utf-8')

            with transaction.atomic():
                with connection.cursor() as cursor:
                    # 1. DESACTIVAR RESTRICCIONES TEMPORALMENTE (Para evitar errores de llaves foráneas)
                    cursor.execute("SET CONSTRAINTS ALL DEFERRED;")

                    # 2. LIMPIAR TODAS LAS TABLAS Y REINICIAR CONTADORES (IDs)
                    # He puesto todas tus tablas aquí:
                    tablas = [
                        "gestion_viaje", "gestion_peaje", "gestion_bitacora", 
                        "gestion_asignacion", "gestion_vehiculo", "gestion_area", 
                        "gestion_tipocombustible", "gestion_inventariocombustible",
                        "gestion_logauditoria", "gestion_ajustesistema"
                    ]
                    
                    # Ejecutamos la limpieza masiva
                    cursor.execute(f"TRUNCATE TABLE {', '.join(tablas)} RESTART IDENTITY CASCADE;")

                    # 3. EJECUTAR EL CONTENIDO DEL SQL
                    cursor.execute(queries)

            messages.success(request, "SISTEMA RESTAURADO: La base de datos se ha actualizado con éxito desde el respaldo.")
            return redirect('dashboard')

        except Exception as e:
            # Si algo falla, el 'transaction.atomic' hace que no se borre nada
            messages.error(request, f"ERROR CRÍTICO DE INTEGRIDAD: {str(e).upper()}")
            return redirect('restaurar_backup')

    return render(request, 'superadmin/restaurar.html')

@login_required
def dar_baja_vehiculo(request, pk):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
        
    vehiculo = get_object_or_404(Vehiculo, pk=pk)
    
    valor_anterior = {'estado': vehiculo.estado}
    
    vehiculo.estado = 'inactivo'
    vehiculo.save()
    
    LogAuditoria.objects.create(
        usuario=request.user,
        usuario_nombre=request.user.username,
        rol=request.user.get_rol_display(),
        accion='eliminacion', 
        tabla='Vehiculo',
        ip=obtener_ip(request),
        valor_anterior=valor_anterior,
        valor_nuevo={'estado': 'inactivo'},
        descripcion=f"Baja lógica del vehículo con placa {vehiculo.placa}."
    )
    
    messages.warning(request, f"El vehículo {vehiculo.placa} ha sido dado de baja del sistema.")
    return redirect('gestion_catalogos')

@login_required
def reactivar_vehiculo(request, pk):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
        
    vehiculo = get_object_or_404(Vehiculo, pk=pk)
    
    # Guardamos el estado anterior para la auditoría
    valor_anterior = {'estado': vehiculo.estado}
    
    # Cambiamos el estado a operacional
    vehiculo.estado = 'operacional'
    vehiculo.save()
    
    # Registramos la acción en el Log de Auditoría
    LogAuditoria.objects.create(
        usuario=request.user,
        usuario_nombre=request.user.username,
        rol=request.user.get_rol_display(),
        accion='modificacion', 
        tabla='Vehiculo',
        ip=obtener_ip(request),
        valor_anterior=valor_anterior,
        valor_nuevo={'estado': 'operacional'},
        descripcion=f"Reactivación del vehículo con placa {vehiculo.placa}."
    )
    
    messages.success(request, f"El vehículo {vehiculo.placa} ha sido reactivado y está disponible para asignación.")
    return redirect('gestion_catalogos')

@login_required
def editar_secretaria(request, pk):
    # Solo personal administrativo puede editar
    if request.user.rol not in ['activos', 'admin', 'superadmin']:
        return redirect('dashboard')
        
    area = get_object_or_404(Area, pk=pk)
    
    if request.method == 'POST':
        form = AreaForm(request.POST, instance=area)
        if form.is_valid():
            form.save()
            messages.success(request, f"La secretaría '{area.nombre}' ha sido actualizada.")
            return redirect('gestion_catalogos')
    else:
        form = AreaForm(instance=area)
        
    return render(request, 'activos/secretaria_form.html', {
        'form': form, 
        'editando': True,
        'area': area
    })

# gestion/views.py
from .forms import TipoCombustibleForm

@login_required
def editar_combustible(request, pk):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
        
    combustible = get_object_or_404(TipoCombustible, pk=pk)
    
    if request.method == 'POST':
        form = TipoCombustibleForm(request.POST, instance=combustible)
        if form.is_valid():
            form.save()
            messages.success(request, "Tipo de combustible actualizado correctamente.")
            return redirect('gestion_catalogos')
    else:
        form = TipoCombustibleForm(instance=combustible)
        
    return render(request, 'catalogos/combustible_form.html', {'form': form, 'combustible': combustible})

@login_required
def kardex_logs_usuario(request, user_id):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
        
    usuario_auditado = get_object_or_404(Usuario, id=user_id)
    
    logs = LogAuditoria.objects.filter(usuario=usuario_auditado).order_by('-fecha_hora')
    
    return render(request, 'usuarios/kardex_logs.html', {
        'usuario_auditado': usuario_auditado,
        'logs': logs
    })

@login_required
def procesar_baja_usuario(request, pk):
    usuario = get_object_or_404(Usuario, pk=pk)
    if request.method == 'POST':
        usuario.estado = 'inactivo' # O el nombre que uses para las bajas
        usuario.is_active = False   # Desactiva el acceso al sistema
        usuario.fecha_baja = timezone.now()
        usuario.motivo_baja = request.POST.get('motivo')
        usuario.save()
        
        # Registrar en Logs
        registrar_auditoria(request, 'baja', 'Usuario', f"Baja de usuario {usuario.username}")
        
        messages.warning(request, f"El usuario {usuario.username} ha sido pasado al archivo de bajas.")
        return redirect('lista_usuarios')
    
@login_required
def solicitar_correccion(request, bitacora_id):
    bitacora = get_object_or_404(Bitacora, id=bitacora_id, chofer=request.user)
    bitacora.solicitud_correccion = True
    bitacora.save()
    
    LogAuditoria.objects.create(
        usuario=request.user,
        usuario_nombre=request.user.username,
        accion='modificacion',
        tabla='Bitacora',
        descripcion=f"El chofer solicitó corrección para el vale {bitacora.nro_vale_combustible}",
        ip=obtener_ip(request)
    )
    
    messages.warning(request, "Solicitud enviada al Superadministrador.")
    return redirect('historial_chofer')