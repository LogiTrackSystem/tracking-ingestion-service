import asyncio
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, desc, or_
from sqlalchemy.exc import IntegrityError

from . import clientes_http
from .database import SessionLocal
from .events import publish_event
from .geo import acumulados, decodificar_polyline, haversine_km, posicion_en
from .models import CargaCompartida, LecturaTelemetria, SimulacionEvento, SimulacionViaje

TICK_SEGUNDOS = int(os.getenv("SIM_TICK_SEGUNDOS", "5"))
FACTOR_TIEMPO = float(os.getenv("SIM_FACTOR_TIEMPO", "120"))
COMBUSTIBLE_INICIAL_PCT = float(os.getenv("SIM_COMBUSTIBLE_INICIAL_PCT", "100"))

RESERVA_PCT = 15.0          # reserva de combustible que se busca conservar al salir de una parada
NIVEL_CRITICO_PCT = 8.0     # por debajo de esto sin parada a la vista, recarga de emergencia
PARADA_RECARGA_MIN = 20     # minutos simulados que dura una recarga
MAX_DT_REAL_S = 60          # tope de segundos por avance (evita saltos tras una pausa del servicio)
ESTADOS_ACTIVOS = ("preparando", "en_curso", "pausada")
UMBRAL_MISMO_LUGAR_KM = float(os.getenv("UMBRAL_MISMO_LUGAR_KM", "2"))   # por debajo: ya está en el origen
MAX_INTENTOS_UBICACION_ANTES_DE_LLEGAR = 3   # intentos de avisar a Fleet antes de publicar la llegada de todos modos
MAX_INTENTOS_UBICACION = 500

# tipo de vehículo -> (tanque en litros, km por litro, combustible), si Fleet no tiene los datos
PERFIL_POR_TIPO = {
    "furgoneta": (70.0, 9.0, "diesel"),
    "camion": (200.0, 4.5, "diesel"),
    "tractocamion": (400.0, 3.0, "diesel"),
}
PERFIL_DEFECTO = (120.0, 6.0, "diesel")


def _ahora():
    return datetime.now(timezone.utc)


def _numero(valor):
    try:
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _evento(db, sim, tipo, descripcion=None, km=None, lat=None, lon=None, nivel_pct=None):
    db.add(SimulacionEvento(
        simulacion_id=sim.id,
        envio_id=sim.envio_id,
        tipo=tipo,
        descripcion=descripcion,
        km=sim.km_recorridos if km is None else km,
        combustible_pct=sim.combustible_pct if nivel_pct is None else round(nivel_pct, 1),
        latitud=sim.latitud if lat is None else lat,
        longitud=sim.longitud if lon is None else lon,
    ))


# ---------- Telemetría ----------

def _registrar_lectura(db, sim, ahora, velocidad):
    odometro = round((sim.odometro_inicio_km or 0.0) + (sim.km_reposicion or 0.0) + (sim.km_recorridos or 0.0), 2)
    en_marcha = velocidad > 0
    temperatura_motor = round(random.uniform(85, 95) if en_marcha else random.uniform(60, 70), 1)
    temperatura_carga = round(random.uniform(2, 6), 1) if sim.tipo_carga == "refrigerada" else None
    db.add(LecturaTelemetria(
        tiempo=ahora,
        vehiculo_id=sim.vehiculo_id,
        latitud=round(sim.latitud, 6),
        longitud=round(sim.longitud, 6),
        velocidad_kmh=round(velocidad, 2),
        nivel_combustible_pct=sim.combustible_pct,
        temperatura_carga_c=temperatura_carga,
        kilometraje_acumulado_km=odometro,
        horas_motor=round(sim.horas_motor or 0.0, 2),
        temperatura_motor_c=temperatura_motor,
    ))
    return {
        "vehiculo_id": str(sim.vehiculo_id),
        "ruta_id": str(sim.ruta_id),
        "fase": sim.fase,   # "reposicion" (vacío, no se factura) o "viaje" (con carga)
        "tiempo": ahora,
        "latitud": round(sim.latitud, 6),
        "longitud": round(sim.longitud, 6),
        "velocidad_kmh": round(velocidad, 2),
        "nivel_combustible_pct": sim.combustible_pct,
        "temperatura_carga_c": temperatura_carga,
        "kilometraje_acumulado_km": odometro,
        "horas_motor": round(sim.horas_motor or 0.0, 2),
        "codigo_obd2": None,
        "temperatura_motor_c": temperatura_motor,
    }


async def _publicar_lectura(payload):
    try:
        await publish_event("telemetry.raw", payload)
    except Exception as exc:
        print(f"[tracking] no se pudo publicar la lectura: {exc}")


# ---------- Creación de la simulación (evento route.assigned) ----------

def _anfitrion_paso_recogida(host, km_recogida: float) -> bool:
    """True si el vehículo anfitrión ya pasó el punto de recogida (o terminó sin llegar): la carga ya no puede subir.
    Un anfitrión que aún se prepara o va vacío hacia su origen no ha pasado nada."""
    if host.estado == "cancelada" or (host.estado == "finalizada" and host.cierre_ubicacion in ("interrupcion", "restaurar")):
        return True
    if host.estado not in ("en_curso", "pausada", "finalizada") or host.fase == "reposicion":
        return False
    km = host.distancia_total_km if host.estado == "finalizada" else (host.km_recorridos or 0.0)
    return (km or 0.0) > km_recogida + 1e-6


