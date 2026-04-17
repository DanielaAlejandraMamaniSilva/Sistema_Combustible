from django.contrib import admin
from .models import Usuario, Vehiculo, Asignacion, Bitacora

@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('username', 'rol', 'ci', 'is_staff')
    list_filter = ('rol',)

@admin.register(Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = ('placa', 'marca', 'modelo', 'estado', 'kilometraje_actual')
    search_fields = ('placa',)

@admin.register(Asignacion)
class AsignacionAdmin(admin.ModelAdmin):
    list_display = ('vehiculo', 'chofer', 'nro_memorandum', 'esta_activo')

@admin.register(Bitacora)
class BitacoraAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'vehiculo', 'chofer', 'nro_vale_combustible', 'cantidad_litros')