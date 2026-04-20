from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser

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
    area = models.ForeignKey('Area', on_delete=models.SET_NULL, null=True)

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
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.CASCADE)
    chofer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    fecha = models.DateTimeField(auto_now_add=True)
    
    # Datos de control
    km_inicial = models.PositiveIntegerField()
    km_final = models.PositiveIntegerField()
    destino = models.CharField(max_length=255)
    objetivo_comision = models.TextField(verbose_name="Objetivo de la comisión")
    
    # Combustible
    nro_vale_combustible = models.CharField(max_length=50, verbose_name="Nro. de Vale")
    cantidad_litros = models.DecimalField(max_digits=10, decimal_places=2)
    costo_total = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Costo en Bs.")
    
    nro_factura = models.CharField(max_length=50)

    ESTADOS_VALIDACION = (
        ('pendiente', 'PENDIENTE'),
        ('validado', 'VALIDADO'),
        ('rechazado', 'RECHAZADO'),
        ('animalia', 'ANOMALÍA')
    )
    estado_validacion = models.CharField(max_length=20, choices=ESTADOS_VALIDACION, default='pendiente')

    def save(self, *args, **kwargs):

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
    
class Area(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre

class TipoCombustible(models.Model):
    nombre = models.CharField(max_length=50, unique=True) 

    def __str__(self):
        return self.nombre
    
class AjusteSistema(models.Model):
    modo_seguro = models.BooleanField(default=False)
    ultima_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ajuste de Sistema"
        verbose_name_plural = "Ajustes de Sistema"

    def __str__(self):
        return f"Modo Seguro: {'ACTIVO' if self.modo_seguro else 'INACTIVO'}"