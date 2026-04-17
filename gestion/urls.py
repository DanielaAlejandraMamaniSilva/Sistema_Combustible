from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('bitacora/nuevo/', views.dashboard_view, name='registro_bitacora'),
    path('vehiculo/estado/', views.dashboard_view, name='estado_vehiculo'),
]