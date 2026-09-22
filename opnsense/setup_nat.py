#!/usr/bin/env python3
"""Publica la web de la DMZ hacia Internet con un port forward (spec §7).

En OPNsense 25.7 el NAT de destino no tiene API MVC, asi que se envia el mismo
formulario que la GUI (opnsense/gui.py) y es OPNsense quien escribe su propia
estructura en config.xml. El PERMISO no se crea aqui: lo declara flows.yml
(`wan-to-published-web`) y lo aplica apply.py, de modo que la matriz sigue
siendo la unica fuente de verdad de lo que esta permitido.

Idempotente: si el port forward ya existe, no hace nada.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api import Opnsense  # noqa: E402
from gui import Gui  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FLOWS = REPO / "opnsense" / "flows.yml"
PREFIJO = "netseg"


def publicaciones() -> list[dict]:
    """Deriva de flows.yml que hay que publicar: los permisos con src WAN."""
    flows = yaml.safe_load(FLOWS.read_text(encoding="utf-8"))
    hosts = flows["hosts"]
    salida = []
    for permiso in flows["allow"]:
        if permiso["src"] != "WAN" or permiso["dst"] not in hosts:
            continue
        for entrada in permiso["ports"]:
            puerto, proto = entrada.split("/", 1)
            salida.append(
                {
                    "id": permiso["id"],
                    "destino": hosts[permiso["dst"]]["ip"],
                    "puerto": puerto,
                    "proto": proto,
                    "descr": f"{PREFIJO}:{permiso['id']} {puerto}/{proto} - {permiso['reason']}",
                }
            )
    return salida


def existentes(gui: Gui) -> str:
    return gui.get("/firewall_nat.php")


def crear(gui: Gui, pub: dict) -> None:
    """Envia el formulario legacy de alta de port forward."""
    gui.post(
        "/firewall_nat_edit.php",
        {
            "interface[]": "wan",
            "ipprotocol": "inet",
            "protocol": pub["proto"],
            "src": "any",
            "srcmask": "32",
            "srcbeginport": "any",
            "srcendport": "any",
            "dst": "wanip",  # la IP de la WAN, que llega por DHCP
            "dstmask": "32",
            "dstbeginport": pub["puerto"],
            "dstendport": pub["puerto"],
            "target": pub["destino"],
            "local-port": pub["puerto"],
            "poolopts": "",
            "natreflection": "default",
            # sin regla asociada: el permiso lo gobierna flows.yml
            "associated-rule-id": "",
            "descr": pub["descr"],
            "tag": "",
            "tagged": "",
            "Submit": "Save",
        },
    )


def main() -> int:
    gui = Gui()
    if not gui.login():
        print("FALLO: no se pudo iniciar sesion en la GUI de fw01", file=sys.stderr)
        return 1

    pagina = existentes(gui)
    creados = 0
    for pub in publicaciones():
        marca = f"{PREFIJO}:{pub['id']} {pub['puerto']}/{pub['proto']}"
        if marca in pagina:
            print(f"   ya publicado: {marca}")
            continue
        crear(gui, pub)
        creados += 1
        print(f"   publicado: {pub['destino']}:{pub['puerto']}/{pub['proto']} desde la WAN")

    if creados:
        gui.post("/firewall_nat.php", {"apply": "Apply changes"})
        Opnsense().post("/api/firewall/filter/apply")
        print(f"aplicado: {creados} port forward(s)")
    else:
        print("nada que hacer: la publicacion ya estaba en su sitio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
