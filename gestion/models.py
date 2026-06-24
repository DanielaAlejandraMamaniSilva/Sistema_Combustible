from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from math import radians, sin, cos, sqrt, atan2
from decimal import Decimal
from django.utils import timezone
from datetime import datetime, timedelta
from django.db.models import Sum, F
from django.forms.models import model_to_dict
from .utils import registrar_auditoria
from django.core.exceptions import ValidationError
import json

class Area(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    area_especifica = models.CharField(max_length=150, blank=True, null=True, verbose_name="Área Institucional")
    estado = models.BooleanField(default=True, verbose_name="Estado Activo")

    def __str__(self):
        if self.area_especifica:
            return f"{self.nombre} - {self.area_especifica}"
        return self.nombre

class TipoCombustible(models.Model):
    nombre = models.CharField(max_length=50, unique=True) 

    def __str__(self):
        return self.nombre

class Usuario(AbstractUser):
    ROLES = (
        ('superadmin', 'Super Administrador'),
        ('admin', 'Administrador'),
        ('activos', 'Encargado de Activos'),
        ('chofer', 'Chofer'),
    )
    ESTADOS = (
        ('activo', 'Activo'),
        ('inactivo', 'Inactivo'),
        ('suspendido', 'Suspendido'),
    )
    CATEGORIAS_LICENCIA = (
        ('B', 'Categoría B'),
        ('C', 'Categoría C'),
    )
    categoria_licencia = models.CharField(
        max_length=2, 
        choices=CATEGORIAS_LICENCIA, 
        null=True, 
        blank=True,
        
    )
    rol = models.CharField(max_length=20, choices=ROLES, default='chofer')
    ci = models.CharField(max_length=15, unique=True, null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='activo')
    foto = models.ImageField(upload_to='perfiles/', null=True, blank=True)
    licencia_conducir = models.CharField(max_length=20, null=True, blank=True)
    vencimiento_licencia = models.DateField(null=True, blank=True)
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_baja = models.DateTimeField(null=True, blank=True)
    motivo_baja = models.TextField(null=True, blank=True)
    baja_por = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='usuarios_desactivados')
    ultima_actividad = models.DateTimeField(null=True, blank=True)


    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.get_rol_display()})"
    
    def get_vehiculo_activo(self):
        asignacion = self.asignacion_set.filter(esta_activo=True).first()
        return asignacion.vehiculo.placa if asignacion else "Sin Asignar"

class Vehiculo(models.Model):
    ESTADOS = (
        ('operacional', 'ACTIVO'),
        ('mantenimiento', 'EN MANTENIMIENTO'),
        ('inactivo', 'INACTIVO'),
    )
    foto = models.ImageField(upload_to='vehiculos/', null=True, blank=True, verbose_name="Fotografía del Vehículo")
    vencimiento_soat = models.DateField(null=True, blank=True, verbose_name="Vencimiento SOAT")
    placa = models.CharField(max_length=15, unique=True)
    marca = models.CharField(max_length=50)
    modelo = models.CharField(max_length=50)
    anio = models.PositiveIntegerField(verbose_name="Año", default=2024)
    tipo = models.CharField(max_length=50, help_text="Ej: Vagoneta, Camioneta")
    color = models.CharField(max_length=30)
    capacidad_tanque = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    nro_chasis = models.CharField(max_length=50, unique=True)
    kilometraje_actual = models.PositiveIntegerField(default=0)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='operacional')
    rendimiento_km_litro = models.DecimalField(max_digits=5, decimal_places=2, default=5.00, help_text="KM por Litro")
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True)
    rendimiento_km_litro = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=6.0, 
        verbose_name="Rendimiento KM/L (Constante Matriz)"
    )
    combustible_inicial = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0.0, 
        verbose_name="Litros Iniciales en Tanque"
    )
    combustible_actual = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0.0, 
        verbose_name="Saldo de Combustible Actual"
    )
    fecha_actualizacion_rendimiento = models.DateField(
        auto_now_add=True, 
        verbose_name="Última actualización de rendimiento"
    )

    TIPOS_COMBUSTIBLE = (
        ('Gasolina', 'Gasolina'),
        ('Diesel', 'Diesel'),
    )
    tipo_combustible = models.ForeignKey(
        'TipoCombustible', 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='vehiculos'
    )
    def save(self, *args, **kwargs):
        accion = 'modificacion' if self.pk else 'creacion'
        if not self.pk:
            self.combustible_actual = self.combustible_inicial
        super().save(*args, **kwargs)
        
        LogAuditoria.objects.create(
            usuario_nombre="Sistema", 
            accion=accion,
            tabla="Vehiculo",
            descripcion=f"Se gestionó el vehículo {self.placa}"
        )
    def __str__(self):
        return f"{self.placa} - {self.marca} {self.modelo}"