async def _registrar_carga_compartida(payload: dict):
    """Un envío que viaja en el vehículo de otro: no tiene simulación propia, sube y baja según el km del anfitrión."""
    ruta_id = uuid.UUID(payload["ruta_id"])
    anfitriona = uuid.UUID(payload["ruta_anfitriona_id"])
    origen, destino = payload.get("origen") or {}, payload.get("destino") or {}
    datos = dict(
        envio_id=uuid.UUID(payload["envio_id"]), ruta_anfitriona_id=anfitriona,
        vehiculo_id=uuid.UUID(payload["vehiculo_id"]),
        conductor_id=uuid.UUID(payload["conductor_id"]) if payload.get("conductor_id") else None,
        placa=payload.get("placa"), conductor_nombre=payload.get("conductor_nombre"), codigo_ruta=payload.get("codigo_ruta"),
        origen_nombre=origen.get("nombre"), destino_nombre=destino.get("nombre"),
        km_recogida=float(payload["km_recogida"]), km_entrega=float(payload["km_entrega"]),
    )
    db = SessionLocal()
    try:
        fila = db.query(CargaCompartida).filter(CargaCompartida.ruta_id == ruta_id).first()
        if fila is not None:
            if fila.ruta_anfitriona_id == anfitriona or fila.estado not in ("pendiente", "cancelada"):
                return  # mensaje repetido, o ya subió/bajó: no se toca
            for k, v in datos.items():   # la misma solicitud se reasignó a otro anfitrión: se rearma
                setattr(fila, k, v)
            fila.estado, fila.inicio_publicado, fila.llegada_publicada = "pendiente", False, False
            fila.reencolar_pendiente = False
            fila.iniciado_en = fila.llegada_en = None
        else:
            fila = CargaCompartida(ruta_id=ruta_id, **datos)
            db.add(fila)
        host = db.query(SimulacionViaje).filter(SimulacionViaje.ruta_id == anfitriona).first()
        if host is not None and _anfitrion_paso_recogida(host, fila.km_recogida):
            # Routing decidió con un avance atrasado: el vehículo ya pasó por ahí. NO se da por recogida; se le avisa a
            # Routing (tracking.shared_load_missed) para que la reasigne a otro vehículo.
            fila.estado = "cancelada"
            fila.reencolar_pendiente = True
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    finally:
        db.close()


async def procesar_route_assigned(payload: dict):
    if payload.get("modo") == "compartida" and payload.get("ruta_anfitriona_id"):
        await _registrar_carga_compartida(payload)
        return
    ruta_id = uuid.UUID(payload["ruta_id"])
    db = SessionLocal()
    try:
        if db.query(SimulacionViaje).filter(SimulacionViaje.ruta_id == ruta_id).first():
            return  # mensaje repetido
        reposicion = payload.get("reposicion") or None
        sim = SimulacionViaje(
            envio_id=uuid.UUID(payload["envio_id"]),
            ruta_id=ruta_id,
            vehiculo_id=uuid.UUID(payload["vehiculo_id"]),
            conductor_id=uuid.UUID(payload["conductor_id"]) if payload.get("conductor_id") else None,
            placa=payload.get("placa"),
            conductor_nombre=payload.get("conductor_nombre"),
            codigo_ruta=payload.get("codigo_ruta"),
            ruta_maestra_id=uuid.UUID(payload["ruta_maestra_id"]) if payload.get("ruta_maestra_id") else None,
            estado="preparando",
            reposicion_datos=reposicion,
            desde=(reposicion or {}).get("desde"),
            origen_info=payload.get("origen"),
            destino_info=payload.get("destino"),
        )
        db.add(sim)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return
        _evento(db, sim, "creada", "Viaje programado; se prepara la ruta y el vehículo")
        db.commit()
    finally:
        db.close()


# ---------- Preparación: reúne los datos y arranca el viaje ----------

def _perfil_combustible(vehiculo: dict):
    tanque_d, consumo_d, combustible_d = PERFIL_POR_TIPO.get((vehiculo.get("tipo") or "").lower(), PERFIL_DEFECTO)
    tanque = _numero(vehiculo.get("capacidad_tanque_l"))
    consumo = _numero(vehiculo.get("consumo_km_por_litro"))
    estimado = not tanque or not consumo
    return (
        tanque or tanque_d,
        consumo or consumo_d,
        vehiculo.get("tipo_combustible") or combustible_d,
        estimado,
    )


