"""Tests de servicios (harness, DHCP, DNS, NAT). Requieren el lab (marker lab).

La matriz de flujos mide la POLITICA; esto mide que los servicios que la
sostienen existen y funcionan. El acceso remoto tiene su propio fichero
(test_vpn.py) porque exige levantar el tunel.
"""

from __future__ import annotations

import ipaddress

import pytest

# Rangos DHCP del spec §4 (los mismos que configura opnsense/setup_red.py).
POOLS = {
    "pc01": ("10.10.10.100", "10.10.10.200"),
    "iot01": ("10.10.40.50", "10.10.40.150"),
    "guest01": ("10.10.99.10", "10.10.99.250"),
}


def _ip(pve_exec, maquina: str) -> str:
    res = pve_exec(maquina, ["/bin/sh", "-c", "ip -4 -o addr show eth0"])
    for parte in res.stdout.split():
        if "/" in parte and parte[0].isdigit():
            return parte.split("/")[0]
    return ""


@pytest.mark.lab
def test_agente_qemu_en_vms(inventory, pve_exec):
    """Las VMs AlmaLinux responden via qemu-guest-agent (harness, fase 1-2)."""
    vms = [n for n, m in inventory.items() if m["type"] == "qemu" and n != "fw01"]
    assert vms, "el inventario no tiene VMs"
    for name in vms:
        res = pve_exec(name, ["/bin/true"])
        assert res.rc == 0, f"{name}: el agente no ejecuta comandos ({res.stderr})"


@pytest.mark.lab
def test_exec_en_lxc(inventory, pve_exec):
    """Los 4 clientes LXC responden via pct exec (harness, fase 1-2)."""
    lxcs = [n for n, m in inventory.items() if m["type"] == "lxc"]
    assert len(lxcs) == 4, f"esperaba 4 LXC, hay {len(lxcs)}"
    for name in lxcs:
        res = pve_exec(name, ["/bin/true"])
        assert res.rc == 0, f"{name}: pct exec fallo ({res.stderr})"


@pytest.mark.lab
@pytest.mark.parametrize("maquina,rango", sorted(POOLS.items()))
def test_dhcp_por_segmento(pve_exec, maquina, rango):
    """Cada cliente recibe direccion del pool de SU segmento, no de otro."""
    ip = ipaddress.ip_address(_ip(pve_exec, maquina))
    inicio, fin = (ipaddress.ip_address(x) for x in rango)
    assert inicio <= ip <= fin, f"{maquina} tiene {ip}, fuera del pool {rango}"


@pytest.mark.lab
def test_dns_interno_resuelve_la_zona(pve_exec):
    """srv-dns es autoritativo de lab.interno y responde a los de su segmento."""
    res = pve_exec(
        "srv-app",
        ["/bin/sh", "-c", "getent hosts srv-dns.lab.interno || dig +short srv-dns.lab.interno"],
    )
    assert "10.10.20.10" in res.stdout, f"la zona interna no resuelve: {res.stdout[:120]}"


@pytest.mark.lab
def test_dns_externo_directo_esta_bloqueado(pve_exec):
    """Un cliente no puede saltarse el resolver del lab (ADR 0003)."""
    orden = (
        "t0=$(date +%s); nc -z -w 3 -u 8.8.8.8 53 2>/dev/null; t1=$(date +%s); "
        'if [ $((t1-t0)) -ge 3 ]; then echo BLOQUEADO; else echo ALCANZABLE; fi'
    )
    res = pve_exec("iot01", ["/bin/sh", "-c", orden], timeout=60)
    assert "BLOQUEADO" in res.stdout, "IOT alcanza un DNS publico y no deberia"


@pytest.mark.lab
def test_port_forward_publica_la_web(pve_exec):
    """Desde fuera del perimetro, la web de la DMZ responde por la IP publica."""
    publica = pve_exec("fw01", ["/bin/sh", "-c", "ifconfig vtnet1 inet"]).stdout
    ip = next(p for p in publica.split() if p.count(".") == 3 and p[0].isdigit())
    res = pve_exec("ext01", ["/bin/sh", "-c", f"wget -q -O - -T 8 http://{ip}/"], timeout=90)
    assert "dmz-web" in res.stdout, f"la web publicada no responde en {ip}: {res.stdout[:100]}"
