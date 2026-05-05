# gestion/utils.py
import json
import math
import os
import networkx as nx
from django.conf import settings

def calcular_distancia(coord1, coord2):
    R = 6371.0
    lat1, lon1 = map(math.radians, coord1)
    lat2, lon2 = map(math.radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)

def buscar_coordenadas(nombre_lugar):
    path = os.path.join(settings.BASE_DIR, 'static', 'data', 'potosi_calles.json')
    if not os.path.exists(path):
        return None
    
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        lista = data.get('elements', data.get('features', []))
        for element in lista:
            tags = element.get('tags', {})
            if tags.get('name', '').lower() == nombre_lugar.lower():
                return (float(element.get('lat', 0)), float(element.get('lon', 0)))
    return None

def obtener_ruta_dijkstra(origen, destino):
    G = nx.Graph()
    # Esta es una lista de prueba para que el sistema arranque
    # En producción esto debe cargarse desde tu archivo JSON o base de datos
    rutas = [
        ('Potosi', 'Uyuni', 205),
        ('Potosi', 'Sucre', 156),
        ('Potosi', 'Oruro', 315),
        ('Oruro', 'La Paz', 230)
    ]
    for o, d, k in rutas:
        G.add_edge(o, d, weight=k)
    
    try:
        distancia = nx.dijkstra_path_length(G, source=origen, target=destino, weight='weight')
        path = nx.dijkstra_path(G, source=origen, target=destino, weight='weight')
        return path, distancia
    except:
        return [], 0

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