async def _preparar(db, sim):
    try:
        envio = await clientes_http.obtener_envio(sim.envio_id)
        if envio.get("estado") in ("entregado", "devuelto", "cancelado"):
            sim.estado = "cancelada"
            sim.ultimo_error = f"El envío ya está {envio['estado']}"
            _marcar_cierre_ubicacion(sim)   # nunca salió: el vehículo conserva su ubicación anterior
            _evento(db, sim, "cancelada", sim.ultimo_error)
            db.commit()
            return
        sim.codigo_envio = envio.get("codigo_envio")
        sim.origen = envio.get("origen")
        sim.destino = envio.get("destino")
        sim.tipo_carga = envio.get("tipo_carga")
        if envio.get("estado") == "retenido_aduana":
            sim.ultimo_error = "RETENIDO_ADUANA: el viaje inicia cuando aduanas libere el envío"
            db.commit()
            return

        if sim.puntos is None:
            if sim.ruta_maestra_id is None:
                sim.estado = "cancelada"
                sim.ultimo_error = "La asignación no trae ruta maestra"
                _evento(db, sim, "cancelada", sim.ultimo_error)
                db.commit()
                return
            maestra = await clientes_http.obtener_ruta_maestra(sim.ruta_maestra_id)
            geometria = await clientes_http.obtener_geometria(sim.ruta_maestra_id)
            sim.distancia_total_km = float(maestra["distancia_km"])
            sim.duracion_horas = float(maestra["duracion_horas"])
            sim.paradas = maestra.get("paradas") or []
            sim.puntos = geometria["puntos"]

        if sim.puntos_reposicion is None and not (sim.reposicion_km or 0.0):
            await _resolver_reposicion(db, sim)

        if sim.capacidad_tanque_l is None:
            vehiculo = await clientes_http.obtener_vehiculo(sim.vehiculo_id)
            tanque, consumo, combustible, estimado = _perfil_combustible(vehiculo)
            sim.capacidad_tanque_l = tanque
            sim.consumo_km_por_litro = consumo
            sim.tipo_combustible = combustible
            sim.combustible_estimado = estimado
    except clientes_http.DatosNoDisponibles as exc:
        db.rollback()
        sim.intentos = (sim.intentos or 0) + 1
        sim.ultimo_error = f"DATOS_NO_DISPONIBLES: {exc}"
        db.commit()
        return

    if not sim.puntos or len(sim.puntos) < 2 or not sim.distancia_total_km:
        sim.ultimo_error = "La ruta no trae un trazado válido"
        db.commit()
        return

    ahora = _ahora()
    ultima = (
        db.query(LecturaTelemetria)
        .filter(LecturaTelemetria.vehiculo_id == sim.vehiculo_id)
        .order_by(desc(LecturaTelemetria.tiempo))
        .first()
    )
    sim.odometro_inicio_km = float(ultima.kilometraje_acumulado_km) if ultima and ultima.kilometraje_acumulado_km is not None else 0.0
    sim.horas_motor = float(ultima.horas_motor) if ultima and ultima.horas_motor is not None else 0.0
    sim.velocidad_promedio_kmh = max(5.0, sim.distancia_total_km / sim.duracion_horas) if sim.duracion_horas else 60.0
    sim.combustible_l = round(sim.capacidad_tanque_l * COMBUSTIBLE_INICIAL_PCT / 100.0, 2)
    sim.km_recorridos = 0.0
    sim.proxima_parada = 0
    reposicion = sim.reposicion_km or 0.0
    sim.km_reposicion = 0.0
    if reposicion > 0 and sim.puntos_reposicion:
        sim.latitud, sim.longitud = float(sim.puntos_reposicion[0][0]), float(sim.puntos_reposicion[0][1])
        sim.iniciado_en = None   # "iniciado" = sale con la carga; ahora todavía va en vacío hacia el origen
    else:
        sim.latitud, sim.longitud = float(sim.puntos[0][0]), float(sim.puntos[0][1])
        sim.iniciado_en = ahora
    sim.velocidad_actual_kmh = 0.0
    sim.ultima_actualizacion = ahora
    sim.hora_estimada_llegada = ahora + timedelta(
        seconds=(reposicion + sim.distancia_total_km) / sim.velocidad_promedio_kmh * 3600.0 / FACTOR_TIEMPO)
    sim.estado = "en_curso"
    sim.ultimo_error = None
    if reposicion > 0:
        _evento(db, sim, "inicio", f"Sale hacia {sim.origen} ({reposicion:.0f} km en vacío) para recoger la carga; "
                                   f"luego va a {sim.destino} ({sim.distancia_total_km:.0f} km)")
    else:
        _evento(db, sim, "inicio", f"Sale de {sim.origen} hacia {sim.destino} ({sim.distancia_total_km:.0f} km)")
    payload = _registrar_lectura(db, sim, ahora, 0.0)
    db.commit()
    await _publicar_lectura(payload)


# ---------- Tramo en vacío: el vehículo sale de DONDE ESTÁ ----------

def _fijar_reposicion(sim, puntos, km, fuente, motivo=None):
    origen = sim.puntos[0]
    if puntos and haversine_km(puntos[-1][0], puntos[-1][1], origen[0], origen[1]) > 0.05:
        puntos = [*puntos, [float(origen[0]), float(origen[1])]]   # el tramo termina exactamente donde empieza la ruta
    sim.puntos_reposicion = puntos
    sim.reposicion_km = float(km)
    sim.km_reposicion = 0.0
    datos = dict(sim.reposicion_datos or {})
    datos.pop("geometria", None)   # ya está decodificada en puntos_reposicion
    datos.update({"fuente": fuente, "distancia_km": round(float(km), 2), "resuelta": motivo or "routing"})
    sim.reposicion_datos = datos


def _sin_reposicion(sim, resuelta: str):
    sim.reposicion_km = 0.0
    sim.km_reposicion = 0.0
    sim.puntos_reposicion = []
    datos = dict(sim.reposicion_datos or {})
    datos.pop("geometria", None)
    datos["resuelta"] = resuelta
    sim.reposicion_datos = datos


