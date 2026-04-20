from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('bitacora/nuevo/', views.dashboard_view, name='registro_bitacora'),
    path('vehiculo/estado/', views.dashboard_view, name='estado_vehiculo'),
    path('usuarios/', views.lista_usuarios, name='lista_usuarios'),
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
]
