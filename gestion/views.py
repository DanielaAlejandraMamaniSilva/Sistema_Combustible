from django.shortcuts import render, redirect, get_object_or_404
from .models import Usuario, Vehiculo, Asignacion, Bitacora, Area, TipoCombustible, AjusteSistema, InventarioCombustible, Peaje, Viaje, ValeCombustible
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import get_user_model
from django.db.models import Avg, Sum, F, ExpressionWrapper, DecimalField, Count
from .forms import UsuarioCreationForm, UserChangeForm, VehiculoForm, AsignacionForm, BitacoraForm, ViajeFormSet
from django.contrib import messages
from .utils import evaluar_tipo_bitacora
from .services import procesar_bitacoras_periodo
from .filters import BitacoraFilter
from django.core.management import call_command
from django.contrib.sessions.models import Session
from xhtml2pdf import pisa
import django_filters
from django.template.loader import get_template
from django.utils import timezone
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE, DELETION
from django.contrib.contenttypes.models import ContentType
import openpyxl
from .utils import obtener_ruta_dijkstra, calcular_distancia, buscar_coordenadas
from django.forms import modelformset_factory
from django.db import transaction
from django.http import JsonResponse
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

    total_usuarios = Usuario.objects.count()
    sesiones_activas = Session.objects.filter(expire_date__gte=timezone.now()).count()
    salud = "99.8%" 
    total_vehiculos = Vehiculo.objects.count()
    logs = LogEntry.objects.all().select_related('user', 'content_type')[:5]
    config, _ = AjusteSistema.objects.get_or_create(id=1)
    
    catalogos =[
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
    
    conteo_roles =[]
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
    bitacoras = Bitacora.objects.filter(chofer=chofer, fecha__year=anio, fecha__month=mes)
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
    f = BitacoraFilter(request.GET, queryset=Bitacora.objects.all().order_by('-fecha'))
    return render(request, 'admin_potosi/registros_list.html', {'filter': f})

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
def crear_asignacion(request):
    if request.user.rol not in['activos', 'admin', 'superadmin']:
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
    
    messages.success(request, f"Registro {bitacora.nro_vale_combustible} marcado como {nuevo_estado.upper()}")
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
    
    context = {
        'bitacora': bitacora,
        'viajes': viajes,
        'peajes': peajes,
    }
    return render(request, 'admin_potosi/detalle_validacion.html', context)

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
