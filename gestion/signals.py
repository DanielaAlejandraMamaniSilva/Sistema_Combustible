from django.contrib.auth.signals import user_logged_in, user_login_failed, user_logged_out
from django.dispatch import receiver
from .models import LogAuditoria
from .utils import obtener_ip

@receiver(user_logged_in)
def log_login_exitoso(sender, request, user, **kwargs):
    LogAuditoria.objects.create(
        usuario=user,
        usuario_nombre=user.username,
        rol=user.get_rol_display(),
        accion='login_exitoso',
        ip=obtener_ip(request),
        descripcion="Acceso exitoso al sistema."
    )

@receiver(user_login_failed)
def log_login_fallido(sender, credentials, request, **kwargs):
    LogAuditoria.objects.create(
        usuario_nombre=credentials.get('username', 'Desconocido'),
        accion='login_fallido',
        ip=obtener_ip(request),
        descripcion=f"Intento de acceso fallido para el usuario: {credentials.get('username')}"
    )

@receiver(user_logged_out)
def log_cierre_sesion(sender, request, user, **kwargs):
    if user:
        LogAuditoria.objects.create(
            usuario=user,
            usuario_nombre=user.username,
            rol=user.get_rol_display(),
            accion='logout', # Asegúrate que este valor coincida con tus CHOICES en el modelo
            ip=obtener_ip(request),
            descripcion=f"El usuario {user.username} ha cerrado su sesión de forma segura."
        )