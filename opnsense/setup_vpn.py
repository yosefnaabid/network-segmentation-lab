#!/usr/bin/env python3
"""Fase 6: WireGuard de acceso remoto que llega a MGMT y solo a MGMT.

WireGuard esta en el nucleo de OPNsense desde 24.1 (no hay que instalar plugin).
Este script crea el servidor, un peer para el cliente externo y deja escrita la
configuracion del cliente. Idempotente: si ya existen, no los duplica.

El permiso de la VPN hacia MGMT no se define aqui: lo declara flows.yml
(`vpn-to-mgmt`) y lo aplica apply.py. Aqui solo se monta el tunel.

La clave privada del cliente NO se guarda en el firewall ni en el repo: se
escribe en ~/.netseg-vpn/ con permisos 600.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api import ApiError, Opnsense  # noqa: E402

NOMBRE_SERVIDOR = "netseg_roadwarrior"
NOMBRE_CLIENTE = "ext01"
PUERTO = "51820"
RED_TUNEL = "10.10.60.1/24"
IP_CLIENTE = "10.10.60.2/32"
RED_MGMT = "10.10.50.0/24"
DESTINO = Path.home() / ".netseg-vpn"


def buscar(api: Opnsense, ruta: str, campo: str, valor: str) -> dict | None:
    for fila in api.get(ruta).get("rows", []):
        if fila.get(campo) == valor:
            return fila
    return None


def asegurar_servidor(api: Opnsense, ip_publica: str) -> tuple[str, str]:
    """Crea el servidor si falta. Devuelve (uuid, clave publica del servidor)."""
    existente = buscar(api, "/api/wireguard/server/search_server", "name", NOMBRE_SERVIDOR)
    if existente:
        uuid = existente["uuid"]
        detalle = api.get(f"/api/wireguard/server/get_server/{uuid}")["server"]
        print(f"servidor WireGuard ya existente ({uuid[:8]}...)")
        return uuid, detalle.get("pubkey", "")

    claves = api.get("/api/wireguard/server/key_pair")
    if claves.get("status") != "ok":
        raise ApiError(f"no se pudieron generar las claves del servidor: {claves}")

    payload = {
        "server": {
            "enabled": "1",
            "name": NOMBRE_SERVIDOR,
            "privkey": claves["privkey"],
            "pubkey": claves["pubkey"],
            "port": PUERTO,
            "mtu": "1420",
            "tunneladdress": RED_TUNEL,
            "disableroutes": "0",
            "gateway": "",
            "dns": "",
            "peers": "",
            "endpoint": ip_publica,
            "peer_dns": "10.10.50.1",
            "debug": "0",
        }
    }
    respuesta = api.exigir_ok(
        api.post("/api/wireguard/server/add_server", payload), "crear servidor WireGuard"
    )
    print(f"servidor WireGuard creado en {RED_TUNEL}, puerto {PUERTO}")
    return respuesta["uuid"], claves["pubkey"]


def asegurar_cliente(api: Opnsense, uuid_servidor: str) -> dict:
    """Crea el peer del cliente si falta y devuelve sus datos (con la privada)."""
    fichero = DESTINO / f"{NOMBRE_CLIENTE}.json"
    existente = buscar(api, "/api/wireguard/client/search_client", "name", NOMBRE_CLIENTE)
    if existente and fichero.exists():
        print("peer del cliente ya existente")
        return json.loads(fichero.read_text(encoding="utf-8"))

    if existente:
        # Sin la clave privada guardada el peer es inservible: se rehace.
        api.post(f"/api/wireguard/client/del_client/{existente['uuid']}")
        print("peer anterior descartado (no conservabamos su clave privada)")

    claves = api.get("/api/wireguard/server/key_pair")
    psk = api.get("/api/wireguard/client/psk")
    payload = {
        "client": {
            "enabled": "1",
            "name": NOMBRE_CLIENTE,
            "pubkey": claves["pubkey"],
            "psk": psk["psk"],
            "tunneladdress": IP_CLIENTE,
            "serveraddress": "",
            "serverport": "",
            "keepalive": "25",
            "servers": uuid_servidor,
        }
    }
    api.exigir_ok(api.post("/api/wireguard/client/add_client", payload), "crear peer")
    datos = {"privkey": claves["privkey"], "pubkey": claves["pubkey"], "psk": psk["psk"]}
    DESTINO.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(fichero, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(datos, fh)
    print(f"peer creado; material del cliente en {fichero}")
    return datos


def config_cliente(datos: dict, pub_servidor: str, ip_publica: str) -> str:
    """Genera el wg0.conf del cliente: solo enruta MGMT por el tunel."""
    return (
        "[Interface]\n"
        f"PrivateKey = {datos['privkey']}\n"
        f"Address = {IP_CLIENTE}\n\n"
        "[Peer]\n"
        f"PublicKey = {pub_servidor}\n"
        f"PresharedKey = {datos['psk']}\n"
        f"Endpoint = {ip_publica}:{PUERTO}\n"
        # Solo MGMT: la VPN no es un tunel total, es un acceso acotado.
        f"AllowedIPs = {RED_MGMT}\n"
        "PersistentKeepalive = 25\n"
    )


def main() -> int:
    api = Opnsense()
    ip_publica = api.url.split("//")[1].split(":")[0]

    uuid_servidor, pub_servidor = asegurar_servidor(api, ip_publica)
    datos = asegurar_cliente(api, uuid_servidor)

    api.post("/api/wireguard/general/set", {"general": {"enabled": "1"}})
    api.post("/api/wireguard/service/reconfigure")
    estado = api.get("/api/wireguard/service/status").get("status", "?")
    print(f"servicio WireGuard: {estado}")

    destino = DESTINO / f"{NOMBRE_CLIENTE}-wg0.conf"
    fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(config_cliente(datos, pub_servidor, ip_publica))
    print(f"configuracion del cliente escrita en {destino}")
    print("recuerda: apply.py debe crear la regla vpn-to-mgmt sobre la interfaz wireguard")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError as exc:
        print(f"ERROR de API: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
