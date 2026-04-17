from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings

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
    celular = models.CharField(max_length=15, null=True, blank=True)

    def __str__(self):
        return f"{self.username} - {self.get_rol_display()}"
    
class Vehiculo(models.Model):
    ESTADOS = (
        ('operacional', 'OPERACIONAL'),
        ('mantenimiento', 'MANTENIMIENTO'),
        ('fuera_servicio', 'FUERA DE SERVICIO'),
    )
    placa = models.CharField(max_length=15, unique=True)
    marca = models.CharField(max_length=50)
    modelo = models.CharField(max_length=50)
    anio = models.PositiveIntegerField(verbose_name="Año")
    capacidad_tanque = models.DecimalField(max_digits=10, decimal_places=2, help_text="En litros o galones")
    estado = models.CharField(max_length=20, choices=ESTADOS, default='operacional')
    tipo_vehiculo = models.CharField(max_length=50, help_text="Ej: Camioneta, Vagoneta, Bus")

    def __str__(self):
        return f"{self.placa} - {self.marca} {self.modelo}"

class Asignacion(models.Model):
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.CASCADE)
    chofer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, limit_choices_to={'rol': 'chofer'})
    fecha_asignacion = models.DateField(auto_now_add=True)
    memo_referencia = models.CharField(max_length=100, verbose_name="Número de Memorándum")
    acta_entrega = models.FileField(upload_to='actas/', null=True, blank=True)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.vehiculo.placa} -> {self.chofer.get_full_name()}"