async def _resolver_reposicion(db, sim):
    """Decide desde dónde sale el vehículo. Prioridad: (1) el tramo que calculó Routing desde la última ubicación
    real que Fleet tiene; (2) si Fleet no sabe dónde está, la última lectura real de Tracking; (3) si no hay nada,
    se admite que no se conoce y arranca en el origen (se deja constancia). NUNCA se inventa una posición."""
    datos = sim.reposicion_datos or {}
    origen = sim.puntos[0]
    if datos.get("geometria") and float(datos.get("distancia_km") or 0) > 0:
        _fijar_reposicion(sim, decodificar_polyline(datos["geometria"]), datos["distancia_km"], datos.get("fuente"))
        return
    if datos.get("fuente") == "mismo_lugar" or (datos.get("distancia_km") is not None and float(datos["distancia_km"]) == 0):
        _sin_reposicion(sim, "mismo_lugar")
        return
    # Fleet no tenía una ubicación fiable: se usa la última lectura real del propio vehículo
    ultima = (
        db.query(LecturaTelemetria)
        .filter(LecturaTelemetria.vehiculo_id == sim.vehiculo_id)
        .order_by(desc(LecturaTelemetria.tiempo))
        .first()
    )
    if ultima is None:
        _sin_reposicion(sim, "sin_ubicacion_conocida")
        return
    lat, lon = float(ultima.latitud), float(ultima.longitud)
    if haversine_km(lat, lon, float(origen[0]), float(origen[1])) < UMBRAL_MISMO_LUGAR_KM:
        _sin_reposicion(sim, "mismo_lugar")
        return
    try:
        tramo = await clientes_http.obtener_trayecto(lat, lon, float(origen[0]), float(origen[1]))
    except clientes_http.DatosNoDisponibles as exc:
        if "HTTP 422" in str(exc):   # no hay carretera entre ambos puntos
            _sin_reposicion(sim, "sin_carretera")
            return
        raise
    if not tramo.get("geometria") or not tramo.get("distancia_km"):
        _sin_reposicion(sim, "mismo_lugar")
        return
    sim.desde = {"latitud": lat, "longitud": lon, "fuente": "telemetria", "tomada_en": ultima.tiempo.isoformat(), "fiable": True}
    _fijar_reposicion(sim, decodificar_polyline(tramo["geometria"]), tramo["distancia_km"], tramo.get("fuente"), "telemetria")


# ---------- Última ubicación real del vehículo (se informa a Fleet) ----------

def _marcar_cierre_ubicacion(sim):
    """Define qué se le dirá a Fleet cuando el viaje termina sin llegar: dónde quedó el vehículo (interrupcion) o,
    si nunca se movió, que sigue donde estaba (restaurar)."""
    se_movio = (sim.km_reposicion or 0.0) + (sim.km_recorridos or 0.0) > 0
    sim.cierre_ubicacion = "interrupcion" if se_movio else "restaurar"
    sim.ubicacion_informada = False
    sim.intentos_ubicacion = 0


def _pais_probable(sim):
    """Un vehículo detenido a mitad de camino no tiene "ciudad": se toma el país del extremo más cercano de su ruta."""
    if sim.latitud is None or sim.longitud is None:
        return None
    mejor, pais = None, None
    for punto in (sim.origen_info, sim.destino_info):
        if punto and punto.get("latitud") is not None and punto.get("codigo_pais"):
            d = haversine_km(sim.latitud, sim.longitud, float(punto["latitud"]), float(punto["longitud"]))
            if mejor is None or d < mejor:
                mejor, pais = d, punto["codigo_pais"]
    return pais


def _cuerpo_ubicacion(sim):
    tipo = sim.cierre_ubicacion
    if tipo == "entrega":
        d = sim.destino_info or {}
        ultimo = (sim.puntos or [[None, None]])[-1]
        lat = d.get("latitud") if d.get("latitud") is not None else ultimo[0]
        lon = d.get("longitud") if d.get("longitud") is not None else ultimo[1]
        return {"latitud": lat, "longitud": lon, "codigo_pais": d.get("codigo_pais"), "ubicacion_id": d.get("ubicacion_id"),
                "nombre": d.get("nombre") or sim.destino, "tomada_en": (sim.llegada_en or _ahora()).isoformat(), "fuente": "entrega"}
    if tipo == "interrupcion":
        return {"latitud": sim.latitud, "longitud": sim.longitud, "codigo_pais": _pais_probable(sim),
                "tomada_en": (sim.ultima_actualizacion or _ahora()).isoformat(), "fuente": "interrupcion"}
    if tipo == "restaurar":
        d = sim.desde or {}
        if not d.get("fiable") or d.get("latitud") is None or not d.get("tomada_en"):
            return None   # no había una ubicación fiable que devolver
        return {"latitud": d["latitud"], "longitud": d["longitud"], "codigo_pais": d.get("codigo_pais"),
                "ubicacion_id": d.get("ubicacion_id"), "nombre": d.get("nombre"), "tomada_en": d["tomada_en"],
                "fuente": "interrupcion"}
    return None


async def _informar_ubicacion(db, sim) -> bool:
    """Guarda en Fleet dónde quedó el vehículo. True si ya quedó (o no hay nada que informar). No lanza errores:
    si Fleet no responde, se reintenta en el siguiente ciclo."""
    if sim.ubicacion_informada or not sim.cierre_ubicacion:
        return True
    cuerpo = _cuerpo_ubicacion(sim)
    if cuerpo is None or cuerpo.get("latitud") is None:
        sim.ubicacion_informada = True
        return True
    sim.intentos_ubicacion = (sim.intentos_ubicacion or 0) + 1
    try:
        await clientes_http.informar_ubicacion_vehiculo(sim.vehiculo_id, cuerpo)
    except clientes_http.DatosNoDisponibles as exc:
        sim.ultimo_error = f"UBICACION_PENDIENTE: {exc}"
        if sim.intentos_ubicacion >= MAX_INTENTOS_UBICACION:
            sim.ubicacion_informada = True   # se desiste; Fleet la dará por no fiable hasta que se registre una nueva
            print(f"[tracking] se desistió de informar la ubicación del vehículo {sim.vehiculo_id}: {exc}")
        return sim.ubicacion_informada
    sim.ubicacion_informada = True
    return True


