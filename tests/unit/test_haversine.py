"""Unitaria 1: geo.haversine_km calcula la distancia entre dos coordenadas."""
import pytest

from app.geo import haversine_km


def test_haversine_un_grado_de_latitud_son_111_km_y_el_mismo_punto_es_cero():
    assert haversine_km(0, 0, 1, 0) == pytest.approx(111.19, abs=0.1)
    assert haversine_km(4.7110, -74.0721, 4.7110, -74.0721) == 0
