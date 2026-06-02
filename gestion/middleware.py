import threading
from django.utils import timezone
from .models import Usuario

_thread_locals = threading.local()

def get_current_request():
    return getattr(_thread_locals, 'request', None)

class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_locals.request = request
        response = self.get_response(request)
        return response
    
class ActualizarActividadMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Actualizamos la hora de actividad sin disparar señales de guardado pesadas
            Usuario.objects.filter(pk=request.user.pk).update(ultima_actividad=timezone.now())
        
        response = self.get_response(request)
        return response