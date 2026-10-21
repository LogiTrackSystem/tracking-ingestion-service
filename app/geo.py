import bisect
import math


def haversine_km(lat1, lon1, lat2, lon2):
    radio = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * radio * math.asin(math.sqrt(a))


def acumulados(puntos):
    """Distancia acumulada (km) sobre la polilínea en cada punto."""
    acc = [0.0]
    for i in range(1, len(puntos)):
        acc.append(acc[-1] + haversine_km(puntos[i - 1][0], puntos[i - 1][1], puntos[i][0], puntos[i][1]))
    return acc


def posicion_en(puntos, acum, fraccion):
    """Posición (lat, lon) a la fracción indicada (0 a 1) del recorrido de la polilínea."""
    if not puntos:
        return None
    fraccion = max(0.0, min(1.0, fraccion))
    largo = acum[-1]
    if largo <= 0 or fraccion <= 0:
        return float(puntos[0][0]), float(puntos[0][1])
    if fraccion >= 1:
        return float(puntos[-1][0]), float(puntos[-1][1])
    objetivo = fraccion * largo
    i = min(bisect.bisect_right(acum, objetivo) - 1, len(puntos) - 2)
    tramo = acum[i + 1] - acum[i]
    t = 0.0 if tramo <= 0 else (objetivo - acum[i]) / tramo
    lat = puntos[i][0] + t * (puntos[i + 1][0] - puntos[i][0])
    lon = puntos[i][1] + t * (puntos[i + 1][1] - puntos[i][1])
    return float(lat), float(lon)

def decodificar_polyline(cadena, precision=5):
    """Convierte la polyline codificada de OSRM/Routing en una lista de [lat, lon]."""
    indice = lat = lon = 0
    factor = 10 ** precision
    puntos = []
    while indice < len(cadena):
        for coordenada in (0, 1):
            desplazamiento = resultado = 0
            while True:
                b = ord(cadena[indice]) - 63
                indice += 1
                resultado |= (b & 0x1F) << desplazamiento
                desplazamiento += 5
                if b < 0x20:
                    break
            delta = ~(resultado >> 1) if resultado & 1 else resultado >> 1
            if coordenada == 0:
                lat += delta
            else:
                lon += delta
        puntos.append([lat / factor, lon / factor])
    return puntos


def distancia_total(puntos):
    acum = acumulados(puntos)
    return acum[-1] if acum else 0.0