async def _informar_ubicaciones_pendientes():
    db = SessionLocal()
    try:
        sims = (
            db.query(SimulacionViaje)
            .filter(SimulacionViaje.cierre_ubicacion.isnot(None), SimulacionViaje.ubicacion_informada.is_(False),
                    SimulacionViaje.estado.in_(("finalizada", "cancelada")))
            .limit(50)
            .all()
        )
        for sim in sims:
            try:
                await _informar_ubicacion(db, sim)
                db.commit()
            except Exception as exc:
                db.rollback()
                print(f"[tracking] error informando la ubicación del viaje {sim.envio_id}: {exc}")
    finally:
        db.close()


# ---------- Avance del vehículo ----------

async def _avanzar_en_vacio(db, sim, ahora, dt):
    """Tramo sin carga hacia el origen del envío. Se detiene EXACTAMENTE en el origen: desde ahí empieza el viaje con
    carga (y lo que Routing mide y Billing factura)."""
    horas_sim = dt * FACTOR_TIEMPO / 3600.0
    velocidad = sim.velocidad_promedio_kmh * random.uniform(0.85, 1.15)
    total = sim.reposicion_km
    km_inicial = sim.km_reposicion or 0.0
    consumo, tanque = sim.consumo_km_por_litro, sim.capacidad_tanque_l
    litros = sim.combustible_l
    minimo_l = tanque * NIVEL_CRITICO_PCT / 100.0

    objetivo = min(km_inicial + velocidad * horas_sim, total)
    km_critico = km_inicial + max(0.0, (litros - minimo_l) * consumo)
    emergencia = total > km_critico and objetivo > km_critico
    if emergencia:
        objetivo = km_critico
    litros -= max(0.0, objetivo - km_inicial) / consumo
    eventos = []
    detenido = False
    if emergencia:
        litros = max(litros, 0.0)
        cargados = tanque - litros
        sim.litros_recargados = (sim.litros_recargados or 0.0) + cargados
        sim.recargas = (sim.recargas or 0) + 1
        eventos.append(("recarga", f"Recarga de {cargados:.1f} L en una estación de servicio en ruta", objetivo))
        litros = tanque
        detenido = True

    llego = objetivo >= total - 1e-6
    km = total if llego else objetivo
    puntos = sim.puntos_reposicion
    lat, lon = posicion_en(puntos, acumulados(puntos), km / total)
    if llego:
        lat, lon = float(sim.puntos[0][0]), float(sim.puntos[0][1])
        sim.iniciado_en = ahora   # a partir de aquí el vehículo lleva la carga
        eventos.append(("llegada_origen", f"Llegó a {sim.origen}; empieza el viaje con la carga", km))
    sim.km_reposicion = km
    sim.combustible_l = max(0.0, litros)
    sim.latitud, sim.longitud = lat, lon
    if km > km_inicial:
        sim.horas_motor = (sim.horas_motor or 0.0) + (km - km_inicial) / max(velocidad, 1.0)
    if detenido:
        sim.en_parada_hasta = ahora + timedelta(seconds=PARADA_RECARGA_MIN * 60.0 / FACTOR_TIEMPO)
        sim.motivo_parada = "recarga"
    sim.velocidad_actual_kmh = 0.0 if (detenido or llego) else round(velocidad, 1)
    restante = max(0.0, total - km) + sim.distancia_total_km
    espera = (sim.en_parada_hasta - ahora).total_seconds() if sim.en_parada_hasta else 0.0
    sim.hora_estimada_llegada = ahora + timedelta(
        seconds=restante / sim.velocidad_promedio_kmh * 3600.0 / FACTOR_TIEMPO + max(0.0, espera))
    for tipo, descripcion, km_e in eventos:
        _evento(db, sim, tipo, descripcion, km=km_e)
    payload = _registrar_lectura(db, sim, ahora, sim.velocidad_actual_kmh or 0.0)
    db.commit()
    await _publicar_lectura(payload)


