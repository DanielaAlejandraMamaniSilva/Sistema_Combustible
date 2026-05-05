# gestion/services.py
from decimal import Decimal
from .models import Bitacora, Asignacion

def procesar_bitacoras_periodo(chofer, anio, mes):
    # 1. Obtener datos iniciales
    vehiculo_asig = Asignacion.objects.filter(chofer=chofer, esta_activo=True).first()
    bitacoras = Bitacora.objects.filter(chofer=chofer, fecha__year=anio, fecha__month=mes).order_by('fecha')
    
    # 2. Definir Saldo Inicial (Normalmente vendría de una tabla de cierres mensuales)
    saldo_anterior = Decimal('133.90') 
    saldo_actual = saldo_anterior
    
    registros_calculados = []
    totales = {'cargado': 0, 'recorrido': 0, 'utilizado': 0}

    for b in bitacoras:
        recorrido = b.km_final - b.km_inicial
        
        # Rendimiento: Distancia / Litros cargados (si es 0, usamos rendimiento del vehículo)
        rendimiento = vehiculo_asig.vehiculo.rendimiento_km_litro if vehiculo_asig else Decimal('5.0')
        consumo_estimado = Decimal(recorrido) / rendimiento
        
        # Lógica de Saldo
        saldo_actual = saldo_actual + b.cantidad_litros - consumo_estimado
        
        # Identificar si es Apoyo Local
        es_apoyo = ("Potosi" in b.origen and "Potosi" in b.destino)
        
        registros_procesados = {
            'fecha': b.fecha,
            'vale': b.nro_vale_combustible,
            'ingreso': b.cantidad_litros,
            'utilizado': round(consumo_estimado, 2),
            'saldo': round(saldo_actual, 2),
            'objeto': "APOYO LOCAL" if es_apoyo else b.objetivo_comision,
            'salida': b.km_inicial,
            'llegada': b.km_final,
            'recorrido': recorrido,
        }
        registros_calculados.append(registros_procesados)
        
        # Sumar totales
        totales['cargado'] += b.cantidad_litros
        totales['recorrido'] += recorrido
        totales['utilizado'] += consumo_estimado

    return registros_calculados, totales, round(saldo_actual, 2)