"""Escenarios A/C (tramo en vacío) y B (carga compartida) en Tracking."""
import uuid

from app import simulador
from app.models import CargaCompartida, LecturaTelemetria, SimulacionViaje
from escenarios import BOGOTA, CALI, GIRARDOT, MEDELLIN, MONTERIA, Viaje, correr, hasta, paso, reposicion_desde


def sim_de(db, v):
    db.expire_all()
    return db.query(SimulacionViaje).filter(SimulacionViaje.envio_id == v.envio_id).one()


def eventos(bus, nombre=None):
    return [e for e in bus if e[0] == "evento" and (nombre is None or e[1] == nombre)]


# ---------------- Tramo en vacío ----------------

def test_el_vehiculo_sale_de_donde_estaba_y_no_del_origen(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar(reposicion=reposicion_desde(GIRARDOT, BOGOTA))
    paso(reloj, 1)
    s = sim_de(db, v)
    assert s.estado == "en_curso" and s.fase == "reposicion" and s.reposicion_km > 100
    assert abs(s.latitud - GIRARDOT[0]) < 0.2 and abs(s.longitud - GIRARDOT[1]) < 0.3     # cerca de Girardot, no de Bogotá


def test_el_viaje_con_carga_no_se_anuncia_hasta_llegar_al_origen(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar(reposicion=reposicion_desde(GIRARDOT, BOGOTA))
    paso(reloj, 2, 20)
    assert sim_de(db, v).fase == "reposicion" and eventos(bus, "tracking.trip_started") == []
    assert hasta(reloj, lambda: sim_de(db, v).fase == "viaje", segundos=20)
    paso(reloj, 2)
    assert len(eventos(bus, "tracking.trip_started")) == 1


def test_el_vehiculo_para_exactamente_en_el_origen_y_la_telemetria_cambia_de_fase(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar(reposicion=reposicion_desde(GIRARDOT, BOGOTA))
    hasta(reloj, lambda: sim_de(db, v).fase == "viaje")
    s = sim_de(db, v)
    assert s.km_reposicion == s.reposicion_km and s.km_recorridos == 0.0
    assert (round(s.latitud, 4), round(s.longitud, 4)) == (round(BOGOTA[0], 4), round(BOGOTA[1], 4))
    lecturas = [e[2] for e in bus if e[0] == "evento" and e[1] == "telemetry.raw"]
    fases = [l["fase"] for l in lecturas]
    assert fases[0] == "reposicion" and fases[-1] == "viaje" and "viaje" not in fases[:fases.index("viaje")]
    assert all(l["ruta_id"] == str(v.ruta_id) for l in lecturas)
    # el odómetro es continuo: la primera lectura de "viaje" queda exactamente reposicion_km después del arranque
    assert lecturas[-1]["kilometraje_acumulado_km"] - lecturas[0]["kilometraje_acumulado_km"] == round(s.reposicion_km, 2)


def test_el_progreso_que_ve_el_cliente_cuenta_solo_el_tramo_con_carga(db, reloj, bus, vecinos):
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.main import app
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar(reposicion=reposicion_desde(GIRARDOT, BOGOTA))
    paso(reloj, 2, 20)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get(f"/seguimiento/envio/{v.envio_id}").json()
    finally:
        app.dependency_overrides.clear()
    assert r["fase"] == "reposicion" and r["progreso"]["porcentaje"] == 0.0
    assert r["reposicion"]["km_totales"] > 100 and r["reposicion"]["km_recorridos"] > 0
    assert r["reposicion"]["salio_de"]["latitud"] == GIRARDOT[0] and "vacío" in r["mensaje"]


def test_si_ya_estaba_en_el_origen_arranca_directo(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    rep = reposicion_desde(BOGOTA, BOGOTA)
    rep.update({"distancia_km": 0.0, "geometria": None, "fuente": "mismo_lugar"})
    v.asignar(reposicion=rep)
    paso(reloj, 2)
    s = sim_de(db, v)
    assert s.fase == "viaje" and s.reposicion_km == 0 and len(eventos(bus, "tracking.trip_started")) == 1


def test_sin_ubicacion_en_fleet_usa_la_ultima_lectura_real_del_vehiculo(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    db.add(LecturaTelemetria(id=1, tiempo=reloj() , vehiculo_id=v.vehiculo_id, latitud=GIRARDOT[0], longitud=GIRARDOT[1],
                             kilometraje_acumulado_km=500))
    db.commit()
    vecinos.trayecto_respuesta = {"distancia_km": 140.0, "duracion_horas": 2.3, "fuente": "osrm",
                                  "geometria": reposicion_desde(GIRARDOT, BOGOTA)["geometria"]}
    v.asignar(reposicion={"desde": None, "distancia_km": None, "motivo": "UBICACION_DESCONOCIDA"})
    paso(reloj, 1)
    s = sim_de(db, v)
    assert len(vecinos.trayectos) == 1 and s.reposicion_km == 140.0 and s.fase == "reposicion"
    assert s.reposicion_datos["resuelta"] == "telemetria"


def test_sin_ubicacion_ni_lecturas_no_se_inventa_un_tramo(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar(reposicion={"desde": None, "distancia_km": None, "motivo": "UBICACION_DESCONOCIDA"})
    paso(reloj, 1)
    s = sim_de(db, v)
    assert s.reposicion_km == 0 and s.reposicion_datos["resuelta"] == "sin_ubicacion_conocida" and vecinos.trayectos == []
    assert s.fase == "viaje"


def test_si_routing_no_responde_al_pedir_el_tramo_se_reintenta_sin_arrancar(db, reloj, bus, vecinos):
    from app import clientes_http
    v = Viaje(vecinos, BOGOTA, CALI)
    db.add(LecturaTelemetria(id=1, tiempo=reloj(), vehiculo_id=v.vehiculo_id, latitud=GIRARDOT[0], longitud=GIRARDOT[1]))
    db.commit()
    vecinos.trayecto_respuesta = clientes_http.DatosNoDisponibles("Routing caído")
    v.asignar(reposicion={"desde": None, "distancia_km": None, "motivo": "UBICACION_DESCONOCIDA"})
    paso(reloj, 2)
    assert sim_de(db, v).estado == "preparando"
    vecinos.trayecto_respuesta = {"distancia_km": 140.0, "duracion_horas": 2.3, "fuente": "estimada",
                                  "geometria": reposicion_desde(GIRARDOT, BOGOTA)["geometria"]}
    paso(reloj, 2)
    assert sim_de(db, v).estado == "en_curso"


def test_dos_viajes_seguidos_no_teletransportan_al_vehiculo(db, reloj, bus, vecinos):
    """Escenario A completo: viaja de Bogotá a Cali, termina; el siguiente envío sale de Bogotá y el vehículo va a Cali -> Bogotá."""
    veh = uuid.uuid4()
    a = Viaje(vecinos, BOGOTA, CALI, vehiculo=veh)
    a.asignar()
    assert hasta(reloj, lambda: sim_de(db, a).estado == "finalizada")
    paso(reloj, 3)
    donde_quedo = vecinos.ubicaciones[-1][1]
    assert (donde_quedo["latitud"], donde_quedo["longitud"]) == CALI
    b = Viaje(vecinos, BOGOTA, MEDELLIN, vehiculo=veh)
    b.asignar(reposicion=reposicion_desde((donde_quedo["latitud"], donde_quedo["longitud"]), BOGOTA))
    paso(reloj, 1)
    s = sim_de(db, b)
    assert s.fase == "reposicion" and abs(s.latitud - CALI[0]) < 0.6      # arranca en Cali, no en Bogotá
    lecturas = db.query(LecturaTelemetria).filter(LecturaTelemetria.vehiculo_id == veh).order_by(LecturaTelemetria.tiempo).all()
    # el odómetro nunca retrocede entre un viaje y el siguiente
    odometros = [float(l.kilometraje_acumulado_km) for l in lecturas]
    assert odometros == sorted(odometros)


# ---------------- Carga compartida ----------------

def anfitrion(db, reloj, bus, vecinos, paradas=None):
    v = Viaje(vecinos, MONTERIA, CALI, km=800.0, paradas=paradas)
    v.asignar()
    return v


def invitada(vecinos, host, km_recogida, km_entrega, **extra):
    envio, ruta = uuid.uuid4(), uuid.uuid4()
    payload = {
        "ruta_id": str(ruta), "envio_id": str(envio), "vehiculo_id": str(host.vehiculo_id), "conductor_id": str(uuid.uuid4()),
        "placa": "ABC123", "conductor_nombre": "Pedro", "codigo_ruta": "RUT-2", "modo": "compartida",
        "ruta_anfitriona_id": str(host.ruta_id), "km_recogida": km_recogida, "km_entrega": km_entrega,
        "origen": {"nombre": "Medellín"}, "destino": {"nombre": "Cali"}, **extra,
    }
    correr(simulador.procesar_route_assigned(payload))
    return envio, ruta


def carga(db, envio):
    db.expire_all()
    return db.query(CargaCompartida).filter(CargaCompartida.envio_id == envio).one()


def test_la_carga_compartida_no_crea_una_segunda_simulacion(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)
    envio, _ = invitada(vecinos, h, 100, 400)
    assert db.query(SimulacionViaje).count() == 1
    assert carga(db, envio).estado == "pendiente"


def test_la_carga_sube_en_su_kilometro_y_baja_en_el_suyo(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)
    envio, ruta = invitada(vecinos, h, 100, 300)
    assert carga(db, envio).estado == "pendiente" and eventos(bus, "tracking.trip_started") == []
    assert hasta(reloj, lambda: carga(db, envio).estado == "a_bordo")
    paso(reloj, 1)
    inicio = [e[2] for e in eventos(bus, "tracking.trip_started") if e[2]["envio_id"] == str(envio)]
    assert len(inicio) == 1 and inicio[0]["ruta_id"] == str(ruta) and inicio[0]["modo"] == "compartida"
    assert sim_de(db, h).km_recorridos >= 100 and sim_de(db, h).estado == "en_curso"
    assert hasta(reloj, lambda: carga(db, envio).estado == "entregada")
    paso(reloj, 1)
    llegada = [e[2] for e in eventos(bus, "tracking.trip_arrived") if e[2]["envio_id"] == str(envio)]
    assert len(llegada) == 1
    assert sim_de(db, h).km_recorridos >= 300 and sim_de(db, h).estado == "en_curso"      # el anfitrión sigue su camino
    # la llegada de la carga se anuncia ANTES que la del anfitrión
    assert not [e for e in eventos(bus, "tracking.trip_arrived") if e[2]["envio_id"] == str(h.envio_id)]


def test_la_carga_que_baja_en_el_destino_final_llega_junto_con_el_anfitrion(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)
    envio, _ = invitada(vecinos, h, 400, 800)
    assert hasta(reloj, lambda: sim_de(db, h).estado == "finalizada")
    paso(reloj, 4)
    assert carga(db, envio).estado == "entregada"
    ids = {e[2]["envio_id"] for e in eventos(bus, "tracking.trip_arrived")}
    assert ids == {str(h.envio_id), str(envio)}


def test_no_se_anuncia_nada_dos_veces(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)
    envio, _ = invitada(vecinos, h, 100, 300)
    hasta(reloj, lambda: carga(db, envio).estado == "entregada")
    paso(reloj, 8)
    mios = [e for e in eventos(bus) if e[2].get("envio_id") == str(envio) and e[1].startswith("tracking.")]
    assert sorted(e[1] for e in mios) == ["tracking.trip_arrived", "tracking.trip_started"]


def test_un_mensaje_repetido_de_asignacion_no_duplica_la_carga(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)
    envio, ruta = invitada(vecinos, h, 100, 300)
    correr(simulador.procesar_route_assigned({
        "ruta_id": str(ruta), "envio_id": str(envio), "vehiculo_id": str(h.vehiculo_id), "modo": "compartida",
        "ruta_anfitriona_id": str(h.ruta_id), "km_recogida": 100, "km_entrega": 300}))
    assert db.query(CargaCompartida).count() == 1


def test_anfitrion_cancelado_cancela_la_pendiente_e_interrumpe_la_que_va_a_bordo(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)
    a_bordo, _ = invitada(vecinos, h, 50, 600)
    pendiente, _ = invitada(vecinos, h, 700, 790)
    hasta(reloj, lambda: carga(db, a_bordo).estado == "a_bordo")
    correr(simulador.procesar_envio_devuelto({"envio_id": str(h.envio_id)}))
    paso(reloj, 3)
    assert carga(db, a_bordo).estado == "interrumpida" and carga(db, pendiente).estado == "cancelada"
    assert not [e for e in eventos(bus, "tracking.trip_arrived") if e[2]["envio_id"] in (str(a_bordo), str(pendiente))]


def test_la_carga_devuelta_se_cancela_sin_tocar_al_anfitrion(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)
    envio, _ = invitada(vecinos, h, 100, 300)
    correr(simulador.procesar_envio_devuelto({"envio_id": str(envio)}))
    assert carga(db, envio).estado == "cancelada" and sim_de(db, h).estado in ("preparando", "en_curso")


def test_una_carga_reasignada_a_otro_anfitrion_se_rearma(db, reloj, bus, vecinos):
    h1 = anfitrion(db, reloj, bus, vecinos)
    envio, ruta = invitada(vecinos, h1, 100, 300)
    correr(simulador.procesar_envio_devuelto({"envio_id": str(h1.envio_id)}))
    paso(reloj, 2)
    assert carga(db, envio).estado == "cancelada"
    h2 = Viaje(vecinos, MONTERIA, CALI, km=800.0)
    h2.asignar()
    correr(simulador.procesar_route_assigned({
        "ruta_id": str(ruta), "envio_id": str(envio), "vehiculo_id": str(h2.vehiculo_id), "modo": "compartida",
        "ruta_anfitriona_id": str(h2.ruta_id), "km_recogida": 120, "km_entrega": 320}))
    c = carga(db, envio)
    assert c.estado == "pendiente" and c.ruta_anfitriona_id == h2.ruta_id and c.km_recogida == 120


def test_la_carga_espera_mientras_el_anfitrion_va_en_vacio(db, reloj, bus, vecinos):
    h = Viaje(vecinos, MONTERIA, CALI, km=800.0)
    h.asignar(reposicion=reposicion_desde(MEDELLIN, MONTERIA))
    envio, _ = invitada(vecinos, h, 0, 300)
    paso(reloj, 2)
    assert sim_de(db, h).fase == "reposicion" and carga(db, envio).estado == "pendiente"
    assert hasta(reloj, lambda: carga(db, envio).estado in ("a_bordo", "entregada"))
    assert sim_de(db, h).fase == "viaje"


def test_el_seguimiento_de_la_carga_muestra_su_tramo_y_la_posicion_del_vehiculo(db, reloj, bus, vecinos):
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.main import app
    h = anfitrion(db, reloj, bus, vecinos)
    envio, _ = invitada(vecinos, h, 100, 300)
    hasta(reloj, lambda: sim_de(db, h).km_recorridos > 200, segundos=15)
    app.dependency_overrides[get_db] = lambda: db
    try:
        c = TestClient(app)
        r = c.get(f"/seguimiento/envio/{envio}").json()
        trazado = c.get(f"/seguimiento/envio/{envio}/trazado")
    finally:
        app.dependency_overrides.clear()
    assert r["envio_id"] == str(envio) and r["estado_viaje"] == "en_curso"
    assert r["carga_compartida"]["estado"] == "a_bordo" and r["progreso"]["km_totales"] == 200.0
    assert 40 < r["progreso"]["km_recorridos"] < 140 and r["posicion"]["latitud"] is not None
    assert trazado.status_code == 200 and trazado.json()["envio_id"] == str(envio)


def sim_de(db, v):  # noqa: F811  (definición final usada por las pruebas de arriba)
    db.expire_all()
    return db.query(SimulacionViaje).filter(SimulacionViaje.envio_id == v.envio_id).one()


# ---------------- Correcciones de la revisión: carga que ya no puede subir ----------------

def test_si_el_vehiculo_ya_paso_la_recogida_la_carga_no_se_da_por_recogida_y_se_avisa_a_routing(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)
    hasta(reloj, lambda: sim_de(db, h).km_recorridos > 150)
    envio, ruta = invitada(vecinos, h, 100, 400)            # Routing decidió con un avance atrasado: el km 100 ya quedó atrás
    c = carga(db, envio)
    assert c.estado == "cancelada" and c.reencolar_pendiente is True
    paso(reloj, 3)
    perdidos = [e[2] for e in eventos(bus, "tracking.shared_load_missed")]
    assert len(perdidos) == 1 and perdidos[0]["ruta_id"] == str(ruta) and perdidos[0]["envio_id"] == str(envio)
    assert eventos(bus, "tracking.trip_started") == [] or all(
        e[2]["envio_id"] != str(envio) for e in eventos(bus, "tracking.trip_started"))   # nunca "subió"
    assert carga(db, envio).reencolar_pendiente is False and carga(db, envio).estado == "cancelada"
    paso(reloj, 6)
    assert len(eventos(bus, "tracking.shared_load_missed")) == 1                           # se avisa una sola vez


def test_si_el_vehiculo_aun_no_llega_a_la_recogida_la_carga_queda_pendiente(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)
    hasta(reloj, lambda: sim_de(db, h).km_recorridos > 20)
    envio, _ = invitada(vecinos, h, 400, 700)
    assert carga(db, envio).estado == "pendiente" and carga(db, envio).reencolar_pendiente is False
    paso(reloj, 2)
    assert eventos(bus, "tracking.shared_load_missed") == []


def test_una_carga_en_la_recogida_inicial_con_el_anfitrion_aun_preparandose_si_se_acepta(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)                  # recién asignado: todavía no sale
    envio, _ = invitada(vecinos, h, 0, 300)
    assert carga(db, envio).estado == "pendiente" and carga(db, envio).reencolar_pendiente is False


def test_el_anfitrion_entregado_a_mano_antes_de_la_recogida_avisa_a_routing_con_el_mismo_criterio(db, reloj, bus, vecinos):
    h = anfitrion(db, reloj, bus, vecinos)
    pendiente, ruta = invitada(vecinos, h, 700, 790)
    a_bordo, _ = invitada(vecinos, h, 10, 600)
    hasta(reloj, lambda: carga(db, a_bordo).estado == "a_bordo")
    correr(simulador.procesar_entrega_manual({"envio_id": str(h.envio_id)}))
    paso(reloj, 4)
    assert carga(db, pendiente).estado == "cancelada"
    assert [e[2]["ruta_id"] for e in eventos(bus, "tracking.shared_load_missed")] == [str(ruta)]   # solo la que no subió
    assert carga(db, a_bordo).estado in ("interrumpida", "entregada")


def test_una_carga_rearmada_en_otro_anfitrion_borra_el_aviso_pendiente(db, reloj, bus, vecinos):
    h1 = anfitrion(db, reloj, bus, vecinos)
    hasta(reloj, lambda: sim_de(db, h1).km_recorridos > 150)
    envio, ruta = invitada(vecinos, h1, 100, 300)
    assert carga(db, envio).reencolar_pendiente is True
    h2 = Viaje(vecinos, MONTERIA, CALI, km=800.0)
    h2.asignar()
    correr(simulador.procesar_route_assigned({
        "ruta_id": str(ruta), "envio_id": str(envio), "vehiculo_id": str(h2.vehiculo_id), "modo": "compartida",
        "ruta_anfitriona_id": str(h2.ruta_id), "km_recogida": 120, "km_entrega": 320}))
    c = carga(db, envio)
    assert c.estado == "pendiente" and c.reencolar_pendiente is False


def test_tracking_espera_mas_que_los_reintentos_de_osrm_de_routing_al_pedir_el_tramo(monkeypatch):
    import httpx
    from app import clientes_http
    visto = {}

    class Falso:
        def __init__(self, timeout=None, **kw):
            visto["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", url))

    monkeypatch.setattr(clientes_http.httpx, "AsyncClient", Falso)
    monkeypatch.setattr(clientes_http, "ROUTING_SERVICE_URL", "http://routing")
    correr(clientes_http.obtener_trayecto(1, 2, 3, 4))
    assert visto["timeout"] > 25            # Routing puede tardar ~21 s en devolver su respaldo "estimado"