class Asignacion(models.Model):
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.CASCADE)
    chofer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, limit_choices_to={'rol': 'chofer'})
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, verbose_name="Secretaría en ese periodo")
    fecha_asignacion = models.DateField(auto_now_add=True)
    nro_memorandum = models.CharField(max_length=50, verbose_name="Nro de Memorándum")
    documento_acta = models.FileField(upload_to='actas/', null=True, blank=True)
    esta_activo = models.BooleanField(default=True)
    fecha_fin = models.DateField(null=True, blank=True)
    motivo_baja = models.CharField(max_length=255, null=True, blank=True)
    documento_baja = models.FileField(upload_to='bajas/', null=True, blank=True)
    class Meta:
        pass

    def clean(self):
        # Usamos _id para evitar el error 'RelatedObjectDoesNotExist'
        # Esto permite validar la regla de negocio (1 chofer = 1 auto) de forma segura
        if self.esta_activo:
            # 1. Validar si el Chofer ya tiene vehículo
            if self.chofer_id:
                if Asignacion.objects.filter(chofer_id=self.chofer_id, esta_activo=True).exclude(pk=self.pk).exists():
                    raise ValidationError({'chofer': "Este conductor ya tiene un vehículo asignado actualmente."})
            
            # 2. Validar si el Vehículo ya está asignado
            if self.vehiculo_id:
                if Asignacion.objects.filter(vehiculo_id=self.vehiculo_id, esta_activo=True).exclude(pk=self.pk).exists():
                    raise ValidationError({'vehiculo': "Este vehículo ya se encuentra asignado a otro conductor."})

    def __str__(self):
        try:
            return f"{self.vehiculo.placa} - {self.chofer.get_full_name()}"
        except:
            return f"Asignación #{self.id}"

