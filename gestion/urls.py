from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('bitacora/nuevo/', views.dashboard_view, name='registro_bitacora'),
    path('vehiculo/estado/', views.dashboard_view, name='estado_vehiculo'),
    # Usuarios
    path('usuarios/', views.lista_usuarios, name='lista_usuarios'),
    path('usuarios/roles/', views.vista_roles, name='lista_roles'),
    path('usuarios/crear/', views.crear_usuario, name='crear_usuario'),
    path('usuarios/editar/<int:pk>/', views.editar_usuario, name='editar_usuario'),
    path('usuarios/eliminar/<int:pk>/', views.eliminar_usuario, name='eliminar_usuario'),

    path('catalogos/vehiculos/', views.lista_vehiculos, name='lista_vehiculos'),
    path('exportar-excel/', views.exportar_usuarios_excel, name='exportar_excel'),
    path('auditoria/logs/', views.log_auditoria, name='log_auditoria'),
    path('configuracion/', views.configuracion_global, name='config_sistema'),
    path('catalogos/', views.gestion_catalogos, name='gestion_catalogos'),
    path('usuarios/roles/', views.vista_roles, name='lista_roles'),
    path('soporte/', views.vista_soporte, name='soporte'),
    path('sistema/toggle-seguro/', views.toggle_modo_seguro, name='toggle_seguro'),
    path('buscar/', views.buscar_registros, name='buscar_registros'),
    path('sistema/respaldo/', views.forzar_respaldo, name='forzar_respaldo'),
    path('catalogos/vehiculo/nuevo/', views.crear_vehiculo, name='crear_vehiculo'),
    path('catalogos/vehiculo/editar/<int:pk>/', views.editar_vehiculo, name='editar_vehiculo'),
    path('catalogos/vehiculo/eliminar/<int:pk>/', views.eliminar_vehiculo, name='eliminar_vehiculo'),
    path('admin-dashboard/', views.dashboard_admin, name='dashboard_admin'),
    path('registros/visualizacion/', views.lista_registros_all, name='registros_visualizacion'),
    path('validacion/', views.validar_registros, name='validar_registros'),
    path('validacion/cambiar-estado/<int:pk>/<str:nuevo_estado>/', views.cambiar_estado_bitacora, name='cambiar_estado'),
    path('reportes/seleccion-chofer/', views.seleccionar_chofer_reporte, name='reporte_por_chofer'),
    path('reportes/planilla-oficial/<int:chofer_id>/', views.reporte_oficial_chofer, name='planilla_oficial'),
    path('monitoreo/', views.monitoreo_tiempo_real, name='monitoreo_real'),
    path('reportes/seleccion-vehiculo/', views.seleccionar_vehiculo_reporte, name='reporte_por_vehiculo_select'),
    path('reportes/vehiculo/<int:vehiculo_id>/', views.reporte_por_vehiculo, name='planilla_vehiculo'),
    #    Activos
    path('activos/asignar/', views.crear_asignacion, name='crear_asignacion'),
    path('activos/memorandums/', views.lista_memorandums, name='lista_memorandums'),
    path('activos/historial/', views.historial_asignaciones, name='historial_asignaciones'),
    path('activos/actas/', views.lista_actas, name='lista_actas'),
    #Bienes
    path('bienes/validar/<int:pk>/<str:estado>/', views.validar_consumo_accion, name='validar_consumo_accion'),
    path('bienes/supervision/', views.supervision_combustible, name='supervision_combustible'),
    path('bienes/validacion/', views.validacion_consumo, name='validar_consumo_bienes'),
    path('bienes/reportes/', views.reportes_bienes, name='reportes_bienes'),
    path('bienes/abastecimiento/', views.control_abastecimiento, name='control_abastecimiento'),
    path('bienes/nuevo-registro/', views.nuevo_registro_combustible, name='nuevo_registro_bienes'),
    #chofer
    path('chofer/historial/', views.historial_viajes_chofer, name='historial_chofer'),
    path('chofer/vehiculo/', views.detalle_vehiculo_chofer, name='vehiculo_chofer'),    
    path('ajax/calcular-ruta/', views.calcular_ruta_ajax, name='calcular_ruta_ajax'),
    path('chofer/vales-peajes/', views.lista_vales_peajes, name='lista_vales_peajes'),
    path('chofer/peajes/nuevo/', views.registrar_peaje, name='registrar_peaje'),
]

