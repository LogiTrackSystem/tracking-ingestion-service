import os
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, desc, or_
from sqlalchemy.exc import IntegrityError

from . import clientes_http
from .database import SessionLocal
from .events import publish_event
from .geo import acumulados, posicion_en
from .models import LecturaTelemetria, SimulacionEvento, SimulacionViaje

TICK_SEGUNDOS = int(os.getenv("SIM_TICK_SEGUNDOS", "5"))
FACTOR_TIEMPO = float(os.getenv("SIM_FACTOR_TIEMPO", "120"))
COMBUSTIBLE_INICIAL_PCT = float(os.getenv("SIM_COMBUSTIBLE_INICIAL_PCT", "80"))

RESERVA_PCT = 15.0          # reserva de combustible que se busca conservar al salir de una parada
NIVEL_CRITICO_PCT = 8.0     # por debajo de esto sin parada a la vista, recarga de emergencia
PARADA_RECARGA_MIN = 20     # minutos simulados que dura una recarga
MAX_DT_REAL_S = 60          # tope de segundos por avance (evita saltos tras una pausa del servicio)
ESTADOS_ACTIVOS = ("preparando", "en_curso", "pausada")

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
    odometro = round((sim.odometro_inicio_km or 0.0) + (sim.km_recorridos or 0.0), 2)
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

async def procesar_route_assigned(payload: dict):
    ruta_id = uuid.UUID(payload["ruta_id"])
    db = SessionLocal()
    try:
        if db.query(SimulacionViaje).filter(SimulacionViaje.ruta_id == ruta_id).first():
            return  # mensaje repetido
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
        if envio.get("estado") in ("entregado", "devuelto"):
            sim.estado = "cancelada"
            sim.ultimo_error = f"El envío ya está {envio['estado']}"
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
    sim.latitud, sim.longitud = float(sim.puntos[0][0]), float(sim.puntos[0][1])
    sim.velocidad_actual_kmh = 0.0
    sim.iniciado_en = ahora
    sim.ultima_actualizacion = ahora
    sim.hora_estimada_llegada = ahora + timedelta(seconds=sim.distancia_total_km / sim.velocidad_promedio_kmh * 3600.0 / FACTOR_TIEMPO)
    sim.estado = "en_curso"
    sim.ultimo_error = None
    _evento(db, sim, "inicio", f"Sale de {sim.origen} hacia {sim.destino} ({sim.distancia_total_km:.0f} km)")
    payload = _registrar_lectura(db, sim, ahora, 0.0)
    db.commit()
    await _publicar_lectura(payload)


# ---------- Avance del vehículo ----------

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


# ---------- Ciclo programado ----------

async def _publicar_pendientes():
    """Publica tracking.trip_started / tracking.trip_arrived; si RabbitMQ falla, se reintenta en el siguiente ciclo."""
    db = SessionLocal()
    try:
        sims = (
            db.query(SimulacionViaje)
            .filter(or_(
                and_(SimulacionViaje.inicio_publicado.is_(False),
                     SimulacionViaje.estado.in_(("en_curso", "pausada", "finalizada"))),
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
        await _preparar_pendientes()
        await _avanzar_en_curso()
    except Exception as exc:
        print(f"[tracking] error en el ciclo de simulación: {exc}")


# ---------- Eventos de otros servicios ----------

def _simulaciones_activas(db, envio_id):
    return (
        db.query(SimulacionViaje)
        .filter(SimulacionViaje.envio_id == envio_id, SimulacionViaje.estado.in_(ESTADOS_ACTIVOS))
        .with_for_update()
        .all()
    )


async def procesar_envio_devuelto(payload: dict):
    db = SessionLocal()
    try:
        for sim in _simulaciones_activas(db, uuid.UUID(payload["envio_id"])):
            sim.estado = "cancelada"
            sim.velocidad_actual_kmh = 0.0
            _evento(db, sim, "cancelada", "El envío fue devuelto; el viaje se detiene")
        db.commit()
    finally:
        db.close()


async def procesar_entrega_manual(payload: dict):
    """Un operador registró la entrega a mano: el viaje termina sin publicar una llegada propia."""
    db = SessionLocal()
    try:
        for sim in _simulaciones_activas(db, uuid.UUID(payload["envio_id"])):
            sim.estado = "finalizada"
            sim.llegada_en = _ahora()
            sim.velocidad_actual_kmh = 0.0
            sim.inicio_publicado = True
            sim.llegada_publicada = True
            _evento(db, sim, "entrega_manual", "El envío se entregó manualmente; el viaje se da por terminado")
        db.commit()
    finally:
        db.close()


async def procesar_aduana_retiene(payload: dict):
    db = SessionLocal()
    try:
        for sim in _simulaciones_activas(db, uuid.UUID(payload["envio_id"])):
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
        for sim in _simulaciones_activas(db, uuid.UUID(payload["envio_id"])):
            if sim.estado == "pausada":
                sim.estado = "en_curso"
                sim.ultima_actualizacion = _ahora()  # el tiempo detenido no cuenta como avance
                _evento(db, sim, "reanudacion_aduana", "Aduana liberó el envío; el viaje continúa")
        db.commit()
    finally:
        db.close()