class Bitacora(models.Model):
    # 1. ESTADOS
    ESTADOS_VALIDACION = (
        ('pendiente', 'PENDIENTE'),
        ('validado', 'VALIDADO'),
        ('rechazado', 'RECHAZADO'),
        ('anomalia', 'ANOMALÍA'),
    )
    ESTADOS_VIAJE = (
        ('en_curso', 'En Curso'), 
        ('finalizado', 'Finalizado')
    )
    
    TERRENO_CHOICES = [('plano', 'Plano'), ('montana', 'Montaña'), ('mixto', 'Mixto')]
    CARGA_CHOICES = [('ligera', 'Ligera'), ('pesada', 'Pesada')]
    TIPO_RUTA_CHOICES = (
        ('Urbana', 'Urbana'),
        ('Interprovincial', 'Interprovincial'),
        ('Rural', 'Rural'),
        ('Mixta', 'Mixta'),
    )
    # Relaciones y Campos base
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.CASCADE)
    chofer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    fecha = models.DateTimeField(auto_now_add=True)

    reporte_cerrado = models.BooleanField(default=False)
    documento_enmienda = models.FileField(upload_to='enmiendas/', null=True, blank=True, verbose_name="Documento de Respaldo de Enmienda")
    # Control de Viaje
    estado_viaje = models.CharField(max_length=20, choices=ESTADOS_VIAJE, default='finalizado')
    estado_validacion = models.CharField(max_length=20, choices=ESTADOS_VALIDACION, default='validado')
    hora_salida = models.TimeField(null=True, blank=True)
    hora_llegada = models.TimeField(null=True, blank=True)
    responsable_viaje = models.CharField(max_length=150, blank=True, null=True)
    origen = models.CharField(max_length=150, default="Potosí")
    destino = models.CharField(max_length=255)
    objetivo_comision = models.TextField(verbose_name="Objetivo de la comisión")
    # Factores de Cálculo
    terreno = models.CharField(max_length=20, choices=TERRENO_CHOICES, default='plano')
    carga = models.CharField(max_length=20, choices=CARGA_CHOICES, default='ligera')
    rendimiento_aplicado = models.DecimalField(max_digits=5, decimal_places=2, default=6.0)
    # Kilometraje   
    km_inicial = models.PositiveIntegerField(default=0)
    km_final = models.PositiveIntegerField(default=0)
    distancia_estimada = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Combustible (Fórmulas del Reporte Matriz)
    saldo_anterior = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    nro_vale_combustible = models.CharField(max_length=50, verbose_name="Nro. de Vale", blank=True, null=True)
    cantidad_litros = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    combustible_utilizado = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    saldo_actual = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Otros
    costo_total = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Costo en Bs.", default=0)
    nro_factura = models.CharField(max_length=50, default="S/N")
    estacion_servicio = models.CharField(max_length=150, blank=True, null=True, verbose_name="Estación de Servicio")
    solicitud_correccion = models.BooleanField(default=False)
    motivo_correccion = models.TextField(blank=True, null=True)
    
    observacion_admin = models.TextField(verbose_name="Observación de Auditoría", blank=True, null=True)
    tipo_ruta = models.CharField(max_length=20, choices=TIPO_RUTA_CHOICES, default='Urbana')

    def save(self, *args, **kwargs):
        if self.estado_viaje == 'finalizado' and self.km_final > self.km_inicial:
            self.rendimiento_aplicado = self.vehiculo.rendimiento_km_litro if self.vehiculo.rendimiento_km_litro > 0 else Decimal('5.0')
            distancia = self.km_final - self.km_inicial
            factores_terreno = {'plano': 1.0, 'montana': 0.75, 'mixto': 0.85}
            factores_carga = {'ligera': 1.0, 'pesada': 0.85}
            f_t = Decimal(str(factores_terreno.get(self.terreno, 1.0)))
            f_c = Decimal(str(factores_carga.get(self.carga, 1.0)))
            rendimiento_final = self.rendimiento_aplicado * f_t * f_c
            recorrido = self.km_final - self.km_inicial
            self.combustible_utilizado = Decimal(recorrido) / rendimiento_final 
            self.saldo_actual = self.saldo_anterior + self.cantidad_litros - self.combustible_utilizado
            ultimo = Bitacora.objects.filter(vehiculo=self.vehiculo, estado_viaje='finalizado').exclude(id=self.id).order_by('-fecha', '-id').first()
            
            if ultimo:
                self.saldo_anterior = ultimo.saldo_actual
            else:
                self.saldo_anterior = self.vehiculo.combustible_inicial
            
            self.saldo_actual = self.saldo_anterior + self.cantidad_litros - self.combustible_utilizado
            
            if self.cantidad_litros > 0:
                margen = self.combustible_utilizado * Decimal('0.15')
                if abs(self.cantidad_litros - self.combustible_utilizado) > margen:
                    self.estado_validacion = 'anomalia'
                else:
                    self.estado_validacion = 'pendiente'
            vehiculo_a_actualizar = self.vehiculo
            vehiculo_a_actualizar.kilometraje_actual = self.km_final
            vehiculo_a_actualizar.save()
            v = self.vehiculo
            v.kilometraje_actual = self.km_final
            v.combustible_actual = self.saldo_actual 
            v.save()
            
        super().save(*args, **kwargs)
    @property
    def recorrido_real(self):
        return self.km_final - self.km_inicial
    def get_motivo_oficial(self):
        """
        Lógica de Negocio: Si todos los viajes del día son en Potosí/Tomás Frías,
        devuelve 'APOYO LOCAL'. De lo contrario, devuelve el motivo real.
        """
        # Palabras clave para identificar la zona local
        zona_local = ['potosí', 'potosi', 'tomas frías', 'tomas frias', 'tomás frías']
        
        # Obtenemos los viajes registrados en el formset para esta bitácora
        viajes = self.viajes.all()
        
        # Si el chofer no registró tramos detallados, usamos el objetivo general
        if not viajes.exists():
            return self.objetivo_comision
            
        es_todo_local = True
        
        for v in viajes:
            # Verificamos origen y destino de cada tramo
            o = v.origen.lower()
            d = v.destino.lower()
            
            # Si algún punto NO es local, marcamos como falso
            o_es_local = any(zona in o for zona in zona_local)
            d_es_local = any(zona in d for zona in zona_local)
            
            if not (o_es_local and d_es_local):
                es_todo_local = False
                break
        
        if es_todo_local:
            return "APOYO LOCAL"
        
        return self.objetivo_comision
    def __str__(self):
        f = self.fecha.strftime('%d/%m/%Y') if self.fecha else "En proceso"
        p = self.vehiculo.placa if self.vehiculo else "S/N"
        return f"Bitácora {f} - {p}"       
    
