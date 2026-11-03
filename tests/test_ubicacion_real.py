"""Escenario A: el vehículo se queda donde termina (o se interrumpe); nunca se registra una llegada que no ocurrió."""
from app import simulador
from app.models import SimulacionViaje
from escenarios import BOGOTA, CALI, GIRARDOT, Viaje, correr, hasta, paso, reposicion_desde


def sim_de(db, v):
    db.expire_all()
    return db.query(SimulacionViaje).filter(SimulacionViaje.envio_id == v.envio_id).one()


def eventos(bus, nombre=None):
    return [e for e in bus if e[0] == "evento" and (nombre is None or e[1] == nombre)]


def terminar(db, reloj, v):
    assert hasta(reloj, lambda: sim_de(db, v).estado == "finalizada"), "el viaje no terminó"
    paso(reloj, 3)


def test_al_llegar_se_informa_a_fleet_el_destino_ANTES_de_publicar_la_llegada(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI, nombre_destino="Cali")
    v.asignar()
    terminar(db, reloj, v)
    orden = [(e[0], e[1]) for e in bus if e[1] in ("ubicacion", "tracking.trip_arrived")]
    assert orden == [("fleet", "ubicacion"), ("evento", "tracking.trip_arrived")]
    cuerpo = next(e[2] for e in bus if e[1] == "ubicacion")
    assert (cuerpo["latitud"], cuerpo["longitud"], cuerpo["fuente"]) == (CALI[0], CALI[1], "entrega")
    assert cuerpo["codigo_pais"] == "CO" and cuerpo["nombre"] == "Cali"
    assert sim_de(db, v).ubicacion_informada is True


def test_si_fleet_no_responde_la_llegada_se_publica_igual_y_la_ubicacion_se_sigue_intentando(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar()
    vecinos.fleet_caido = True
    assert hasta(reloj, lambda: sim_de(db, v).estado == "finalizada")
    paso(reloj, 6)
    assert len(eventos(bus, "tracking.trip_arrived")) == 1       # no se bloquea la entrega
    assert sim_de(db, v).ubicacion_informada is False             # pero queda pendiente
    vecinos.fleet_caido = False
    paso(reloj, 2)
    assert sim_de(db, v).ubicacion_informada is True and len(vecinos.ubicaciones) == 1


def test_un_viaje_devuelto_a_mitad_de_camino_informa_donde_quedo_y_no_publica_llegada(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar()
    hasta(reloj, lambda: sim_de(db, v).km_recorridos > 100)
    correr(simulador.procesar_envio_devuelto({"envio_id": str(v.envio_id)}))
    paso(reloj, 3)
    s = sim_de(db, v)
    assert s.estado == "cancelada" and s.ubicacion_informada is True
    assert eventos(bus, "tracking.trip_arrived") == []
    cuerpo = vecinos.ubicaciones[-1][1]
    assert cuerpo["fuente"] == "interrupcion" and (cuerpo["latitud"], cuerpo["longitud"]) == (s.latitud, s.longitud)
    # está en el camino, no en Bogotá ni en Cali
    assert 3.5 < cuerpo["latitud"] < 4.7 and cuerpo["codigo_pais"] == "CO"


def test_un_envio_cancelado_a_mitad_de_camino_tambien_deja_su_posicion_real(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar()
    hasta(reloj, lambda: sim_de(db, v).km_recorridos > 50)
    correr(simulador.procesar_envio_cancelado({"envio_id": str(v.envio_id)}))
    paso(reloj, 3)
    assert sim_de(db, v).estado == "cancelada" and vecinos.ubicaciones[-1][1]["fuente"] == "interrupcion"
    assert eventos(bus, "tracking.trip_arrived") == []


def test_cancelado_antes_de_moverse_restaura_la_ubicacion_que_tenia(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar(reposicion=reposicion_desde(GIRARDOT, BOGOTA))
    correr(simulador.procesar_envio_cancelado({"envio_id": str(v.envio_id)}))     # aún "preparando"
    paso(reloj, 2)
    cuerpo = vecinos.ubicaciones[-1][1]
    assert (cuerpo["latitud"], cuerpo["longitud"]) == GIRARDOT                  # vuelve a Girardot, no se va a Bogotá
    assert cuerpo["tomada_en"] == "2026-10-20T10:00:00+00:00"                   # con su fecha original
    assert eventos(bus, "tracking.trip_arrived") == []


def test_cancelado_antes_de_moverse_sin_ubicacion_fiable_no_inventa_nada(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar(reposicion=reposicion_desde(GIRARDOT, BOGOTA, fiable=False))
    correr(simulador.procesar_envio_cancelado({"envio_id": str(v.envio_id)}))
    paso(reloj, 2)
    assert vecinos.ubicaciones == [] and sim_de(db, v).ubicacion_informada is True


def test_envio_ya_cancelado_al_preparar_conserva_su_ubicacion(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    vecinos.envios[str(v.envio_id)] = {"estado": "cancelado"}
    v.asignar(reposicion=reposicion_desde(GIRARDOT, BOGOTA))
    paso(reloj, 3)
    assert sim_de(db, v).estado == "cancelada"
    assert (vecinos.ubicaciones[-1][1]["latitud"]) == GIRARDOT[0]


def test_entrega_manual_a_mitad_de_camino_no_teletransporta_el_vehiculo_al_destino(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar()
    hasta(reloj, lambda: sim_de(db, v).km_recorridos > 100)
    correr(simulador.procesar_entrega_manual({"envio_id": str(v.envio_id)}))
    paso(reloj, 3)
    s = sim_de(db, v)
    cuerpo = vecinos.ubicaciones[-1][1]
    assert s.estado == "finalizada" and cuerpo["fuente"] == "interrupcion"
    assert (cuerpo["latitud"], cuerpo["longitud"]) == (s.latitud, s.longitud) != CALI
    assert eventos(bus, "tracking.trip_arrived") == []


def test_la_entrega_normal_no_se_informa_dos_veces(db, reloj, bus, vecinos):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar()
    terminar(db, reloj, v)
    paso(reloj, 5)
    assert len(vecinos.ubicaciones) == 1