async def _avanzar(db, sim, ahora):
    dt = min((ahora - sim.ultima_actualizacion).total_seconds(), MAX_DT_REAL_S)
    sim.ultima_actualizacion = ahora
    if dt <= 0:
        db.commit()
        return

    if sim.en_parada_hasta is not None:
        if ahora < sim.en_parada_hasta:
            payload = _registrar_lectura(db, sim, ahora, 0.0)
            db.commit()
            await _publicar_lectura(payload)
            return
        sim.en_parada_hasta = None
        sim.motivo_parada = None
        _evento(db, sim, "reanuda_marcha", "Terminó la parada; el vehículo retoma la ruta")

    if sim.fase == "reposicion":
        await _avanzar_en_vacio(db, sim, ahora, dt)
        return

    horas_sim = dt * FACTOR_TIEMPO / 3600.0
    velocidad = sim.velocidad_promedio_kmh * random.uniform(0.85, 1.15)
    total = sim.distancia_total_km
    consumo = sim.consumo_km_por_litro
    tanque = sim.capacidad_tanque_l
    minimo_l = tanque * NIVEL_CRITICO_PCT / 100.0
    puntos = sim.puntos
    acum = acumulados(puntos)
    paradas = sim.paradas or []

    km_inicial = sim.km_recorridos
    km = km_inicial
    litros = sim.combustible_l
    objetivo = min(km + velocidad * horas_sim, total)
    eventos = []  # (tipo, descripcion, km, lat, lon, nivel_pct); lat/lon None = posición final

    # Tope por combustible: si no alcanza a llegar, se detiene cuando toque el nivel crítico
    km_critico = km + max(0.0, (litros - minimo_l) * consumo)
    tope_emergencia = False
    if total > km_critico and objetivo > km_critico:
        objetivo = km_critico
        tope_emergencia = True

    # Paradas alcanzadas en este avance
    indice = sim.proxima_parada or 0
    detenido = False
    while indice < len(paradas) and float(paradas[indice]["km_desde_origen"]) <= objetivo:
        parada = paradas[indice]
        pk = float(parada["km_desde_origen"])
        litros -= max(0.0, pk - km) / consumo
        km = max(km, pk)
        indice += 1
        siguiente_km = float(paradas[indice]["km_desde_origen"]) if indice < len(paradas) else total
        autonomia = litros * consumo
        reserva = tanque * RESERVA_PCT / 100.0 * consumo
        if autonomia < (siguiente_km - pk) + reserva:
            cargados = tanque - litros
            sim.litros_recargados = (sim.litros_recargados or 0.0) + cargados
            sim.recargas = (sim.recargas or 0) + 1
            eventos.append(("recarga", f"Recarga de {cargados:.1f} L en {parada.get('nombre')}", pk,
                            parada.get("latitud"), parada.get("longitud"), 100.0))
            litros = tanque
            detenido = True
            break
        eventos.append(("parada_alcanzada", f"Pasa por {parada.get('nombre')}", pk,
                        parada.get("latitud"), parada.get("longitud"), litros / tanque * 100.0))
    sim.proxima_parada = indice

    if not detenido:
        litros -= max(0.0, objetivo - km) / consumo
        km = max(km, objetivo)
        if tope_emergencia:
            litros = max(litros, 0.0)
            cargados = tanque - litros
            sim.litros_recargados = (sim.litros_recargados or 0.0) + cargados
            sim.recargas = (sim.recargas or 0) + 1
            eventos.append(("recarga", f"Recarga de {cargados:.1f} L en una estación de servicio en ruta",
                            km, None, None, 100.0))
            litros = tanque
            detenido = True

    litros = max(0.0, litros)
    llego = km >= total - 1e-6
    if llego:
        km = total
    lat, lon = posicion_en(puntos, acum, km / total)

    sim.km_recorridos = km
    sim.combustible_l = litros
    sim.latitud, sim.longitud = lat, lon
    if km > km_inicial:
        sim.horas_motor = (sim.horas_motor or 0.0) + (km - km_inicial) / max(velocidad, 1.0)

    if detenido:
        sim.en_parada_hasta = ahora + timedelta(seconds=PARADA_RECARGA_MIN * 60.0 / FACTOR_TIEMPO)
        sim.motivo_parada = "recarga"

    if llego:
        sim.estado = "finalizada"
        sim.cierre_ubicacion = "entrega"       # el vehículo quedó en el destino: se le dice a Fleet antes de publicar la llegada
        sim.ubicacion_informada = False
        sim.intentos_ubicacion = 0
        sim.llegada_en = ahora
        sim.hora_estimada_llegada = ahora
        sim.velocidad_actual_kmh = 0.0
        eventos.append(("llegada", f"Llegó a {sim.destino}", total, None, None, None))
    else:
        sim.velocidad_actual_kmh = 0.0 if detenido else round(velocidad, 1)
        restante = max(0.0, total - km)
        espera = (sim.en_parada_hasta - ahora).total_seconds() if sim.en_parada_hasta else 0.0
        sim.hora_estimada_llegada = ahora + timedelta(
            seconds=restante / sim.velocidad_promedio_kmh * 3600.0 / FACTOR_TIEMPO + max(0.0, espera)
        )

    for tipo, descripcion, km_e, lat_e, lon_e, nivel_e in eventos:
        _evento(db, sim, tipo, descripcion, km=km_e, lat=lat_e, lon=lon_e, nivel_pct=nivel_e)

    payload = _registrar_lectura(db, sim, ahora, sim.velocidad_actual_kmh or 0.0)
    db.commit()
    await _publicar_lectura(payload)


# ---------- Cargas compartidas: suben y bajan según el kilómetro del vehículo anfitrión ----------

def _hora_llegada_carga(host, fila, km_actual, ahora):
    restante = max(0.0, fila.km_entrega - km_actual)
    return ahora + timedelta(seconds=restante / max(host.velocidad_promedio_kmh or 60.0, 1.0) * 3600.0 / FACTOR_TIEMPO)


async def _procesar_cargas_compartidas():
    db = SessionLocal()
    try:
        filas = db.query(CargaCompartida).filter(CargaCompartida.estado.in_(("pendiente", "a_bordo"))).limit(200).all()
        ahora = _ahora()
        for fila in filas:
            host = db.query(SimulacionViaje).filter(SimulacionViaje.ruta_id == fila.ruta_anfitriona_id).first()
            if host is None or host.estado == "preparando":
                continue
            terminado_sin_llegar = host.estado == "cancelada" or (
                host.estado == "finalizada" and host.cierre_ubicacion in ("interrupcion", "restaurar"))
            if terminado_sin_llegar:
                # El vehículo no llegó a su destino. La carga que no había subido se reasigna (lo hace Routing);
                # la que ya iba a bordo queda marcada como interrumpida para que un operador la resuelva.
                if fila.estado == "pendiente":
                    fila.estado = "cancelada"
                    fila.reencolar_pendiente = True   # Routing la reasigna; el mismo criterio en ambos servicios
                else:
                    fila.estado = "interrumpida"
                continue
            if host.fase == "reposicion":
                continue
            km = host.distancia_total_km if host.estado == "finalizada" else (host.km_recorridos or 0.0)
            if fila.estado == "pendiente" and km >= fila.km_recogida - 1e-6:
                fila.estado = "a_bordo"
                fila.iniciado_en = ahora
                fila.hora_estimada_llegada = _hora_llegada_carga(host, fila, km, ahora)
            if fila.estado == "a_bordo" and km >= fila.km_entrega - 1e-6:
                fila.estado = "entregada"
                fila.llegada_en = ahora
        db.commit()
    finally:
        db.close()