class InventarioCombustible(models.Model):
    tipo = models.CharField(max_length=50) 
    cantidad_total = models.DecimalField(max_digits=15, decimal_places=2)
    ultima_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.tipo}: {self.cantidad_total} Lts"

class AjusteSistema(models.Model):
    modo_seguro = models.BooleanField(default=False)
    ultima_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ajuste de Sistema"
        verbose_name_plural = "Ajustes de Sistema"

    def __str__(self):
        return f"Modo Seguro: {'ACTIVO' if self.modo_seguro else 'INACTIVO'}"

class Viaje(models.Model):
    bitacora = models.ForeignKey(Bitacora, on_delete=models.CASCADE, related_name='viajes')
    origen = models.CharField(max_length=100)
    destino = models.CharField(max_length=100)
    TIPO_RUTA = [('Urbana', 'Urbana'), ('Interprovincial', 'Interprovincial'), ('Rural', 'Rural'), ('Mixta', 'Mixta')]
    tipo_ruta = models.CharField(max_length=20, choices=TIPO_RUTA)
    hora_inicio = models.TimeField(null=True, blank=True) 
    hora_fin = models.TimeField(null=True, blank=True)
    km_inicio = models.PositiveIntegerField()
    km_fin = models.PositiveIntegerField()
    motivo = models.CharField(max_length=255)
    distancia_real = models.PositiveIntegerField(editable=False)

    @property
    def es_apoyo_local(self):
        zona = ['potosí', 'tomas frías', 'tarapaya']
        return any(z in self.origen.lower() for z in zona) and any(z in self.destino.lower() for z in zona)
    
    def km_recorridos(self):
        return self.km_fin - self.km_inicio

    def get_motivo_oficial(self):
        zona = ['potosí', 'potosi', 'tomas frías', 'tomas frias']
        if any(z in self.origen.lower() for z in zona) and any(z in self.destino.lower() for z in zona):
            return "Apoyo Local"
        return self.objetivo_comision
    
    def save(self, *args, **kwargs):
        self.distancia_real = self.km_fin - self.km_inicio
        super().save(*args, **kwargs)

class Peaje(models.Model):
    chofer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True)
    lugar = models.CharField(max_length=100)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha = models.DateTimeField(auto_now_add=True, null=True)
    comprobante = models.ImageField(upload_to='peajes/', blank=True, null=True)

    def __str__(self):
        return f"Peaje: {self.lugar} - Bs {self.monto}"       

class ValeCombustible(models.Model):
    chofer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    nro_vale = models.CharField(max_length=50, unique=True)
    cantidad_litros = models.DecimalField(max_digits=10, decimal_places=2)
    monto_bs = models.DecimalField(max_digits=10, decimal_places=2)
    fecha = models.DateTimeField(auto_now_add=True)
    comprobante = models.ImageField(upload_to='vales/', blank=True, null=True)

    def __str__(self):
        return f"Vale N° {self.nro_vale}"

def calcular_saldo_combustible(chofer, dias=15):
    limite = timezone.now() - timedelta(days=dias)
    bitacoras = Bitacora.objects.filter(chofer=chofer, fecha__gte=limite)
    
    total_cargado = bitacoras.aggregate(Sum('cantidad_litros'))['cantidad_litros__sum'] or 0
    total_recorrido = bitacoras.aggregate(total=Sum(F('km_final') - F('km_inicial')))['total'] or 0
    
    rendimiento = 5.0 
    consumo_real = total_recorrido / rendimiento
    
    return total_cargado - Decimal(consumo_real)

