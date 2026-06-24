# gestion/context_processors.py
from .models import LogAuditoria
from .models import Bitacora

def notificaciones_sistema(request):
    if request.user.is_authenticated and request.user.rol == 'superadmin':    
        logs = LogAuditoria.objects.all().order_by('-fecha_hora')[:5]
        return {'notificaciones_recientes': logs}
    return {'notificaciones_recientes': []}

def contadores_globales(request):
    if request.user.is_authenticated and request.user.rol == 'superadmin':
        return {
            'num_solicitudes_pendientes': Bitacora.objects.filter(solicitud_correccion=True).count()
        }
    return {'num_solicitudes_pendientes': 0}