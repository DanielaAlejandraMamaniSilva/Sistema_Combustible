from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from math import radians, sin, cos, sqrt, atan2
from decimal import Decimal
from django.utils import timezone
from datetime import datetime, timedelta
from django.db.models import Sum, F

class Usuario(AbstractUser):
    ROLES = (
        ('superadmin', 'Super Administrador'),
        ('admin', 'Administrador'),
        ('activos', 'Encargado de Activos'),
        ('bienes', 'Bienes y Servicios'),
        ('chofer', 'Chofer'),
    )
    rol = models.CharField(max_length=20, choices=ROLES, default='chofer')
    ci = models.CharField(max_length=15, unique=True, null=True, blank=True)
    licencia_conducir = models.CharField(max_length=20, null=True, blank=True)
    vencimiento_licencia = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.get_rol_display()})"

class Area(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre

class TipoCombustible(models.Model):
    nombre = models.CharField(max_length=50, unique=True) 

    def __str__(self):
        return self.nombre

class Vehiculo(models.Model):
    ESTADOS = (
        ('operacional', 'OPERACIONAL'),
        ('mantenimiento', 'MANTENIMIENTO'),
        ('fuera_servicio', 'FUERA DE SERVICIO'),
    )
    placa = models.CharField(max_length=15, unique=True)
    marca = models.CharField(max_length=50)
    modelo = models.CharField(max_length=50)
    tipo = models.CharField(max_length=50, help_text="Ej: Vagoneta, Camioneta")
    color = models.CharField(max_length=30)
    nro_chasis = models.CharField(max_length=50, unique=True)
    kilometraje_actual = models.PositiveIntegerField(default=0)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='operacional')
    rendimiento_km_litro = models.DecimalField(max_digits=5, decimal_places=2, default=5.00, help_text="KM por Litro")
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True)

    TIPOS_COMBUSTIBLE = (
        ('Gasolina', 'Gasolina'),
        ('Diesel', 'Diesel'),
    )
    tipo_combustible = models.CharField(max_length=20, choices=TIPOS_COMBUSTIBLE, default='Gasolina')

    def __str__(self):
        return f"{self.placa} - {self.marca} {self.modelo}"

class Asignacion(models.Model):
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.CASCADE)
    chofer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, limit_choices_to={'rol': 'chofer'})
    fecha_asignacion = models.DateField(auto_now_add=True)
    nro_memorandum = models.CharField(max_length=50, verbose_name="Nro de Memorándum")
    documento_acta = models.FileField(upload_to='actas/', null=True, blank=True)
    esta_activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.vehiculo.placa} asignado a {self.chofer.username}"

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

    # 2. RELACIONES
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.CASCADE)
    chofer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    fecha = models.DateTimeField(auto_now_add=True)
    reporte_cerrado = models.BooleanField(default=False)
    
    # 3. CAMPOS DE CONTROL
    estado_viaje = models.CharField(max_length=20, choices=ESTADOS_VIAJE, default='finalizado')
    estado_validacion = models.CharField(max_length=20, choices=ESTADOS_VALIDACION, default='pendiente')
    
    observacion_admin = models.TextField(verbose_name="Observación de Auditoría", blank=True, null=True)
    hora_salida = models.TimeField(null=True, blank=True)
    hora_llegada = models.TimeField(null=True, blank=True)
    responsable_viaje = models.CharField(max_length=150, blank=True, null=True)
    origen = models.CharField(max_length=150, default="Potosí")
    
    terreno = models.CharField(max_length=20, choices=TERRENO_CHOICES, default='plano')
    carga = models.CharField(max_length=20, choices=CARGA_CHOICES, default='ligera')
    tipo_ruta = models.CharField(max_length=20, choices=TIPO_RUTA_CHOICES, default='Urbana')
    distancia_estimada = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    km_inicial = models.PositiveIntegerField(default=0)
    km_final = models.PositiveIntegerField(default=0)
    destino = models.CharField(max_length=255)
    objetivo_comision = models.TextField(verbose_name="Objetivo de la comisión")
    
    # 4. COMBUSTIBLE
    nro_vale_combustible = models.CharField(max_length=50, verbose_name="Nro. de Vale", blank=True, null=True)
    cantidad_litros = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    costo_total = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Costo en Bs.", default=0)
    nro_factura = models.CharField(max_length=50, default="S/N")

    def save(self, *args, **kwargs):
        # SOLO ejecutamos las matemáticas y actualizamos KM si el viaje ya FINALIZÓ
        if self.estado_viaje == 'finalizado' and self.km_final > self.km_inicial:
            
            # 1. Rendimiento base del vehículo
            rendimiento_base = self.vehiculo.rendimiento_km_litro if self.vehiculo.rendimiento_km_litro > 0 else Decimal('5.0')
            
            # 2. Factores de ajuste por terreno y carga (Control de combustible inteligente)
            factores_terreno = {'plano': 1.0, 'montana': 0.75, 'mixto': 0.85}
            factores_carga = {'ligera': 1.0, 'pesada': 0.85}
            
            # 3. Cálculo del rendimiento final adaptado
            rendimiento_final = rendimiento_base * Decimal(str(factores_terreno.get(self.terreno, 1.0))) * Decimal(str(factores_carga.get(self.carga, 1.0)))
            
            # 4. Cálculo de distancia real
            distancia = self.km_final - self.km_inicial
            
            # 5. Cálculo de consumo teórico estimado
            consumo_teorico = Decimal(distancia) / rendimiento_final
            
            # 6. Margen de tolerancia automatizado (15% según requerimiento)
            margen_tolerancia = consumo_teorico * Decimal('0.15')
            
            # 7. Comparación y generación de alertas
            if self.cantidad_litros > 0:
                diferencia = abs(Decimal(self.cantidad_litros) - consumo_teorico)
                if diferencia > margen_tolerancia:
                    self.estado_validacion = 'anomalia'
                else:
                    self.estado_validacion = 'pendiente'

            # Sincronización del kilometraje actual del activo
            self.vehiculo.kilometraje_actual = self.km_final
            self.vehiculo.save()
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Bitácora {self.fecha.date()} - {self.vehiculo.placa}"
    
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
        if "Potosí" in self.origen and "Potosí" in self.destino:
            return "APOYO LOCAL"
        return f"{self.origen} a {self.destino}"
    
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

class Ciudad(models.Model):
    nombre = models.CharField(max_length=100)
    lat = models.FloatField()
    lon = models.FloatField()

    def __str__(self):
        return self.nombre

class Ruta(models.Model):
    origen = models.ForeignKey(Ciudad, related_name='rutas_origen', on_delete=models.CASCADE)
    destino = models.ForeignKey(Ciudad, related_name='rutas_destino', on_delete=models.CASCADE)
    km = models.FloatField()
    tiempo_horas = models.FloatField()

    class Meta:
        unique_together = ('origen', 'destino')

    def __str__(self):
        return f"{self.origen.nombre} a {self.destino.nombre}"

class ValeCombustible(models.Model):
    chofer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    nro_vale = models.CharField(max_length=50, unique=True)
    cantidad_litros = models.DecimalField(max_digits=10, decimal_places=2)
    monto_bs = models.DecimalField(max_digits=10, decimal_places=2)
    fecha = models.DateTimeField(auto_now_add=True)
    comprobante = models.ImageField(upload_to='vales/', blank=True, null=True)

    def __str__(self):
        return f"Vale N° {self.nro_vale}"

# ==========================================
# FUNCIONES AUXILIARES DE CÁLCULO LOGÍSTICO
# ==========================================
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