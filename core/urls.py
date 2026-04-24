from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from gestion import views as gestion_views 
from gestion.views import mi_error_404

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', include('gestion.urls')),
]

# --- ESTO HACE QUE LOS ESTÁTICOS Y EL 404 FUNCIONEN SIEMPRE ---
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Forzar página 404 personalizada
handler404 = 'gestion.views.mi_error_404'