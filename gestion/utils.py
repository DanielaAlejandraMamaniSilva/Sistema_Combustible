import json
import math
from django.conf import settings
import os

def calcular_distancia(origen_coords, destino_coords):
    # Radio de la Tierra en Km
    R = 6371.0
    lat1, lon1 = map(math.radians, origen_coords)
    lat2, lon2 = map(math.radians, destino_coords)
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)

def buscar_coordenadas(nombre_lugar):
    # Carga tu archivo local sin internet
    path = os.path.join(settings.STATICFILES_DIRS[0], 'data', 'potosi_calles.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        # Buscamos nodos o formas que tengan ese nombre en el GeoJSON
        for element in data['elements']:
            if 'tags' in element and element['tags'].get('name') == nombre_lugar:
                # Retorna lat/lon promedio
                return (element['lat'], element['lon'])
    return None