async def _publicar_cargas_compartidas():
    db = SessionLocal()
    try:
        filas = (
            db.query(CargaCompartida)
            .filter(or_(
                and_(CargaCompartida.inicio_publicado.is_(False), CargaCompartida.estado.in_(("a_bordo", "entregada"))),
                and_(CargaCompartida.estado == "entregada", CargaCompartida.llegada_publicada.is_(False)),
                CargaCompartida.reencolar_pendiente.is_(True),
            ))
            .limit(50)
            .all()
        )
        for fila in filas:
            base = {
                "envio_id": str(fila.envio_id),
                "ruta_id": str(fila.ruta_id),
                "ruta_anfitriona_id": str(fila.ruta_anfitriona_id),
                "modo": "compartida",
                "vehiculo_id": str(fila.vehiculo_id),
                "conductor_id": str(fila.conductor_id) if fila.conductor_id else None,
                "placa": fila.placa,
                "distancia_km": round(fila.km_entrega - fila.km_recogida, 2),
            }
            try:
                if fila.reencolar_pendiente:
                    await publish_event("tracking.shared_load_missed", {
                        "envio_id": str(fila.envio_id), "ruta_id": str(fila.ruta_id),
                        "ruta_anfitriona_id": str(fila.ruta_anfitriona_id), "vehiculo_id": str(fila.vehiculo_id),
                        "motivo": "El vehículo anfitrión ya pasó por el punto de recogida o terminó antes de recoger la carga"})
                    fila.reencolar_pendiente = False
                    db.commit()
                    continue
                if not fila.inicio_publicado:
                    await publish_event("tracking.trip_started", {
                        **base, "iniciado_en": fila.iniciado_en, "hora_estimada_llegada": fila.hora_estimada_llegada})
                    fila.inicio_publicado = True
                    db.commit()
                if fila.estado == "entregada" and not fila.llegada_publicada:
                    await publish_event("tracking.trip_arrived", {
                        **base, "llegada_en": fila.llegada_en, "litros_recargados": 0.0, "recargas": 0})
                    fila.llegada_publicada = True
                    db.commit()
            except Exception as exc:
                db.rollback()
                print(f"[tracking] no se pudo publicar el evento de la carga compartida {fila.envio_id} (se reintentará): {exc}")
    finally:
        db.close()


# ---------- Ciclo programado ----------

async def _publicar_pendientes():
    """Publica tracking.trip_started / tracking.trip_arrived; si RabbitMQ falla, se reintenta en el siguiente ciclo."""
    db = SessionLocal()
    try:
        sims = (
            db.query(SimulacionViaje)
            .filter(or_(
                and_(SimulacionViaje.inicio_publicado.is_(False),
                     SimulacionViaje.estado.in_(("en_curso", "pausada", "finalizada")),
                     SimulacionViaje.km_reposicion >= SimulacionViaje.reposicion_km),   # sale CON carga, no en vacío
                and_(SimulacionViaje.estado == "finalizada", SimulacionViaje.llegada_publicada.is_(False)),
            ))
            .limit(50)
            .all()
        )
        for sim in sims:
            base = {
                "envio_id": str(sim.envio_id),
                "ruta_id": str(sim.ruta_id),
                "vehiculo_id": str(sim.vehiculo_id),
                "conductor_id": str(sim.conductor_id) if sim.conductor_id else None,
                "placa": sim.placa,
                "distancia_km": sim.distancia_total_km,
            }
            try:
                if not sim.inicio_publicado:
                    await publish_event("tracking.trip_started", {
                        **base,
                        "iniciado_en": sim.iniciado_en,
                        "hora_estimada_llegada": sim.hora_estimada_llegada,
                    })
                    sim.inicio_publicado = True
                    db.commit()
                if sim.estado == "finalizada" and not sim.llegada_publicada:
                    # Primero se deja la ubicación real en Fleet: así, cuando Shipment confirme la entrega y Routing
                    # libere el vehículo, ya figura dónde está. Si Fleet no responde se reintenta unos ciclos.
                    if not await _informar_ubicacion(db, sim):
                        if (sim.intentos_ubicacion or 0) < MAX_INTENTOS_UBICACION_ANTES_DE_LLEGAR:
                            db.commit()
                            continue
                    await publish_event("tracking.trip_arrived", {
                        **base,
                        "llegada_en": sim.llegada_en,
                        "litros_recargados": round(sim.litros_recargados or 0.0, 1),
                        "recargas": sim.recargas or 0,
                    })
                    sim.llegada_publicada = True
                    db.commit()
            except Exception as exc:
                db.rollback()
                print(f"[tracking] no se pudo publicar el evento del viaje {sim.envio_id} (se reintentará): {exc}")
    finally:
        db.close()


async def _preparar_pendientes():
    db = SessionLocal()
    try:
        ids = [r[0] for r in db.query(SimulacionViaje.id).filter(SimulacionViaje.estado == "preparando").limit(50).all()]
        for sim_id in ids:
            sim = (
                db.query(SimulacionViaje)
                .filter(SimulacionViaje.id == sim_id)
                .with_for_update(skip_locked=True)
                .first()
            )
            if sim is None or sim.estado != "preparando":
                continue
            try:
                await _preparar(db, sim)
            except Exception as exc:
                db.rollback()
                print(f"[tracking] error preparando el viaje {sim_id}: {exc}")
    finally:
        db.close()


