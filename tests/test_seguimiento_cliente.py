"""Seguimiento para la cuenta de CLIENTE: autorización por dueño, respuesta reducida y estados sin inventar datos."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import clientes_http, simulador
from app.database import get_db
from app.main import app
from app.models import CargaCompartida, LecturaTelemetria, SimulacionViaje
from escenarios import BOGOTA, CALI, GIRARDOT, MONTERIA, Viaje, correr, hasta, paso, reposicion_desde

CLIENTE_A = str(uuid.uuid4())
CLIENTE_B = str(uuid.uuid4())
USUARIO_A = str(uuid.uuid4())

# Nada de esto puede aparecer jamás en lo que ve un cliente, ni como clave ni como valor
PROHIBIDAS = {"vehiculo", "placa", "conductor_id", "conductor_nombre", "combustible", "vehiculo_id", "ruta_id", "ruta_anfitriona_id",
              "reposicion", "carga_compartida", "factor_tiempo", "codigo_ruta", "nivel_combustible_pct", "litros", "autonomia_km"}


class Shipment:
    """Shipment simulado: responde 404 a un cliente que pide un envío ajeno (igual que el real)."""

    def __init__(self):
        self.envios = {}
        self.caido = False
        self.llamadas = []

    def crear(self, cliente, envio_id, estado="en_transito", **extra):
        self.envios[str(envio_id)] = {"id": str(envio_id), "cliente_id": cliente, "codigo_envio": "ENV-00000001",
                                      "origen": "Origen", "destino": "Destino", "estado": estado, **extra}

    async def obtener_envio_de_cliente(self, envio_id, cliente_id, usuario_id=None):
        self.llamadas.append((str(envio_id), str(cliente_id), usuario_id))
        if self.caido:
            raise clientes_http.DatosNoDisponibles("Shipment no responde")
        envio = self.envios.get(str(envio_id))
        if envio is None or envio["cliente_id"] != str(cliente_id):
            raise clientes_http.EnvioNoEncontrado()
        return envio


@pytest.fixture()
def shipment(monkeypatch):
    s = Shipment()
    monkeypatch.setattr(clientes_http, "obtener_envio_de_cliente", s.obtener_envio_de_cliente)
    return s


@pytest.fixture()
def api(db, shipment):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def como(cliente=CLIENTE_A, rol="cliente", usuario=USUARIO_A):
    cab = {}
    if rol:
        cab["X-User-Rol"] = rol
    if cliente:
        cab["X-Cliente-Id"] = cliente
    if usuario:
        cab["X-User-Id"] = usuario
    return cab


def ver(api, envio_id, sufijo="", cliente=CLIENTE_A, **kw):
    return api.get(f"/seguimiento/cliente/envio/{envio_id}{sufijo}", headers=como(cliente, **kw))


def claves(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from claves(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from claves(v)


def sim_de(db, v):
    db.expire_all()
    return db.query(SimulacionViaje).filter(SimulacionViaje.envio_id == v.envio_id).one()


def viaje_de(shipment, vecinos, cliente=CLIENTE_A, origen=BOGOTA, destino=CALI, **extra):
    v = Viaje(vecinos, origen, destino, nombre_origen="Bogotá", nombre_destino="Cali")
    shipment.crear(cliente, v.envio_id, **extra)
    return v


# ---------------- Propiedad del envío ----------------

def test_el_cliente_ve_su_propio_envio_con_ruta_posicion_y_progreso(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos)
    v.asignar()
    paso(reloj, 2, 20)
    r = ver(api, v.envio_id)
    assert r.status_code == 200
    d = r.json()
    assert d["envio_id"] == str(v.envio_id) and d["estado_viaje"] == "en_curso" and d["mensaje"] == "En camino."
    assert d["origen"]["nombre"] == "Origen" and d["destino"]["nombre"] == "Destino"
    assert d["posicion"]["disponible"] and d["posicion"]["latitud"] is not None and d["posicion"]["actualizada_en"]
    assert d["posicion"]["desactualizada"] is False
    assert d["progreso"]["km_totales"] > 0 and d["tiempos"]["hora_estimada_llegada"] and d["trazado_disponible"] is True


def test_un_envio_ajeno_y_uno_inexistente_responden_exactamente_igual(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos, cliente=CLIENTE_B)
    v.asignar()
    paso(reloj, 1, 20)
    for sufijo in ("", "/trazado", "/recorrido"):
        ajeno = ver(api, v.envio_id, sufijo)
        inexistente = ver(api, uuid.uuid4(), sufijo)
        assert ajeno.status_code == inexistente.status_code == 404
        assert ajeno.json() == inexistente.json() == {"detail": {"codigo": "ENVIO_NO_ENCONTRADO", "mensaje": "Envío no encontrado"}}


def test_el_dueno_se_decide_con_la_identidad_del_gateway_no_con_lo_que_pida_el_frontend(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos, cliente=CLIENTE_B)
    v.asignar()
    paso(reloj, 1, 20)
    # el frontend intenta colar el cliente de otro en la URL, en la query y en un cuerpo: no cambia nada
    r = api.get(f"/seguimiento/cliente/envio/{v.envio_id}?cliente_id={CLIENTE_B}&customer_id={CLIENTE_B}", headers=como(CLIENTE_A))
    assert r.status_code == 404
    assert shipment.llamadas[-1][1] == CLIENTE_A          # a Shipment solo se le pregunta como el cliente autenticado


def test_si_shipment_devolviera_un_envio_de_otro_cliente_igual_se_rechaza(db, reloj, bus, vecinos, monkeypatch, api):
    v = Viaje(vecinos, BOGOTA, CALI)
    v.asignar()

    async def mal(envio_id, cliente_id, usuario_id=None):      # fallo hipotético de Shipment
        return {"id": str(envio_id), "cliente_id": CLIENTE_B, "estado": "en_transito"}

    monkeypatch.setattr(clientes_http, "obtener_envio_de_cliente", mal)
    assert ver(api, v.envio_id).status_code == 404


@pytest.mark.parametrize("cab", [
    {}, {"X-Cliente-Id": CLIENTE_A},                                # sin rol
    {"X-User-Rol": "cliente"}, {"X-User-Rol": "cliente", "X-Cliente-Id": "no-es-uuid"},   # cliente sin cliente_id
    {"X-User-Rol": "administrador", "X-Cliente-Id": CLIENTE_A}, {"X-User-Rol": "operador"}, {"X-User-Rol": "gestor_flota"},
])
def test_solo_una_cuenta_de_cliente_con_su_cliente_id_puede_usarlo(db, vecinos, shipment, api, cab):
    v = viaje_de(shipment, vecinos)
    for sufijo in ("", "/trazado", "/recorrido"):
        r = api.get(f"/seguimiento/cliente/envio/{v.envio_id}{sufijo}", headers=cab)
        assert r.status_code == 403 and r.json()["detail"]["codigo"] == "SIN_PERMISO"
    assert shipment.llamadas == []                               # ni siquiera se consulta nada


def test_si_shipment_no_responde_es_503_y_no_se_muestra_nada(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos)
    v.asignar()
    paso(reloj, 1, 20)
    shipment.caido = True
    r = ver(api, v.envio_id)
    assert r.status_code == 503 and r.json()["detail"]["codigo"] == "SEGUIMIENTO_NO_DISPONIBLE"
    assert "latitud" not in r.text


def test_un_id_que_no_es_uuid_no_llega_a_nada(db, shipment, api):
    assert ver(api, "no-es-un-uuid").status_code == 422
    assert shipment.llamadas == []


# ---------------- Respuesta reducida ----------------

def test_la_respuesta_nunca_trae_vehiculo_conductor_combustible_ni_datos_internos(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos)
    v.asignar(reposicion=reposicion_desde(GIRARDOT, BOGOTA))
    for n in (1, 20):
        paso(reloj, n, 60)
        for sufijo in ("", "/trazado", "/recorrido"):
            r = ver(api, v.envio_id, sufijo)
            assert r.status_code in (200, 409), r.text
            assert not (set(claves(r.json())) & PROHIBIDAS), (sufijo, set(claves(r.json())) & PROHIBIDAS)
            for secreto in ("ABC123", "Pedro", str(v.vehiculo_id), str(v.ruta_id)):
                assert secreto not in r.text


# ---------------- Estados que el cliente debe entender ----------------

@pytest.mark.parametrize("estado,texto", [
    ("programado", "programado"), ("pendiente", "asignando un vehículo"), ("sin_asignacion", "no hay un vehículo disponible"),
    ("cancelado", "cancelado"), ("devuelto", "devuelto"),
])
def test_sin_vehiculo_ni_viaje_se_explica_y_no_se_inventa_ubicacion(db, shipment, api, estado, texto):
    envio = uuid.uuid4()
    shipment.crear(CLIENTE_A, envio, estado=estado)
    d = ver(api, envio).json()
    assert d["estado_viaje"] == "sin_viaje" and texto in d["mensaje"].lower()
    assert d["posicion"]["disponible"] is False and d["posicion"]["latitud"] is None and d["posicion"]["longitud"] is None
    assert d["trazado_disponible"] is False and d["progreso"] is None
    assert ver(api, envio, "/trazado").status_code == 404
    assert ver(api, envio, "/recorrido").json() == []


def test_pedido_dividido_avisa_que_se_sigue_por_partes(db, shipment, api):
    envio = uuid.uuid4()
    shipment.crear(CLIENTE_A, envio, estado="en_transito", partes_total=3)
    d = ver(api, envio).json()
    assert d["es_pedido_dividido"] is True and "partes" in d["mensaje"] and d["posicion"]["disponible"] is False


def test_viaje_preparando_no_tiene_ubicacion_todavia(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos)
    v.asignar()
    sim = sim_de(db, v)
    sim.estado, sim.latitud, sim.longitud = "preparando", None, None
    db.commit()
    d = ver(api, v.envio_id).json()
    assert d["estado_viaje"] == "preparando" and d["posicion"]["disponible"] is False and d["posicion"]["desactualizada"] is False
    assert ver(api, v.envio_id, "/trazado").status_code in (200, 409)


def test_retenido_en_aduana_se_explica(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos, estado="retenido_aduana")
    v.asignar()
    paso(reloj, 1, 20)
    sim = sim_de(db, v)
    sim.estado = "pausada"
    db.commit()
    d = ver(api, v.envio_id).json()
    assert d["estado_viaje"] == "pausada" and "aduana" in d["mensaje"].lower() and d["posicion"]["desactualizada"] is False


def test_ubicacion_desactualizada_solo_cuando_el_viaje_sigue_en_curso(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos)
    v.asignar()
    paso(reloj, 1, 20)
    assert ver(api, v.envio_id).json()["posicion"]["desactualizada"] is False
    reloj.avanzar(simulador.TICK_SEGUNDOS * 3 + 600)          # el simulador dejó de reportar
    p = ver(api, v.envio_id).json()["posicion"]
    assert p["desactualizada"] is True and p["segundos_desde_actualizacion"] > p["umbral_segundos"] and p["disponible"] is True


def test_viaje_terminado_muestra_el_final_y_no_es_obsoleto(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos, estado="entregado")
    v.asignar()
    assert hasta(reloj, lambda: sim_de(db, v).estado == "finalizada")
    reloj.avanzar(7200)
    d = ver(api, v.envio_id).json()
    assert d["estado_viaje"] == "finalizada" and d["mensaje"] == "Llegó a su destino." and d["progreso"]["porcentaje"] == 100.0
    assert d["posicion"]["desactualizada"] is False and d["tiempos"]["llegada_en"] and d["fase"] is None


def test_envio_cancelado_en_shipment_manda_sobre_el_mensaje_del_viaje(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos)
    v.asignar()
    paso(reloj, 1, 20)
    shipment.envios[str(v.envio_id)]["estado"] = "cancelado"
    assert ver(api, v.envio_id).json()["mensaje"] == "Envío cancelado."


def test_incidencia_se_comunica(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos, estado="con_incidencia")
    v.asignar()
    paso(reloj, 1, 20)
    assert "incidencia" in ver(api, v.envio_id).json()["mensaje"]


# ---------------- Tramo en vacío y estela ----------------

def test_mientras_el_vehiculo_va_a_recoger_se_dice_y_no_hay_estela_ni_progreso(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos)
    v.asignar(reposicion=reposicion_desde(GIRARDOT, BOGOTA))
    paso(reloj, 2, 20)
    d = ver(api, v.envio_id).json()
    assert d["fase"] == "reposicion" and d["mensaje"] == "El vehículo se dirige al punto de recogida."
    assert d["progreso"]["porcentaje"] == 0.0 and d["posicion"]["disponible"] is True
    assert ver(api, v.envio_id, "/recorrido").json() == []           # nada del tramo en vacío
    assert "Girardot" not in json.dumps(d) and "salio_de" not in json.dumps(d)


def test_la_estela_empieza_cuando_el_vehiculo_lleva_su_carga(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos)
    v.asignar(reposicion=reposicion_desde(GIRARDOT, BOGOTA))
    assert hasta(reloj, lambda: sim_de(db, v).fase == "viaje")
    paso(reloj, 6, 60)
    estela = ver(api, v.envio_id, "/recorrido").json()
    assert len(estela) >= 3 and set(estela[0]) == {"tiempo", "latitud", "longitud"}
    assert estela == sorted(estela, key=lambda p: p["tiempo"])
    from datetime import datetime
    inicio = sim_de(db, v).iniciado_en
    assert all(datetime.fromisoformat(p["tiempo"].replace("Z", "+00:00")).replace(tzinfo=inicio.tzinfo) >= inicio for p in estela)


def test_el_trazado_es_el_de_su_ruta_con_origen_y_destino(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos)
    v.asignar()
    paso(reloj, 1, 20)
    t = ver(api, v.envio_id, "/trazado").json()
    assert t["origen"]["nombre"] == "Origen" and t["destino"]["nombre"] == "Destino" and len(t["puntos"]) >= 2
    assert t["puntos"][0] == [pytest.approx(BOGOTA[0]), pytest.approx(BOGOTA[1])]


# ---------------- Carga compartida: solo SU tramo ----------------

def _anfitrion_y_carga(db, reloj, bus, vecinos, shipment, km_recogida=100, km_entrega=300):
    h = Viaje(vecinos, MONTERIA, CALI, km=800.0, nombre_origen="Montería", nombre_destino="Cali")
    shipment.crear(CLIENTE_B, h.envio_id)                      # el anfitrión es de OTRO cliente
    h.asignar()
    envio, ruta = uuid.uuid4(), uuid.uuid4()
    shipment.crear(CLIENTE_A, envio, origen="Montería", destino="Cali")
    payload = {
        "ruta_id": str(ruta), "envio_id": str(envio), "vehiculo_id": str(h.vehiculo_id), "conductor_id": str(uuid.uuid4()),
        "placa": "ZZZ999", "conductor_nombre": "Luisa", "codigo_ruta": "RUT-2", "modo": "compartida",
        "ruta_anfitriona_id": str(h.ruta_id), "km_recogida": km_recogida, "km_entrega": km_entrega,
        "origen": {"nombre": "Sincelejo"}, "destino": {"nombre": "Medellín"},
    }
    correr(simulador.procesar_route_assigned(payload))
    return h, envio


def test_la_carga_compartida_ve_su_tramo_y_nada_del_anfitrion(db, reloj, bus, vecinos, shipment, api):
    h, envio = _anfitrion_y_carga(db, reloj, bus, vecinos, shipment)
    assert hasta(reloj, lambda: sim_de(db, h).km_recorridos > 200, segundos=15)
    d = ver(api, envio).json()
    assert d["viaja_compartido"] is True and d["estado_viaje"] == "en_curso" and d["posicion"]["disponible"]
    assert d["progreso"]["km_totales"] == 200.0 and 40 < d["progreso"]["km_recorridos"] < 140
    assert d["origen"]["nombre"] == "Sincelejo" and d["destino"]["nombre"] == "Medellín"
    assert d["paradas"] == [] and not (set(claves(d)) & PROHIBIDAS)
    t = ver(api, envio, "/trazado").json()
    assert t["distancia_km"] == 200.0 and t["origen"]["nombre"] == "Sincelejo"
    # el trazado es SOLO su tramo: empieza y termina en sus puntos, no en los del anfitrión
    assert t["puntos"][0] != [pytest.approx(MONTERIA[0]), pytest.approx(MONTERIA[1])]
    assert t["puntos"][-1] != [pytest.approx(CALI[0]), pytest.approx(CALI[1])]
    for secreto in ("ZZZ999", "Luisa", "ABC123", str(h.ruta_id), str(h.vehiculo_id)):
        assert secreto not in json.dumps(d) + json.dumps(t)
    # y el cliente dueño del anfitrión no puede ver la carga ajena
    assert ver(api, envio, cliente=CLIENTE_B).status_code == 404


def test_la_carga_compartida_pendiente_aun_no_tiene_estela(db, reloj, bus, vecinos, shipment, api):
    h, envio = _anfitrion_y_carga(db, reloj, bus, vecinos, shipment, km_recogida=400, km_entrega=700)
    paso(reloj, 2, 15)
    d = ver(api, envio).json()
    assert d["estado_viaje"] == "preparando" and "recogida" in d["mensaje"]
    assert ver(api, envio, "/recorrido").json() == []


def test_el_anfitrion_del_otro_cliente_no_se_ve_desde_el_endpoint_de_cliente(db, reloj, bus, vecinos, shipment, api):
    h, envio = _anfitrion_y_carga(db, reloj, bus, vecinos, shipment)
    assert ver(api, h.envio_id, cliente=CLIENTE_A).status_code == 404
    assert ver(api, h.envio_id, "/trazado", cliente=CLIENTE_A).status_code == 404
    assert ver(api, h.envio_id, "/recorrido", cliente=CLIENTE_A).status_code == 404


# ---------------- No se rompe lo del personal ----------------

def test_los_endpoints_del_personal_siguen_igual(db, reloj, bus, vecinos, shipment, api):
    v = viaje_de(shipment, vecinos)
    v.asignar()
    paso(reloj, 2, 20)
    d = api.get(f"/seguimiento/envio/{v.envio_id}").json()
    assert d["vehiculo"]["placa"] == "ABC123" and d["combustible"]["nivel_pct"] is not None
    assert len(api.get("/seguimiento/flota").json()) == 1
