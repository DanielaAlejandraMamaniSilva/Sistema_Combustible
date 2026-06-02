# gestion/context_processors.py
from .models import LogAuditoria

def notificaciones_sistema(request):
    if request.user.is_authenticated and request.user.rol == 'superadmin':
        # Traemos los últimos 5 eventos críticos o de seguridad
        logs = LogAuditoria.objects.all().order_by('-fecha_hora')[:5]
        return {'notificaciones_recientes': logs}
    return {'notificaciones_recientes': []}