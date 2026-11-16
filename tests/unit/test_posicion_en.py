"""Unitaria 2: geo.posicion_en ubica el vehículo a una fracción del recorrido."""
import pytest

from app.geo import acumulados, posicion_en

PUNTOS = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)]


def test_posicion_en_devuelve_inicio_mitad_y_final_del_recorrido():
    acum = acumulados(PUNTOS)
    assert posicion_en(PUNTOS, acum, 0) == (0.0, 0.0)
    assert posicion_en(PUNTOS, acum, 0.5) == pytest.approx((0.0, 1.0), abs=1e-6)
    assert posicion_en(PUNTOS, acum, 1.5) == (0.0, 2.0)  # se limita al final
