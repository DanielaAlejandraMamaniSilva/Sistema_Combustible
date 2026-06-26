from django.shortcuts import render, redirect, get_object_or_404
from .models import Usuario, Vehiculo, Asignacion, Bitacora, Area, TipoCombustible, AjusteSistema, InventarioCombustible, Peaje, RegistroMantenimiento,BitacoraActividad, JustificacionEdicion, LogAuditoria, ReporteFolio
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import get_user_model
from django.forms.models import model_to_dict
from django.db.models import Sum, F, Count
from .forms import UsuarioCreationForm, UserChangeForm, VehiculoForm, AsignacionForm, BitacoraForm, ViajeFormSet, RegistroChoferCompletoForm, AreaForm, TraspasoAreaForm, EnmiendaBitacoraForm, TipoCombustibleForm
from django.contrib import messages
from .utils import obtener_ip, generar_sql_insert,evaluar_tipo_bitacora, registrar_auditoria
from django.utils.crypto import get_random_string
from django.apps import apps
from .filters import BitacoraFilter
from django.core.management import call_command
from xhtml2pdf import pisa
from django.core.paginator import Paginator
from django.utils.timezone import localtime
from django.template.loader import get_template
from django.utils import timezone
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType
import openpyxl
import calendar
from django.db import transaction, models
from django.http import HttpResponseForbidden
from django.db.models import Q
from django.http import HttpResponse
from decimal import Decimal
import json
from django.db.models.functions import TruncDate
import io
from django.conf import settings 
from django.http import JsonResponse
from datetime import datetime, timedelta, date

@login_required
def dashboard_view(request):
    config, _ = AjusteSistema.objects.get_or_create(id=1)
    if request.user.rol == 'superadmin':
        return dashboard_superadmin(request)
    
    if config.modo_seguro:
        return render(request, 'mantenimiento.html')
    
    if request.user.rol == 'chofer':
        return dashboard_chofer(request)
    
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
    viaje_activo = Bitacora.objects.filter(chofer=request.user, estado_viaje='en_curso').first()
    # --- LÓGICA DE ARRASTRE DE SALDO (MATRIZ) ---
    ultimo_registro = Bitacora.objects.filter(vehiculo=vehiculo, estado_viaje='finalizado').order_by('-fecha', '-id').first()
    
    if ultimo_registro:
        saldo_para_nuevo_viaje = ultimo_registro.saldo_actual
    elif vehiculo:
        saldo_para_nuevo_viaje = vehiculo.combustible_inicial
    else:
        saldo_para_nuevo_viaje = Decimal('0.00')

    alerta_rendimiento = False
    if vehiculo:
        dias_desde_update = (timezone.now().date() - vehiculo.fecha_actualizacion_rendimiento).days
        if dias_desde_update >= 15:
            alerta_rendimiento = True
        ultima_bitacora_este_auto = Bitacora.objects.filter(
            vehiculo=vehiculo, 
            estado_viaje='finalizado'
        ).order_by('-fecha', '-id').first()
        
        if ultima_bitacora_este_auto:
            km_actual = ultima_bitacora_este_auto.km_final
        else:
            km_actual = vehiculo.kilometraje_actual
    else:
        km_actual = 0

    # --- LÓGICA DEL GRÁFICO ---
    ahora_bolivia = timezone.localtime(timezone.now())
    hoy = ahora_bolivia.date()
    lunes_semana = hoy - timedelta(days=hoy.weekday())
    datos_grafico = []
    nombres_dias = ['LUN', 'MAR', 'MIE', 'JUE', 'VIE', 'SAB', 'DOM']
    
    for i in range(7):
        fecha_dia = lunes_semana + timedelta(days=i)
        stats = Bitacora.objects.filter(
            chofer=request.user, 
            fecha__date=fecha_dia, 
            estado_viaje='finalizado'
        ).aggregate(
            km=Sum(F('km_final') - F('km_inicial')),
            lts=Sum('cantidad_litros')
        )
        
        km = float(stats['km'] or 0)
        lts = float(stats['lts'] or 0)
        eficiencia = round(km / lts, 1) if lts > 0 else 0

        if eficiencia > 0:
            altura = min(int((eficiencia / 20) * 100), 100)
            if altura < 20: altura = 20 
        else:
            altura = 15
        
        datos_grafico.append({
            'dia': nombres_dias[i],
            'eficiencia': eficiencia,
            'altura': altura,
            'es_hoy': fecha_dia == hoy,
        })
    historial = Bitacora.objects.filter(chofer=request.user, estado_viaje='finalizado').order_by('-fecha')[:5]
    ultima_eficiencia = 0
    if historial.exists():
        ult = historial[0]
        dist = ult.km_final - ult.km_inicial
        if ult.cantidad_litros > 0:
            ultima_eficiencia = round(float(dist) / float(ult.cantidad_litros), 1)

    if request.method == 'POST' and vehiculo:
        accion = request.POST.get('accion') 
        
        if accion == 'iniciar':
            hora_actual_bolivia = localtime(timezone.now()).time()
            motivo = request.POST.get('motivo')
            responsable = request.POST.get('responsable')
            km_salida = vehiculo.kilometraje_actual
            terreno = request.POST.get('terreno', 'plano')
            carga = request.POST.get('carga', 'ligera')
                
            Bitacora.objects.create(
                vehiculo=vehiculo,
                chofer=request.user,
                estado_viaje='en_curso',
                hora_salida=hora_actual_bolivia,
                km_inicial=km_salida,
                km_final=km_salida,
                saldo_anterior=saldo_para_nuevo_viaje,     
                destino=request.POST.get('destino'),
                origen=request.POST.get('origen', 'Potosí'),
                objetivo_comision=motivo,
                responsable_viaje=responsable,
                terreno=terreno,
                carga=carga,
            )
            messages.success(request, "Viaje iniciado. ¡Conduce con precaución!")
            return redirect('dashboard')
    
        elif accion == 'finalizar' and viaje_activo:
            km_llegada_raw = request.POST.get('km_llegada')
            
            if not km_llegada_raw:
                messages.error(request, "Error: Debe ingresar el kilometraje de llegada.")
                return redirect('dashboard')

            km_llegada = int(km_llegada_raw) 
            
            if km_llegada <= viaje_activo.km_inicial:
                messages.error(request, f"El KM de llegada ({km_llegada}) debe ser mayor al de salida ({viaje_activo.km_inicial}).")
            else:
                viaje_activo.km_final = km_llegada
                viaje_activo.hora_llegada = localtime(timezone.now()).time()
                viaje_activo.estado_viaje = 'finalizado'
                
                if request.POST.get('toggle_combustible') == 'on':
                    viaje_activo.nro_vale_combustible = request.POST.get('nro_vale')
                    cantidad_raw = request.POST.get('cantidad')
                    viaje_activo.cantidad_litros = Decimal(cantidad_raw) if cantidad_raw else Decimal('0')
                    viaje_activo.costo_total = viaje_activo.cantidad_litros * Decimal('3.74')
                    viaje_activo.estacion_servicio = request.POST.get('estacion')
                
                viaje_activo.save()
                messages.success(request, "Viaje finalizado. El kilometraje del vehículo ha sido actualizado.")
                return redirect('dashboard')
    context = {
        'asignacion': asignacion,
        'vehiculo': vehiculo,
        'viaje_activo': viaje_activo,
        'historial': historial,
        'km_actual': vehiculo.kilometraje_actual if vehiculo else 0,
        'datos_grafico': datos_grafico,
        'ultima_eficiencia': ultima_eficiencia,
        'saldo_tanque': saldo_para_nuevo_viaje,
        'km_actual': km_actual,
    }
    return render(request, 'dashboard_chofer.html', context)

