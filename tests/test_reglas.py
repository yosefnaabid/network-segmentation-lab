"""Tests unitarios de la traduccion flows.yml -> reglas. Sin lab (CI)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "opnsense"))

from reglas import (  # noqa: E402
    ALIAS_INTERNAS,
    PREFIJO,
    es_gestionada,
    flujos_pendientes,
    generar,
)

INTERFACES = {
    "USERS": "opt1",
    "SERVERS": "opt2",
    "DMZ": "opt3",
    "IOT": "opt4",
    "MGMT": "opt5",
    "GUEST": "opt6",
    "VPN": "opt7",
}


@pytest.fixture()
def flows() -> dict:
    return yaml.safe_load((REPO / "opnsense" / "flows.yml").read_text(encoding="utf-8"))


def test_todas_las_reglas_son_pass(flows):
    """La denegacion no se escribe: es la politica por defecto (ADR 0002)."""
    for regla in generar(flows, INTERFACES):
        assert regla.accion == "pass"


def test_cada_regla_lleva_su_porque(flows):
    """La descripcion arrastra el 'reason' del flujo, no solo el que."""
    for regla in generar(flows, INTERFACES):
        assert regla.descripcion.startswith(f"{PREFIJO}:")
        assert " - " in regla.descripcion
        assert len(regla.descripcion.split(" - ", 1)[1]) > 10


def test_host_a_host_usa_32(flows):
    """Los flujos declarados contra un host concreto no abren la subred."""
    reglas = [r for r in generar(flows, INTERFACES) if "dmz-to-database" in r.descripcion]
    assert reglas, "falta la regla de dmz-web a la base de datos"
    for regla in reglas:
        assert regla.origen == "10.10.30.10/32"
        assert regla.destino == "10.10.20.20/32"
        assert regla.puerto_destino == "5432"


def test_entrada_por_la_interfaz_de_origen(flows):
    """El filtro se evalua donde entra el trafico: la interfaz del origen."""
    for regla in generar(flows, INTERFACES):
        assert regla.direccion == "in"
    users = [r for r in generar(flows, INTERFACES) if "users-to-fileshare" in r.descripcion]
    assert users and users[0].interfaz == "opt1"


def test_wan_entrante_se_ancla_en_wan(flows):
    reglas = [r for r in generar(flows, INTERFACES) if "wan-to-published-web" in r.descripcion]
    assert reglas
    for regla in reglas:
        assert regla.interfaz == "wan"
        assert regla.destino == "10.10.30.10/32"


def test_una_regla_por_puerto():
    flows = {
        "segments": {"USERS": {"vlan": 10, "cidr": "10.10.10.0/24"}},
        "hosts": {"srv": {"segment": "USERS", "ip": "10.10.10.5"}},
        "probes": {"ports": ["53/udp"], "icmp": False},
        "allow": [
            {
                "id": "prueba",
                "src": "USERS",
                "dst": "srv",
                "ports": ["53/udp", "53/tcp", "80/tcp"],
                "reason": "resolucion y web internas del laboratorio",
            }
        ],
    }
    reglas = generar(flows, {"USERS": "opt1"})
    # OPNsense no admite listas de puertos en una regla: una por puerto
    assert sorted((r.protocolo, r.puerto_destino) for r in reglas) == [
        ("TCP", "53"),
        ("TCP", "80"),
        ("UDP", "53"),
    ]


def test_generacion_es_determinista(flows):
    """Dos pasadas dan exactamente lo mismo: sin esto, make check parpadearia."""
    assert generar(flows, INTERFACES) == generar(flows, INTERFACES)


def test_solo_se_gestionan_las_propias():
    assert es_gestionada("netseg:users-to-fileshare - motivo")
    assert not es_gestionada("Gestion de la GUI desde la red domestica")
    assert not es_gestionada("Default allow LAN to any rule")


def test_salir_a_internet_no_abre_las_redes_internas(flows):
    """"Salida a Internet" es "a cualquier sitio MENOS el propio lab".

    Con destino `any` a secas, permitir navegar dejaba a GUEST llegar a la web
    de la DMZ: lo detecto la propia suite (incidencia #19).
    """
    salidas = [r for r in generar(flows, INTERFACES) if "-internet" in r.descripcion]
    assert salidas
    for regla in salidas:
        assert regla.destino == ALIAS_INTERNAS
        assert regla.destino_negado == "1"
    # y un permiso interno normal no lleva negacion
    interno = next(r for r in generar(flows, INTERFACES) if "users-to-fileshare" in r.descripcion)
    assert interno.destino_negado == "0"


def test_destino_firewall_y_origenes_multiples(flows):
    """Un permiso con lista de origenes genera una regla por segmento."""
    todas = generar(flows, INTERFACES)
    dns = [r for r in todas if "resolucion-contra-el-firewall" in r.descripcion]
    # 6 segmentos x 2 puertos (53/udp y 53/tcp)
    assert len(dns) == 12
    assert {r.destino for r in dns} == {"(self)"}
    assert {r.interfaz for r in dns} == {"opt1", "opt2", "opt3", "opt4", "opt5", "opt6"}


def test_segmento_sin_interfaz_queda_pendiente_y_visible(flows):
    """Sin WireGuard (fase 6) la VPN no tiene interfaz: se omite, pero se lista."""
    sin_vpn = {k: v for k, v in INTERFACES.items() if k != "VPN"}
    reglas = generar(flows, sin_vpn)
    assert not [r for r in reglas if "vpn-to-mgmt" in r.descripcion]
    assert flujos_pendientes(flows, sin_vpn) == ["vpn-to-mgmt"]
    # y con su interfaz, la regla aparece y no queda nada pendiente
    assert [r for r in generar(flows, INTERFACES) if "vpn-to-mgmt" in r.descripcion]
    assert flujos_pendientes(flows, INTERFACES) == []


def test_origen_desconocido_falla():
    flows = {
        "segments": {"USERS": {"vlan": 10, "cidr": "10.10.10.0/24"}},
        "hosts": {},
        "probes": {"ports": ["80/tcp"], "icmp": False},
        "allow": [
            {
                "id": "malo",
                "src": "NOEXISTE",
                "dst": "USERS",
                "ports": ["80/tcp"],
                "reason": "un origen que no existe en el fichero",
            }
        ],
    }
    with pytest.raises(ValueError, match="origen desconocido"):
        generar(flows, {"USERS": "opt1"})
