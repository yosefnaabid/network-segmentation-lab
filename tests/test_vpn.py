"""Fase 6: la VPN llega a MGMT y SOLO a MGMT. Requiere el lab (marker lab).

Se comprueba desde ext01, que esta fuera del perimetro: es el unico punto de
observacion valido para un acceso remoto (spec §4). El tunel debe estar
levantado previamente con scripts/configurar_cliente_vpn.py.
"""

from __future__ import annotations

import pytest

EXT01 = "ext01"
# Un host de MGMT (alcanzable por el tunel) y uno de cada segmento que la VPN
# no debe tocar. Salen del plan de direccionamiento del spec.
DESTINO_MGMT = "10.10.50.10"
DESTINOS_PROHIBIDOS = {
    "SERVERS": "10.10.20.20",
    "DMZ": "10.10.30.10",
    "USERS": "10.10.10.1",
}

def _sondear(pve_exec, ip: str, puerto: int) -> str:
    """Un intento de conexion TCP desde ext01, con la tabla de estados de siempre.

    Se usa busybox nc y se mide el tiempo, igual que en la matriz: un rechazo
    vuelve al instante y un descarte agota la espera.
    """
    orden = (
        f"t0=$(date +%s); nc -z -w 4 {ip} {puerto} 2>/dev/null; rc=$?; t1=$(date +%s); "
        'if [ $rc -eq 0 ]; then echo ABIERTO; '
        'elif [ $((t1-t0)) -ge 4 ]; then echo SIN-RESPUESTA; else echo RECHAZADO; fi'
    )
    res = pve_exec(EXT01, ["/bin/sh", "-c", orden], timeout=60)
    return res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "SIN-DATO"


@pytest.fixture(scope="module", autouse=True)
def tunel(pve_exec):
    """Levanta el tunel en ext01 (la matriz de conectividad lo deja bajado)."""
    pve_exec(EXT01, ["/bin/sh", "-c", "wg-quick up wg0 2>/dev/null; true"], timeout=90)
    yield
    pve_exec(EXT01, ["/bin/sh", "-c", "wg-quick down wg0 2>/dev/null; true"], timeout=90)


@pytest.mark.lab
def test_tunel_levantado(pve_exec):
    """El cliente externo tiene el tunel activo y con handshake reciente."""
    res = pve_exec(EXT01, ["/bin/sh", "-c", "wg show wg0 latest-handshakes"])
    assert res.rc == 0, f"wg no responde en ext01: {res.stderr[:200]}"
    marcas = [int(linea.split()[-1]) for linea in res.stdout.strip().splitlines() if linea.split()]
    assert marcas and max(marcas) > 0, "el tunel no ha completado ningun handshake"


@pytest.mark.lab
def test_la_vpn_alcanza_mgmt(pve_exec):
    """El bastion de MGMT responde por el tunel: para eso existe la VPN."""
    assert _sondear(pve_exec, DESTINO_MGMT, 22) == "ABIERTO"


@pytest.mark.lab
@pytest.mark.parametrize("segmento,ip", sorted(DESTINOS_PROHIBIDOS.items()))
def test_la_vpn_no_alcanza_nada_mas(pve_exec, segmento, ip):
    """Y nada mas: el resto de segmentos queda fuera del alcance del tunel."""
    puerto = 22 if segmento != "DMZ" else 80
    assert _sondear(pve_exec, ip, puerto) != "ABIERTO", (
        f"la VPN alcanza {segmento} ({ip}:{puerto}) y no deberia"
    )