async def _avanzar_en_curso():
    db = SessionLocal()
    try:
        ids = [r[0] for r in db.query(SimulacionViaje.id).filter(SimulacionViaje.estado == "en_curso").all()]
        ahora = _ahora()
        for sim_id in ids:
            sim = (
                db.query(SimulacionViaje)
                .filter(SimulacionViaje.id == sim_id)
                .with_for_update(skip_locked=True)
                .first()
            )
            if sim is None or sim.estado != "en_curso":
                continue
            try:
                await _avanzar(db, sim, ahora)
            except Exception as exc:
                db.rollback()
                print(f"[tracking] error avanzando el viaje {sim_id}: {exc}")
    finally:
        db.close()


async def tick():
    try:
        await _publicar_pendientes()
        await _publicar_cargas_compartidas()
        await _informar_ubicaciones_pendientes()
        await _preparar_pendientes()
        await _avanzar_en_curso()
        await _procesar_cargas_compartidas()
    except Exception as exc:
        print(f"[tracking] error en el ciclo de simulación: {exc}")


# ---------- Eventos de otros servicios ----------

ESPERA_SIMULACION_S = float(os.getenv("ESPERA_SIMULACION_S", "0.5"))
INTENTOS_SIMULACION = int(os.getenv("INTENTOS_SIMULACION", "120"))


async def _simulaciones_activas(db, envio_id):
    """Simulaciones activas del envío, bloqueadas. NUNCA espera un candado de forma síncrona: el ciclo de simulación
    puede estar sosteniendo esa fila mientras espera una llamada HTTP en este mismo bucle de eventos, y una espera
    bloqueante aquí lo congelaría todo. Si alguna está ocupada se suelta lo tomado y se reintenta sin bloquear."""
    filtro = (SimulacionViaje.envio_id == envio_id, SimulacionViaje.estado.in_(ESTADOS_ACTIVOS))
    for _ in range(INTENTOS_SIMULACION):
        todas = db.query(SimulacionViaje.id).filter(*filtro).count()
        tomadas = db.query(SimulacionViaje).filter(*filtro).with_for_update(skip_locked=True).all()
        if len(tomadas) == todas:
            return tomadas
        db.rollback()   # suelta lo que sí se pudo tomar y espera a que el ciclo termine con la otra
        await asyncio.sleep(ESPERA_SIMULACION_S)
    raise RuntimeError(f"las simulaciones del envío {envio_id} siguen ocupadas; no se aplicó el evento")


def _cerrar_cargas_del_envio(db, envio_id, estado: str, publicado: bool = False):
    for fila in db.query(CargaCompartida).filter(
            CargaCompartida.envio_id == envio_id, CargaCompartida.estado.in_(("pendiente", "a_bordo"))).all():
        fila.estado = estado
        if publicado:
            fila.inicio_publicado = fila.llegada_publicada = True
            fila.llegada_en = _ahora()


async def _interrumpir(payload: dict, descripcion: str):
    db = SessionLocal()
    try:
        envio_id = uuid.UUID(payload["envio_id"])
        for sim in await _simulaciones_activas(db, envio_id):
            sim.estado = "cancelada"
            sim.velocidad_actual_kmh = 0.0
            _marcar_cierre_ubicacion(sim)   # dónde quedó realmente: NO se registra una llegada
            _evento(db, sim, "cancelada", descripcion)
        _cerrar_cargas_del_envio(db, envio_id, "cancelada")
        db.commit()
    finally:
        db.close()


async def procesar_envio_devuelto(payload: dict):
    await _interrumpir(payload, "El envío fue devuelto; el viaje se detiene")


async def procesar_envio_cancelado(payload: dict):
    await _interrumpir(payload, "El envío fue cancelado; el viaje se detiene")


async def procesar_entrega_manual(payload: dict):
    """Un operador registró la entrega a mano: el viaje termina sin publicar una llegada propia."""
    db = SessionLocal()
    try:
        envio_id = uuid.UUID(payload["envio_id"])
        for sim in await _simulaciones_activas(db, envio_id):
            sim.estado = "finalizada"
            sim.llegada_en = _ahora()
            sim.velocidad_actual_kmh = 0.0
            sim.inicio_publicado = True
            sim.llegada_publicada = True
            _marcar_cierre_ubicacion(sim)   # el vehículo queda donde la simulación lo tenía, sin teletransportarlo
            _evento(db, sim, "entrega_manual", "El envío se entregó manualmente; el viaje se da por terminado")
        _cerrar_cargas_del_envio(db, envio_id, "entregada", publicado=True)
        db.commit()
    finally:
        db.close()


async def procesar_aduana_retiene(payload: dict):
    db = SessionLocal()
    try:
        for sim in await _simulaciones_activas(db, uuid.UUID(payload["envio_id"])):
            if sim.estado == "en_curso":
                sim.estado = "pausada"
                sim.velocidad_actual_kmh = 0.0
                _evento(db, sim, "pausa_aduana", payload.get("motivo_retencion") or "Retenido en aduana")
        db.commit()
    finally:
        db.close()


async def procesar_aduana_libera(payload: dict):
    db = SessionLocal()
    try:
        for sim in await _simulaciones_activas(db, uuid.UUID(payload["envio_id"])):
            if sim.estado == "pausada":
                sim.estado = "en_curso"
                sim.ultima_actualizacion = _ahora()  # el tiempo detenido no cuenta como avance
                _evento(db, sim, "reanudacion_aduana", "Aduana liberó el envío; el viaje continúa")
        db.commit()
    finally:
        db.close()