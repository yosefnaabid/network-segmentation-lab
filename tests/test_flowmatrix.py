"""Tests unitarios de la derivacion de la matriz. Corren SIN lab (CI)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from flowmatrix import (
    FlowMatrix,
    Origin,
    Target,
    build_probe_script,
    load_flows,
    parse_portproto,
    parse_probe_output,
)

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def mini_flows() -> dict:
    return {
        "segments": {
            "USERS": {"vlan": 10, "cidr": "10.10.10.0/24"},
            "SERVERS": {"vlan": 20, "cidr": "10.10.20.0/24"},
            "MGMT": {"vlan": 50, "cidr": "10.10.50.0/24"},
        },
        "hosts": {
            "srv-app": {"segment": "SERVERS", "ip": "10.10.20.20"},
            "jump01": {"segment": "MGMT", "ip": "10.10.50.10"},
        },
        "probes": {"ports": ["22/tcp", "445/tcp", "53/udp"], "icmp": True},
        "allow": [
            {
                "id": "users-to-fileshare",
                "src": "USERS",
                "dst": "srv-app",
                "ports": ["445/tcp"],
                "reason": "acceso a recursos compartidos",
            },
            {
                "id": "mgmt-to-servers",
                "src": "MGMT",
                "dst": "SERVERS",
                "ports": ["22/tcp"],
                "reason": "administracion via bastion",
            },
        ],
    }


def test_parse_portproto():
    assert parse_portproto("443/tcp") == (443, "tcp")
    assert parse_portproto("53/udp") == (53, "udp")


def test_decide_host_y_segmento(mini_flows):
    m = FlowMatrix(mini_flows)
    pc01 = Origin("pc01", "USERS")
    srv_app = Target("srv-app", "SERVERS", "10.10.20.20")
    # host destino explicito
    assert m.decide(pc01, srv_app, 445, "tcp") == (True, "users-to-fileshare")
    # puerto no permitido
    assert m.decide(pc01, srv_app, 22, "tcp") == (False, None)
    # allow por segmento destino (MGMT -> SERVERS cubre srv-app)
    jump = Origin("jump01", "MGMT")
    assert m.decide(jump, srv_app, 22, "tcp") == (True, "mgmt-to-servers")


def test_icmp_denegado_por_defecto(mini_flows):
    m = FlowMatrix(mini_flows)
    pc01 = Origin("pc01", "USERS")
    srv_app = Target("srv-app", "SERVERS", "10.10.20.20")
    assert m.decide(pc01, srv_app, 0, "icmp") == (False, None)


def test_expectations_producto_cartesiano(mini_flows):
    m = FlowMatrix(mini_flows)
    origins = [Origin("pc01", "USERS")]
    targets = [Target("srv-app", "SERVERS", "10.10.20.20"), Target("jump01", "MGMT", "10.10.50.10")]
    exps = m.expectations(origins, targets)
    # 2 destinos x (3 puertos + icmp) = 8 expectativas
    assert len(exps) == 8
    permitidas = [e for e in exps if e.allowed]
    assert len(permitidas) == 1
    assert permitidas[0].dst == "srv-app" and permitidas[0].port == 445
    # todo lo demas, bloqueado: la exhaustividad sale del producto cartesiano
    assert sum(1 for e in exps if not e.allowed) == 7


def test_mismo_segmento_excluido(mini_flows):
    m = FlowMatrix(mini_flows)
    exps = m.expectations(
        [Origin("srv-dns", "SERVERS")], [Target("srv-app", "SERVERS", "10.10.20.20")]
    )
    assert exps == []  # intra-segmento no cruza el firewall


def test_flows_reales_cargan_y_validan():
    """El flows.yml del repo pasa esquema y semantica del validador real."""
    sys.path.insert(0, str(REPO / "scripts"))
    import validate_flows

    flows = load_flows(REPO / "opnsense" / "flows.yml")
    schema = json.loads((REPO / "opnsense" / "flows.schema.json").read_text(encoding="utf-8"))
    assert validate_flows.schema_errors(flows, schema) == []
    errors, _warnings = validate_flows.semantic_errors(flows)
    assert errors == []


def test_flows_reales_casos_representativos():
    m = FlowMatrix(load_flows(REPO / "opnsense" / "flows.yml"))
    pc01 = Origin("pc01", "USERS")
    guest = Origin("guest01", "GUEST")
    iot = Origin("iot01", "IOT")
    dmz = Origin("dmz-web", "DMZ")
    srv_app = Target("srv-app", "SERVERS", "10.10.20.20")
    srv_dns = Target("srv-dns", "SERVERS", "10.10.20.10")
    wan = Target("wan", "WAN", "203.0.113.10")

    assert m.decide(pc01, srv_app, 445, "tcp")[0] is True
    assert m.decide(pc01, srv_app, 5432, "tcp")[0] is False  # sin 3306/5432 desde USERS
    assert m.decide(guest, srv_dns, 53, "udp")[0] is False  # GUEST no toca nada interno
    assert m.decide(iot, wan, 443, "tcp")[0] is True
    assert m.decide(iot, wan, 80, "tcp")[0] is False  # IOT solo 443+NTP
    assert m.decide(dmz, srv_app, 5432, "tcp")[0] is True  # host a host via src dmz-web
    assert m.decide(guest, wan, 443, "tcp")[0] is True


def test_probe_script_alma_y_parseo():
    checks = [("10.10.20.20", 445, "tcp"), ("10.10.20.10", 53, "udp"), ("10.10.50.10", 0, "icmp")]
    argv = build_probe_script("alma", checks, timeout=1)
    assert argv[0] == "python3" and argv[1] == "-c"
    assert "10.10.20.20:445:tcp" in argv
    salida = (
        "R 10.10.20.20 445 tcp open\nR 10.10.20.10 53 udp noanswer\n"
        "R 10.10.50.10 0 icmp timeout\nbasura\n"
    )
    parsed = parse_probe_output(salida)
    assert parsed[("10.10.20.20", 445, "tcp")] == "open"
    assert parsed[("10.10.20.10", 53, "udp")] == "noanswer"
    assert parsed[("10.10.50.10", 0, "icmp")] == "timeout"
    assert len(parsed) == 3


def test_probe_script_alpine_estructura():
    checks = [("10.10.30.10", 443, "tcp"), ("10.10.30.10", 0, "icmp")]
    argv = build_probe_script("alpine", checks, timeout=1)
    assert argv[:2] == ["/bin/sh", "-c"]
    script = argv[2]
    # la sonda mide el tiempo para distinguir un rechazo de un descarte
    assert "nc -z -w 2 10.10.30.10 443" in script
    assert "date +%s" in script
    assert "ping -c 1 -W 1 10.10.30.10" in script


def test_probe_flavor_desconocido():
    with pytest.raises(ValueError):
        build_probe_script("windows", [])
