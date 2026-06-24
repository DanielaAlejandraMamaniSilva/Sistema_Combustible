import json
from django.forms.models import model_to_dict
from django.apps import apps
from decimal import Decimal
from datetime import datetime, date  
import json 
from django.db import models

def es_zona_local(lugar):
    zona = ['potosí', 'tomas frias', 'tomás frías', 'cantumarca', 'tarapaya']
    lugar = lugar.lower()
    return any(z in lugar for z in zona)

def evaluar_tipo_bitacora(bitacora):
    viajes = bitacora.viajes.all()
    
    if not viajes.exists():
        return bitacora.objetivo_comision
        
    es_local = True
    for v in viajes:
        if not (es_zona_local(v.origen) and es_zona_local(v.destino)):
            es_local = False
            break
            
    return "APOYO LOCAL" if es_local else bitacora.objetivo_comision

def obtener_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

def registrar_auditoria(instance, accion, anterior=None):
    from .middleware import get_current_request
    from .models import LogAuditoria
    
    request = get_current_request()
    if not request or not request.user.is_authenticated:
        return

    nuevo = model_to_dict(instance)
    
    for key, val in nuevo.items():
        if isinstance(val, (datetime, date)): nuevo[key] = str(val)

    LogAuditoria.objects.create(
        usuario=request.user,
        tabla=instance._meta.model_name,
        accion=accion,
        ip=obtener_ip(request),
        valor_anterior=anterior,
        valor_nuevo=nuevo,
        objeto_id=instance.pk
    )


def generar_sql_insert(modelo_nombre):
    """Genera sentencias INSERT compatibles con JSONB y tipos de datos de Postgres"""
    try:
        model = apps.get_model('gestion', modelo_nombre)
    except LookupError:
        return f"-- Error: Modelo {modelo_nombre} no encontrado\n"
        
    table_name = model._meta.db_table
    queryset = model.objects.all()
    sql_lines = []

    for obj in queryset:
        fields = []
        values = []
        for field in obj._meta.fields:
            fields.append(f'"{field.column}"')
            val = getattr(obj, field.attname) 
            
            if val is None:
                values.append("NULL")
            elif isinstance(field, (models.JSONField)):
                # TRATAMIENTO ESPECIAL PARA JSON:
                # Convertimos a string con comillas dobles y escapamos comillas simples para el SQL
                json_str = json.dumps(val).replace("'", "''")
                values.append(f"'{json_str}'")
            elif isinstance(val, bool):
                values.append('true' if val else 'false')
            elif isinstance(val, (int, float, Decimal)):
                values.append(str(val))
            else:
                # Para textos y fechas, escapamos comillas simples
                safe_val = str(val).replace("'", "''")
                values.append(f"'{safe_val}'")
        
        line = f'INSERT INTO {table_name} ({", ".join(fields)}) VALUES ({", ".join(values)});'
        sql_lines.append(line)
    
    return "\n".join(sql_lines)

def generar_respaldo_sql(modulos_seleccionados):
    """Genera el contenido completo del archivo .sql"""
    mapeo = {
        'usuarios': ['Usuario'],
        'vehiculos': ['Vehiculo'],
        'bitacoras': ['Bitacora', 'Viaje'],
        'combustible': ['ValeCombustible', 'InventarioCombustible', 'Peaje'],
        'secretarias': ['Area'],
        'auditoria': ['LogAuditoria'],
    }

    output = "-- BACKUP SOBERANÍA POTOSÍ\n\n"
    for mod in modulos_seleccionados:
        modelos = mapeo.get(mod, [])
        for m in modelos:
            output += f"-- MODULO: {mod} | TABLA: {m}\n"
            output += generar_sql_insert(m) + "\n\n"
    return output