@login_required
def historial_viajes_chofer(request):
    if request.user.rol != 'chofer':
        return redirect('dashboard')
        
    mes_filtro = request.GET.get('mes')
    anio_filtro = request.GET.get('anio', datetime.now().year)
    viajes_queryset = Bitacora.objects.filter(chofer=request.user, estado_viaje='finalizado').order_by('-fecha')

    if mes_filtro and mes_filtro != "0":
        viajes_queryset = viajes_queryset.filter(fecha__month=mes_filtro, fecha__year=anio_filtro)
    
    total_km = viajes_queryset.aggregate(
        total=Sum(F('km_final') - F('km_inicial'))
    )['total'] or 0

    paginator = Paginator(viajes_queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    meses = [
        (1, 'Enero'), (2, 'Febrero'), (3, 'Marzo'), (4, 'Abril'), (5, 'Mayo'), (6, 'Junio'),
        (7, 'Julio'), (8, 'Agosto'), (9, 'Septiembre'), (10, 'Octubre'), (11, 'Noviembre'), (12, 'Diciembre')
    ]
    return render(request, 'chofer/historial.html', {
        'page_obj': page_obj,
        'total_km_historico': total_km,
        'meses': meses,
        'mes_actual': int(mes_filtro) if mes_filtro else 0
    })

@login_required
def detalle_vehiculo_chofer(request):
    asignacion = Asignacion.objects.filter(chofer=request.user, esta_activo=True).first()
    
    historial = []
    if asignacion:
        historial = Bitacora.objects.filter(
            vehiculo=asignacion.vehiculo,
            estado_viaje='finalizado'
        ).order_by('-fecha', '-id')[:5]

    return render(request, 'chofer/vehiculo.html', {
        'asignacion': asignacion,
        'historial_reciente': historial
    })

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
def vales_peajes_chofer(request):
    if request.user.rol != 'chofer':
        return redirect('dashboard')
    
    vales_list = Bitacora.objects.filter(chofer=request.user).exclude(
        Q(nro_vale_combustible='') | Q(nro_vale_combustible='S/N') | Q(nro_vale_combustible__isnull=True)
    ).order_by('-fecha')
    
    peajes_list = Peaje.objects.filter(chofer=request.user).order_by('-fecha')

    paginator_vales = Paginator(vales_list, 5)
    page_vales = request.GET.get('page_vales')
    vales_obj = paginator_vales.get_page(page_vales)

    paginator_peajes = Paginator(peajes_list, 5)
    page_peajes = request.GET.get('page_peajes')
    peajes_obj = paginator_peajes.get_page(page_peajes)

    total_peajes = peajes_list.aggregate(Sum('monto'))['monto__sum'] or 0

    return render(request, 'chofer/vales_peajes_lista.html', {
        'vales': vales_obj,    
        'peajes': peajes_obj,   
        'total_peajes': total_peajes
    })

@login_required
def vales_peajes_admin(request):
    if request.user.rol not in ['admin', 'superadmin', 'bienes']:
        return redirect('dashboard')
    
    vales = Bitacora.objects.exclude(nro_vale_combustible='').order_by('-fecha')
    peajes = Peaje.objects.all().order_by('-fecha')
    
    return render(request, 'admin_potosi/vales_peajes.html', {
        'bitacoras': vales, 
        'peajes': peajes
    })

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
        return redirect('lista_vales_peajes') 
        
    return render(request, 'chofer/registro_gasto.html')

@login_required
def dashboard_activos(request):
    if request.user.rol not in ['activos', 'superadmin']:
        return redirect('dashboard')

    total_vehiculos = Vehiculo.objects.count()
    vehiculos_ok = Vehiculo.objects.filter(estado='operacional').count()
    porcentaje = int((vehiculos_ok / total_vehiculos) * 100) if total_vehiculos > 0 else 0
    
    if total_vehiculos > 0:
        porcentaje = int((vehiculos_ok / total_vehiculos) * 100)

    pendientes_validacion = Bitacora.objects.filter(estado_validacion='pendiente').count()
    pendientes_acta = Asignacion.objects.filter(esta_activo=True, documento_acta='').count()
    total_pendientes = pendientes_validacion + pendientes_acta
    asignaciones = Asignacion.objects.filter(esta_activo=True).select_related('vehiculo', 'chofer').order_by('-fecha_asignacion')[:5]
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
    if request.user.rol not in ['admin', 'superadmin']:
        return redirect('dashboard')

    hoy = timezone.now().date()
    inicio_mes = hoy.replace(day=1)

    # 1. Consumo Promedio Mensual
    bitacoras_mes = Bitacora.objects.filter(fecha__date__gte=inicio_mes)
    stats = bitacoras_mes.aggregate(
        total_km=Sum(F('km_final') - F('km_inicial')),
        total_lts=Sum('cantidad_litros')
    )
    km = float(stats['total_km'] or 0)
    lts = float(stats['total_lts'] or 1)
    consumo_avg = round(km / lts, 1)

    # 2. Resumen de Inventario
    inv_diesel = InventarioCombustible.objects.filter(tipo='Diesel').first()
    inv_gasolina = InventarioCombustible.objects.filter(tipo='Gasolina').first()
    
    alertas_count = Bitacora.objects.filter(estado_validacion='anomalia').count()

    choferes = Usuario.objects.filter(rol='chofer').order_by('-id')[:4]
    for c in choferes:
        viaje = Bitacora.objects.filter(chofer=c, estado_viaje='en_curso').first()
        c.estado_operativo = "EN RUTA" if viaje else "EN BASE"

    
    registros_list = Bitacora.objects.all().select_related('vehiculo', 'chofer').order_by('-fecha')
    paginator = Paginator(registros_list, 10)
    page_number = request.GET.get('page')
    registros_obj = paginator.get_page(page_number)

    context = {
        'consumo_avg': consumo_avg,
        'diesel_stock': inv_diesel.cantidad_total if inv_diesel else 0,
        'gasolina_stock': inv_gasolina.cantidad_total if inv_gasolina else 0,
        'alertas_count': alertas_count,
        'choferes': choferes,
        'registros': registros_obj,
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
    if request.user.rol not in ['superadmin', 'admin', 'activos']:
        return HttpResponse("No autorizado", status=403)
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Personal de Conducción"

    columns = ['Username', 'Nombre', 'Apellido', 'Rol', 'CI', 'Licencia', 'Vencimiento', 'Email']
    ws.append(columns)

    if request.user.rol == 'activos':
        usuarios = Usuario.objects.filter(rol='chofer')
    else:
        usuarios = Usuario.objects.all()

    for u in usuarios:
        ws.append([
            u.username, 
            u.first_name, 
            u.last_name, 
            u.get_rol_display(), 
            u.ci, 
            u.licencia_conducir, 
            str(u.vencimiento_licencia), 
            u.email
        ])

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="Reporte_Personal_GADP.xlsx"'
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


def gestion_catalogos(request):
    hoy = timezone.now()
    areas = Area.objects.annotate(
        num_vehiculos=Count('vehiculo', distinct=True),
        num_choferes=Count('usuario', distinct=True)
    ).order_by('nombre')
    combustibles_data = TipoCombustible.objects.all().order_by('nombre')
    for c in combustibles_data:
        c.num_vehiculos = Vehiculo.objects.filter(tipo_combustible=c).count()
        stats = Bitacora.objects.filter(
            vehiculo__tipo_combustible=c,
            fecha__month=hoy.month,
            fecha__year=hoy.year
        ).aggregate(
            total_lts=Sum('cantidad_litros'),
            total_bs=Sum('costo_total')
        )
        
        c.consumo_mes = stats['total_lts'] or 0
        c.gasto_mes = stats['total_bs'] or 0
        if c.consumo_mes > 0:
            c.porcentaje_uso = min(int((float(c.consumo_mes) / 5000) * 100), 100)
        else:
            c.porcentaje_uso = 0
        
    context = {
        'vehiculos': Vehiculo.objects.all().order_by('placa'),
        'areas': areas,
        'combustibles': combustibles_data,
    }
    return render(request, 'usuarios/catalogos.html', context)

@login_required
def crear_combustible(request):
    if request.user.rol not in ['activos', 'superadmin']:
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = TipoCombustibleForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Nuevo tipo de combustible registrado exitosamente.")
            return redirect('gestion_catalogos')
    else:
        form = TipoCombustibleForm()
        
    return render(request, 'catalogos/combustible_form.html', {'form': form})

@login_required
def detalle_vales_combustible(request, pk):
    # 1. Obtenemos el objeto completo del combustible (esto tiene el ID y el Nombre)
    tipo_insumo = get_object_or_404(TipoCombustible, pk=pk)
    
    # 2. Filtramos las bitácoras
    # IMPORTANTE: Pasamos 'tipo_insumo' (el objeto), NO 'tipo_insumo.nombre'
    vales = Bitacora.objects.filter(
        vehiculo__tipo_combustible=tipo_insumo
    ).exclude(
        Q(nro_vale_combustible='') | 
        Q(nro_vale_combustible='S/N') | 
        Q(nro_vale_combustible__isnull=True)
    ).order_by('-fecha')
    
    return render(request, 'catalogos/detalle_vales_combustible.html', {
        'tipo': tipo_insumo,
        'vales': vales
    })

@login_required
def vista_roles(request):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
    
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
        form = VehiculoForm(request.POST, request.FILES)
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
        form = VehiculoForm(request.POST, request.FILES, instance=vehiculo)
        if form.is_valid():
            form.save()
            messages.success(request, f"Vehículo {vehiculo.placa} actualizado.")
            return redirect('gestion_catalogos')
        else:
            messages.error(request, "Error al actualizar. Revise los datos ingresados.")
    else:
        form = VehiculoForm(instance=vehiculo)
    return render(request, 'catalogos/vehiculo_form.html', {
        'form': form, 
        'editando': True, 
        'vehiculo': vehiculo
    })

@login_required
def eliminar_vehiculo(request, pk):
    vehiculo = get_object_or_404(Vehiculo, pk=pk)
    vehiculo.delete()
    messages.success(request, "Vehículo eliminado del catálogo.")
    return redirect('gestion_catalogos')

@login_required
def reporte_oficial_chofer(request, chofer_id):
    chofer = get_object_or_404(Usuario, id=chofer_id)
    ahora = timezone.localtime(timezone.now())

    anio = int(request.GET.get('anio', ahora.year))
    mes_raw = request.GET.get('mes', ahora.month)
    periodo = request.GET.get('periodo', 'mensual')

    mes_map = {'ENERO':1,'FEBRERO':2,'MARZO':3,'ABRIL':4,'MAYO':5,'JUNIO':6,'JULIO':7,'AGOSTO':8,'SEPTIEMBRE':9,'OCTUBRE':10,'NOVIEMBRE':11,'DICIEMBRE':12}
    mes = int(mes_raw) if str(mes_raw).isdigit() else mes_map.get(str(mes_raw).upper(), ahora.month)
    
    def obtener_nro_folio(c, a, m, p):
        existente = ReporteFolio.objects.filter(chofer=c, anio=a, mes=m, periodo=p).first()
        if existente:
            return existente.numero


        if p.startswith('quincena'):
            ultimo = ReporteFolio.objects.filter(periodo__startswith='quincena').order_by('numero').last()
        else:
            ultimo = ReporteFolio.objects.filter(periodo='mensual').order_by('numero').last()

        nuevo_nro = (ultimo.numero + 1) if ultimo else 1
        
        ReporteFolio.objects.create(numero=nuevo_nro, chofer=c, anio=a, mes=m, periodo=p)
        return nuevo_nro

    nro_raw = obtener_nro_folio(chofer, anio, mes, periodo)
    nro_reporte = f"{int(nro_raw):03d}"
    
    try:
        nro_reporte = int(nro_raw)
    except (ValueError, TypeError):
        nro_reporte = 1

    asignacion_actual = Asignacion.objects.filter(chofer=chofer, esta_activo=True).first()
    if not asignacion_actual:
        messages.error(request, "El chofer no tiene un vehículo asignado actualmente.")
        return redirect('dashboard')    

    vehiculo = Asignacion.objects.filter(chofer=chofer, esta_activo=True).first().vehiculo
    rendimiento = vehiculo.rendimiento_km_litro

    ultimo_dia_mes = calendar.monthrange(anio, mes)[1]
    if periodo == 'quincena1':
        dia_inicio, dia_fin = 1, 15
    elif periodo == 'quincena2':
        dia_inicio, dia_fin = 16, ultimo_dia_mes
    else:
        dia_inicio, dia_fin = 1, ultimo_dia_mes

    fecha_inicio_reporte = date(anio, mes, dia_inicio)
    bitacora_previa = Bitacora.objects.filter(
        vehiculo=vehiculo, 
        fecha__date__lt=fecha_inicio_reporte,
        estado_viaje='finalizado'
    ).order_by('-fecha', '-id').first()

    saldo_acumulado = bitacora_previa.saldo_actual if bitacora_previa else vehiculo.combustible_inicial
    km_acumulado = bitacora_previa.km_final if bitacora_previa else vehiculo.kilometraje_actual
    
    saldo_anterior_fijo = saldo_acumulado
    km_inicial_fijo = km_acumulado

    registros_finales = []
    total_cargado_periodo = 0
    total_utilizado_periodo = 0

    for d in range(dia_inicio, dia_fin + 1):
        fecha_dia = date(anio, mes, d)
        viajes_dia = Bitacora.objects.filter(
            chofer=chofer, 
            fecha__date=fecha_dia, 
            estado_viaje='finalizado'
        ).order_by('id')

        if viajes_dia.exists():
            ingreso_dia = viajes_dia.aggregate(Sum('cantidad_litros'))['cantidad_litros__sum'] or 0
            vale_dia = viajes_dia.exclude(nro_vale_combustible='S/N').first()
            nro_vale = vale_dia.nro_vale_combustible if vale_dia else "--"
            
            km_salida_dia = viajes_dia.first().km_inicial
            km_llegada_dia = viajes_dia.last().km_final
            recorrido_dia = km_llegada_dia - km_salida_dia
            objeto_viaje = viajes_dia.first().get_motivo_oficial()
            responsable_dia = viajes_dia.first().responsable_viaje if viajes_dia.first().responsable_viaje else "--"
        else:
            ingreso_dia = 0
            nro_vale = "--"
            km_salida_dia = km_acumulado
            km_llegada_dia = km_acumulado
            recorrido_dia = 0
            objeto_viaje = "APOYO LOCAL"
            responsable_dia = "--"

        utilizado_dia = Decimal(recorrido_dia) / rendimiento if rendimiento > 0 else 0
        saldo_acumulado = saldo_acumulado + Decimal(ingreso_dia) - utilizado_dia
        
        km_acumulado = km_llegada_dia

        registros_finales.append({
            'fecha': fecha_dia,
            'vale': nro_vale,
            'ingreso': ingreso_dia,
            'utilizado': round(utilizado_dia, 2),
            'saldo': round(saldo_acumulado, 2),
            'salida': km_salida_dia,
            'llegada': km_llegada_dia,
            'recorrido': recorrido_dia,
            'objeto': objeto_viaje,
            'responsable': responsable_dia
        })
        
        total_cargado_periodo += ingreso_dia
        total_utilizado_periodo += utilizado_dia

    nombres_meses = {1:'ENERO', 2:'FEBRERO', 3:'MARZO', 4:'ABRIL', 5:'MAYO', 6:'JUNIO', 7:'JULIO', 8:'AGOSTO', 9:'SEPTIEMBRE', 10:'OCTUBRE', 11:'NOVIEMBRE', 12:'DICIEMBRE'}

    context = {
        'chofer': chofer,
        'vehiculo': vehiculo,
        'registros': registros_finales,
        'mes_nombre': nombres_meses.get(mes),
        'anio': anio,
        'saldo_anterior': saldo_anterior_fijo,
        'km_inicial_mes': km_inicial_fijo,
        'total_recorrido': sum(r['recorrido'] for r in registros_finales),
        'total_cargado': total_cargado_periodo,
        'total_utilizado': round(total_utilizado_periodo, 2),
        'saldo_final': round(saldo_acumulado, 2),
        'nro_reporte': f"{int(nro_reporte):03d}",
        'periodo': periodo
    }
    return render(request, 'reportes/planilla_oficial.html', context)

@login_required
def reporte_diario_detalle(request, bitacora_id):
    if request.user.rol not in ['admin', 'superadmin']:
        return redirect('dashboard')
        
    bitacora_ref = get_object_or_404(Bitacora, id=bitacora_id)
    
    fecha_local_completa = timezone.localtime(bitacora_ref.fecha)
    fecha_sel = fecha_local_completa.date()
    
    nro_correlativo = Bitacora.objects.filter(
        chofer=bitacora_ref.chofer,
        estado_viaje='finalizado',
        fecha__date__lte=fecha_sel
    ).annotate(
        dia_bolivia=TruncDate('fecha', tzinfo=timezone.get_current_timezone())
    ).values('dia_bolivia').distinct().count()

    viajes = Bitacora.objects.annotate(
        fecha_bolivia=TruncDate('fecha', tzinfo=timezone.get_current_timezone())
    ).filter(
        chofer=bitacora_ref.chofer,
        fecha_bolivia=fecha_sel,
        estado_viaje='finalizado'
    ).order_by('hora_salida')
    
    total_km = sum((v.km_final - v.km_inicial) for v in viajes)

    return render(request, 'reportes/reporte_diario.html', {
        'bitacora': bitacora_ref,
        'viajes': viajes,
        'fecha_sel': fecha_sel,
        'total_km': total_km,
        'nro_correlativo': nro_correlativo 
    })

@login_required
def cerrar_periodo_chofer(request, chofer_id, anio, mes):
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
    if request.user.rol not in ['admin', 'superadmin']:
        return redirect('dashboard')

    choferes = Usuario.objects.filter(rol='chofer').prefetch_related(
        models.Prefetch('asignacion_set', queryset=Asignacion.objects.filter(esta_activo=True), to_attr='asignacion_activa')
    ).order_by('last_name')

    anios = range(2024, datetime.now().year + 1)
    meses = [
        (1, 'Enero'), (2, 'Febrero'), (3, 'Marzo'), (4, 'Abril'),
        (5, 'Mayo'), (6, 'Junio'), (7, 'Julio'), (8, 'Agosto'),
        (9, 'Septiembre'), (10, 'Octubre'), (11, 'Noviembre'), (12, 'Diciembre')
    ]

    context = {
        'choferes': choferes,
        'anios': anios,
        'meses': meses,
        'anio_actual': datetime.now().year,
        'mes_actual': datetime.now().month,
    }
    return render(request, 'admin_potosi/seleccionar_chofer.html', context)

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
    if request.user.rol not in ['activos', 'superadmin']:
        return redirect('dashboard')
    
    query = request.GET.get('q', '')
    estado = request.GET.get('estado', 'todos')
    
    qs = Asignacion.objects.all().select_related('vehiculo', 'chofer').order_by('-fecha_asignacion')
    
    if query:
        qs = qs.filter(
            Q(vehiculo__placa__icontains=query) | 
            Q(chofer__username__icontains=query) | 
            Q(chofer__first_name__icontains=query) | 
            Q(chofer__last_name__icontains=query) |
            Q(nro_memorandum__icontains=query)
        )

    if estado == 'vigentes':
        qs = qs.filter(esta_activo=True)
    elif estado == 'finalizados':
        qs = qs.filter(esta_activo=False)

    paginator = Paginator(qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'admin_potosi/historial_asignaciones.html', {
        'asignaciones': page_obj, 
        'query': query,
        'estado_actual': estado
    })

@login_required
def lista_memorandums(request):
    asignaciones = Asignacion.objects.exclude(nro_memorandum='').order_by('-fecha_asignacion')
    return render(request, 'admin_potosi/memorandums.html', {'asignaciones': asignaciones})

@login_required
def imprimir_memorandum(request, pk):
    asignacion = get_object_or_404(Asignacion, pk=pk)
    template_path = 'reportes/pdf_memorandum.html'
    context = {'asig': asignacion, 'fecha': timezone.now()}
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Memorandum_{asignacion.nro_memorandum}.pdf"'
    
    template = get_template(template_path)
    html = template.render(context)

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('Error al generar el reporte', status=500)
    return response

@login_required
def ver_memorandum(request, pk):
    asignacion = get_object_or_404(Asignacion, pk=pk)
    if asignacion.documento_acta:
        return redirect(asignacion.documento_acta.url)
    else:
        messages.warning(request, "No se ha cargado un documento digitalizado para este memorándum.")
        return redirect('lista_memorandums')

@login_required
def lista_actas(request):
    asignaciones = Asignacion.objects.all().order_by('-fecha_asignacion')
    return render(request, 'admin_potosi/actas.html', {'asignaciones': asignaciones})

@login_required
def subir_acta(request, pk):
    if request.user.rol not in ['activos', 'admin', 'superadmin']:
        return redirect('dashboard')
        
    asignacion = get_object_or_404(Asignacion, pk=pk)
    
    if request.method == 'POST':
        archivo = request.FILES.get('documento_acta')
        if archivo:
            asignacion.documento_acta = archivo
            asignacion.save()
            
            # Registrar en Auditoría
            LogAuditoria.objects.create(
                usuario=request.user,
                usuario_nombre=request.user.username,
                accion='modificacion',
                tabla='Asignacion',
                descripcion=f"Se cargó acta digital para el vehículo {asignacion.vehiculo.placa}",
                ip=obtener_ip(request)
            )
            messages.success(request, "Acta digitalizada cargada correctamente.")
        else:
            messages.error(request, "No se seleccionó ningún archivo.")
            
        return redirect('lista_actas')
    
    return render(request, 'activos/subir_acta_form.html', {'asignacion': asignacion})

@login_required
def vista_vales_peajes(request):
    if request.user.rol in ['superadmin', 'admin', 'bienes', 'chofer']:
        vales = Bitacora.objects.exclude(nro_vale_combustible__in=['', None]).order_by('-fecha')
        peajes = Peaje.objects.all().order_by('-fecha')
        es_admin = True
    else:
        vales = Bitacora.objects.filter(chofer=request.user).exclude(nro_vale_combustible__in=['', None]).order_by('-fecha')
        peajes = Peaje.objects.filter(chofer=request.user).order_by('-fecha')
        es_admin = False

    return render(request, 'chofer/vales_peajes_lista.html', {
        'vales': vales,
        'peajes': peajes,
        'es_admin': es_admin
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
def registrar_peaje(request):
    if request.user.rol != 'chofer':
        return redirect('dashboard')
        
    if request.method == 'POST':
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
    logs_enmienda = LogAuditoria.objects.filter(
        tabla='Bitacora', 
        descripcion__icontains=f"Registro {bitacora.id}"
    ).order_by('-fecha_hora')
    identificador = bitacora.nro_vale_combustible if bitacora.nro_vale_combustible else f"PLACA {bitacora.vehiculo.placa}"
    
    context = {
        'bitacora': bitacora,
        'identificador': identificador,
        'viajes': viajes,
        'peajes': peajes,
        'logs_enmienda': logs_enmienda
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
def historial_diario_chofer(request, chofer_id):
    if request.user.rol not in ['admin', 'superadmin', 'activos']:
        return redirect('dashboard')

    chofer = get_object_or_404(Usuario, id=chofer_id)
    hoy = timezone.now().date()
    bitacoras = Bitacora.objects.filter(chofer=chofer, fecha__date=hoy).order_by('hora_salida')
    
    return render(request, 'admin_potosi/historial_diario.html', {
        'chofer': chofer,
        'bitacoras': bitacoras,
        'fecha': hoy
    })

@login_required
@transaction.atomic 
def registrar_chofer_con_asignacion(request):
    if request.method == 'POST':
        form = RegistroChoferCompletoForm(request.POST, request.FILES)
        if form.is_valid():
            chofer = form.save(commit=False)
            chofer.rol = 'chofer'
            chofer.area = form.cleaned_data['area']
            chofer.set_password('Potosi123')
            chofer.save()
            
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
        RegistroMantenimiento.objects.create(
            vehiculo=vehiculo,
            fecha_ingreso=request.POST.get('fecha_ingreso'),
            motivo=request.POST.get('motivo'),
            observaciones=request.POST.get('observaciones'),
            encargado_taller=request.POST.get('taller')
        )
        
        vehiculo.estado = 'mantenimiento'
        vehiculo.save()
        
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
    
    registro = RegistroMantenimiento.objects.filter(vehiculo=vehiculo, finalizado=False).last()
    
    if registro:
        registro.fecha_salida = timezone.now().date()
        registro.finalizado = True
        registro.save()
    
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
        asignacion.esta_activo = False
        asignacion.fecha_fin = timezone.now().date()
        asignacion.motivo_baja = request.POST.get('motivo')
        asignacion.documento_baja = request.FILES.get('documento_baja')
        asignacion.save()        
        chofer = asignacion.chofer
        chofer.area = None
        chofer.save()

        messages.success(request, f"Baja procesada. El chofer {chofer.get_full_name()} ha sido liberado para nueva asignación.")
        return redirect('dashboard')
        
    return render(request, 'activos/baja_form.html', {'asignacion': asignacion})

@login_required
def habilitar_vehiculo(request, vehiculo_id):
    vehiculo = get_object_or_404(Vehiculo, id=vehiculo_id)
    RegistroMantenimiento.objects.filter(vehiculo=vehiculo, finalizado=False).update(
        finalizado=True, 
        fecha_salida=timezone.now().date()
    )
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
            if asignacion_actual:
                asignacion_actual.esta_activo = False
                asignacion_actual.fecha_fin = timezone.now().date()
                asignacion_actual.motivo_baja = "Traspaso de Secretaría"
                asignacion_actual.save()
                vehiculo = asignacion_actual.vehiculo
            else:
                vehiculo = None

            nueva_area = form.cleaned_data['nueva_area']
            chofer.area = nueva_area
            chofer.save()

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
        usuario_objetivo.is_active = False 
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
    if request.user.rol != 'superadmin':
        return HttpResponseForbidden("Acceso denegado. Solo la alta dirección puede enmendar registros.")

    bitacora = get_object_or_404(Bitacora, pk=pk)
    valor_anterior = model_to_dict(bitacora)

    if request.method == 'POST':
        form = EnmiendaBitacoraForm(request.POST, request.FILES, instance=bitacora)
        if form.is_valid():
            JustificacionEdicion.objects.create(
                superusuario=request.user,
                tabla_afectada='Bitacora',
                registro_id=bitacora.id,
                motivo=form.cleaned_data['motivo_enmienda'],
                documento_respaldo=request.FILES['documento_respaldo'],
                observacion_adicional=f"Se corrigió de {valor_anterior['km_final']} a {bitacora.km_final} KM."
            )

            form.save()

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

    app_config = apps.get_app_config('gestion')
    modelos = [model.__name__ for model in app_config.get_models()]

    sql_final = "-- ==================================================\n"
    sql_final += "-- BACKUP TOTAL GADP POTOSÍ (ESTRUCTURA Y DATOS)\n"
    sql_final += f"-- Generado: {timezone.now()}\n"
    sql_final += f"-- Superusuario: {request.user.username}\n"
    sql_final += "-- ==================================================\n\n"
    
    sql_final += "SET CONSTRAINTS ALL DEFERRED;\n\n"

    for m in modelos:
        sql_final += f"-- TABLA: {m}\n"
        sql_final += generar_sql_insert(m) + "\n\n"

    LogAuditoria.objects.create(
        usuario=request.user,
        usuario_nombre=request.user.username,
        accion='backup',
        tabla='BASE DE DATOS COMPLETA',
        descripcion="EJECUCIÓN DE BACKUP TOTAL DEL SISTEMA (.SQL)",
        ip=obtener_ip(request)
    )

    response = HttpResponse(sql_final, content_type='application/sql')
    response['Content-Disposition'] = 'attachment; filename="backup_completo_potosi.sql"'
    return response

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
    valor_anterior = {'estado': vehiculo.estado}
    vehiculo.estado = 'operacional'
    vehiculo.save()
    
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
    if request.user.rol not in ['activos', 'admin', 'superadmin']:
        return redirect('dashboard')
        
    secretaria = get_object_or_404(Area, pk=pk)
    
    num_vehiculos = Vehiculo.objects.filter(area=secretaria).count()
    num_choferes = Usuario.objects.filter(area=secretaria).count()
    
    if request.method == 'POST':
        form = AreaForm(request.POST, instance=secretaria)
        if form.is_valid():
            form.save()
            messages.success(request, f"Cambios guardados en {secretaria.nombre}")
            return redirect('gestion_catalogos')
    else:
        form = AreaForm(instance=secretaria)
        
    return render(request, 'activos/secretaria_form.html', {
        'form': form, 
        'editando': True, 
        'secretaria': secretaria,
        'num_v': num_vehiculos,
        'num_c': num_choferes
    })

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
        
    return render(request, 'catalogos/combustible_form.html', {'form': form, 'editando':True, 'combustible': combustible})

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
        usuario.estado = 'inactivo' 
        usuario.is_active = False  
        usuario.fecha_baja = timezone.now()
        usuario.motivo_baja = request.POST.get('motivo')
        usuario.save()
    
        registrar_auditoria(request, 'baja', 'Usuario', f"Baja de usuario {usuario.username}")
        
        messages.warning(request, f"El usuario {usuario.username} ha sido pasado al archivo de bajas.")
        return redirect('lista_usuarios')
    
@login_required
def solicitar_correccion(request, bitacora_id):
    if request.method == 'POST':
        bitacora = get_object_or_404(Bitacora, id=bitacora_id, chofer=request.user)
        
        if bitacora.solicitud_correccion:
            messages.info(request, "Ya existe una solicitud pendiente para este registro.")
            return redirect('dashboard')

        motivo = request.POST.get('motivo_ajuste', 'No especificado')
        
        bitacora.solicitud_correccion = True
        bitacora.motivo_correccion = motivo
        bitacora.save()
        
        LogAuditoria.objects.create(
            usuario=request.user,
            usuario_nombre=request.user.username,
            rol=request.user.get_rol_display(),
            accion='modificacion',
            tabla='Bitacora',
            descripcion=f"SOLICITUD DE EDICIÓN: El chofer reporta error en Vale {bitacora.nro_vale_combustible}. Motivo: {motivo}",
            ip=obtener_ip(request)
        )
        
        messages.success(request, "Solicitud de rectificación enviada al Superadministrador.")
        return redirect('dashboard')
    
    return redirect('dashboard')

@login_required
def kardex_vehiculo(request, pk):
    vehiculo = get_object_or_404(Vehiculo, pk=pk)
    
    historial_asignaciones = Asignacion.objects.filter(vehiculo=vehiculo).order_by('-fecha_asignacion')
    
    historial_mantenimiento = RegistroMantenimiento.objects.filter(vehiculo=vehiculo).order_by('-fecha_ingreso')

    return render(request, 'catalogos/kardex_vehiculo.html', {
        'vehiculo': vehiculo,
        'asignaciones': historial_asignaciones,
        'mantenimientos': historial_mantenimiento
    })

@login_required
def editar_bitacora_admin(request, pk):
    if request.user.rol != 'superadmin':
        messages.error(request, "Acceso denegado. Solo el Superadmin puede realizar enmiendas.")
        return redirect('dashboard')
        
    bitacora = get_object_or_404(Bitacora, pk=pk)
    
    if request.method == 'POST':
        form = EnmiendaBitacoraForm(request.POST, request.FILES, instance=bitacora)
        if form.is_valid():
            enmienda = form.save(commit=False)
            nuevo_destino = request.POST.get('destino')
            nuevo_km = request.POST.get('km_final')
            nuevos_litros = request.POST.get('cantidad_litros')
            archivo = request.FILES.get('documento_respaldo')
            if not archivo:
                messages.error(request, "ERROR: Es obligatorio subir la Nota de Respaldo (Foto o PDF) para aplicar la enmienda.")
                return render(request, 'superadmin/enmienda_form.html', {
                    'form': form, 
                    'bitacora': bitacora
                })
            enmienda.documento_enmienda = archivo
            enmienda.solicitud_correccion = False
            enmienda.estado_validacion = 'validado'
            enmienda.save()
            bitacora.destino = nuevo_destino
            bitacora.km_final = int(nuevo_km)
            bitacora.cantidad_litros = Decimal(nuevos_litros)
            bitacora.documento_enmienda = archivo
            
            bitacora.solicitud_correccion = False 
            bitacora.estado_validacion = 'validado'
            bitacora.save()
            justificacion = request.POST.get('justificacion')
            LogAuditoria.objects.create(
                usuario=request.user,
                usuario_nombre=request.user.username,
                rol="SUPERADMIN",
                accion='modificacion',
                tabla='Bitacora',
                descripcion=f"ENMIENDA CRÍTICA: Registro {bitacora.id}. Motivo: {form.cleaned_data['motivo_enmienda']}",
                ip=obtener_ip(request)
            )
        
            messages.success(request, "Enmienda legal aplicada y registrada en el historial forense.")
            return redirect('registros_visualizacion')  
        else:
            messages.error(request, "Error en el formulario. Asegúrese de subir el documento de respaldo.")
    else:
        form = EnmiendaBitacoraForm(instance=bitacora)

    return render(request, 'superadmin/enmienda_form.html', {
        'form': form, 
        'bitacora': bitacora
    })  

@login_required
def lista_solicitudes_enmienda(request):
    if request.user.rol != 'superadmin':
        return redirect('dashboard')
        
    solicitudes = Bitacora.objects.filter(solicitud_correccion=True).order_by('-fecha')
    
    return render(request, 'superadmin/solicitudes_list.html', {
        'solicitudes': solicitudes
    })

@login_required
def api_choferes_por_area(request, area_id):
    area = get_object_or_404(Area, id=area_id)
    choferes = Usuario.objects.filter(area=area, rol='chofer').values('first_name', 'last_name', 'ci', 'foto')
    
    lista_choferes = []
    for c in choferes:
        lista_choferes.append({
            'nombre': f"{c['first_name']} {c['last_name']}",
            'ci': c['ci'] if c['ci'] else "S/N",
            'foto': f"{settings.MEDIA_URL}{c['foto']}" if c['foto'] else None
        })
        
    return JsonResponse({
        'area': area.nombre,
        'total': len(lista_choferes),
        'choferes': lista_choferes
    })

@login_required
def imprimir_acta_entrega(request, pk):
    asignacion = get_object_or_404(Asignacion, pk=pk)
    template_path = 'reportes/pdf_acta_entrega.html'
    context = {'asig': asignacion, 'fecha': timezone.now()}
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Acta_Entrega_{asignacion.vehiculo.placa}.pdf"'
    template = get_template(template_path)
    html = template.render(context)
    pisa_status = pisa.CreatePDF(html, dest=response)
    return response

@login_required
def lista_reportes_chofer(request, chofer_id):
    if request.user.rol not in ['admin', 'superadmin']:
        return redirect('dashboard')

    chofer = get_object_or_404(Usuario, id=chofer_id)
    
    meses_raw = Bitacora.objects.filter(chofer=chofer).dates('fecha', 'month', order='DESC')
    
    reportes_por_mes = []
    nombres_meses = {
        1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
        7: 'Julio', 8: 'Agosto', 9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
    }
    meses_disponibles = Bitacora.objects.filter(chofer=chofer).dates('fecha', 'month', order='DESC')
    dias_disponibles = Bitacora.objects.filter(chofer=chofer).order_by('-fecha')[:15] 
    for fecha in meses_raw:
        bitacoras_del_mes = Bitacora.objects.filter(
            chofer=chofer, 
            fecha__year=fecha.year, 
            fecha__month=fecha.month
        ).order_by('-fecha')

        reportes_por_mes.append({
            'anio': fecha.year,
            'mes_num': fecha.month,
            'mes_nombre': nombres_meses[fecha.month],
            'bitacoras_diarias': bitacoras_del_mes 
        })
        
    context = {
        'chofer': chofer,
        'meses': meses_disponibles,
        'dias': dias_disponibles,
        'reportes': reportes_por_mes,
        'anio_actual': datetime.now().year,
    }

    return render(request, 'admin_potosi/lista_reportes.html', context)

@login_required
def buscar_diario_por_fecha(request, chofer_id):
    fecha_sel = request.GET.get('fecha')
    if not fecha_sel:
        messages.error(request, "Seleccione una fecha válida.")
        return redirect('lista_reportes_chofer', chofer_id=chofer_id)
    
    bitacora = Bitacora.objects.filter(chofer_id=chofer_id, fecha__date=fecha_sel).first()
    
    if bitacora:
        return redirect('reporte_diario_detalle', bitacora_id=bitacora.id)
    else:
        messages.error(request, f"No se encontró actividad registrada para el día {fecha_sel}")
        return redirect('lista_reportes_chofer', chofer_id=chofer_id)