def calcular_distancia_geografica(lat1, lon1, lat2, lon2):
    R = 6371 
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

# gestion/models.py

class RegistroMantenimiento(models.Model):
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.CASCADE, related_name='mantenimientos')
    fecha_ingreso = models.DateField(verbose_name="Fecha de Ingreso")
    fecha_salida = models.DateField(null=True, blank=True, verbose_name="Fecha de Salida Real")
    motivo = models.CharField(max_length=255, verbose_name="Motivo del Mantenimiento")
    observaciones = models.TextField(blank=True, null=True)
    encargado_taller = models.CharField(max_length=150, blank=True, null=True)
    
    # Estado para saber si el mantenimiento sigue abierto
    finalizado = models.BooleanField(default=False)

    def __str__(self):
        return f"Mantenimiento {self.vehiculo.placa} - {self.fecha_ingreso}"

    class Meta:
        verbose_name = "Registro de Mantenimiento"
        verbose_name_plural = "Registros de Mantenimiento"

class LogAuditoria(models.Model):
    ACCIONES = (
        ('login_exitoso', 'Inicio de Sesión'),
        ('login_fallido', 'Intento Fallido'),
        ('logout', 'Cierre de Sesión'),
        ('creacion', 'Creación de Registro'),
        ('modificacion', 'Cambio de Datos'),
        ('eliminacion', 'Baja Lógica'),
        ('reporte', 'Generación de Reporte'),
        ('backup', 'Descarga de Backup'),
        ('error', 'Error de Sistema'),
    )

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    usuario_nombre = models.CharField(max_length=150, blank=True) # Respaldo si se borra el usuario
    rol = models.CharField(max_length=50, blank=True)
    accion = models.CharField(max_length=20, choices=ACCIONES)
    tabla = models.CharField(max_length=100, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    
    # Datos forenses
    valor_anterior = models.JSONField(null=True, blank=True)
    valor_nuevo = models.JSONField(null=True, blank=True)
    descripcion = models.TextField(blank=True)

    def __str__(self):
        return f"{self.fecha_hora} - {self.usuario_nombre} - {self.accion}"

class BitacoraActividad(models.Model):
    CATEGORIAS = (
        ('seguridad', 'SEGURIDAD / ACCESO'),
        ('operativo', 'OPERACIÓN DIARIA'),
        ('auditoria', 'AUDITORÍA / REPORTES'),
        ('sistema', 'SISTEMA / BACKUP'),
        ('error', 'ERROR DE SISTEMA'),
    )

    fecha_hora = models.DateTimeField(auto_now_add=True)
    usuario_texto = models.CharField(max_length=150, blank=True, null=True) # Por si el usuario es borrado
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    rol = models.CharField(max_length=50, blank=True, null=True)
    categoria = models.CharField(max_length=20, choices=CATEGORIAS)
    accion = models.CharField(max_length=100)
    descripcion = models.TextField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True) 

    class Meta:
        verbose_name = "Bitácora de Actividad"
        verbose_name_plural = "Bitácora de Actividades"

    def __str__(self):
        return f"[{self.categoria}] {self.accion} - {self.fecha_hora}"

class JustificacionEdicion(models.Model):
    superusuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    tabla_afectada = models.CharField(max_length=50)
    registro_id = models.PositiveIntegerField()
    motivo = models.TextField(verbose_name="Motivo de la corrección")
    documento_respaldo = models.FileField(upload_to='correcciones/%Y/%m/', verbose_name="Nota de Respaldo (PDF/JPG/PNG)")
    fecha_hora = models.DateTimeField(auto_now_add=True)
    observacion_adicional = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Enmienda {self.tabla_afectada} ID:{self.registro_id} - {self.fecha_hora.date()}"
    
class SolicitudEdicion(models.Model):
    ESTADOS = (('pendiente', 'Pendiente'), ('aprobada', 'Aprobada'), ('rechazada', 'Rechazada'))
    
    bitacora = models.ForeignKey(Bitacora, on_delete=models.CASCADE, related_name='solicitudes')
    chofer = models.ForeignKey(Usuario, on_delete=models.CASCADE)
    motivo_correccion = models.TextField()
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='pendiente')

    def __str__(self):
        return f"Solicitud de {self.chofer.username} para Bitácora #{self.bitacora.id}"