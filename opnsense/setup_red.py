#!/usr/bin/env python3
"""Fase 2: crea las VLAN del laboratorio en fw01 y las deja asignadas con IP.

Idempotente: ejecutarlo dos veces no duplica nada.

Reparto de responsabilidades:
  - Las VLAN se crean por API (modelo Interfaces/Vlan).
  - La ASIGNACION de cada VLAN como interfaz de OPNsense no tiene API MVC en
    25.7 (la pagina sigue siendo legacy), asi que se hace escribiendo en
    config.xml con la propia API de configuracion de OPNsense (write_config),
    partiendo del esquema REAL exportado del firewall, no inventado.

Los segmentos y sus direcciones salen de opnsense/flows.yml: una sola fuente
de verdad tambien para el direccionamiento.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api import ApiError, Opnsense  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FLOWS = REPO / "opnsense" / "flows.yml"
PADRE = "vtnet0"  # el trunk hacia vmbr2


def segmentos() -> list[dict]:
    """Segmentos con VLAN, ordenados por tag, desde flows.yml."""
    datos = yaml.safe_load(FLOWS.read_text(encoding="utf-8"))
    salida = []
    for nombre, seg in datos["segments"].items():
        if "vlan" not in seg:
            continue  # VPN no es una VLAN fisica
        red = ipaddress.ip_network(seg["cidr"])
        salida.append(
            {
                "nombre": nombre,
                "tag": int(seg["vlan"]),
                "cidr": seg["cidr"],
                "ip": str(next(red.hosts())),  # .1 de cada red: el firewall
                "mascara": red.prefixlen,
            }
        )
    return sorted(salida, key=lambda s: s["tag"])


def asegurar_vlans(api: Opnsense, segs: list[dict]) -> dict[int, str]:
    """Crea las VLAN que falten. Devuelve {tag: dispositivo}."""
    existentes = {}
    for fila in api.get("/api/interfaces/vlan_settings/search_item").get("rows", []):
        existentes[str(fila.get("tag"))] = fila

    creadas = 0
    for seg in segs:
        if str(seg["tag"]) in existentes:
            continue
        payload = {
            "vlan": {
                "if": PADRE,
                "tag": str(seg["tag"]),
                "pcp": "0",
                "proto": "",
                "descr": f"VLAN {seg['tag']} {seg['nombre']}",
            }
        }
        api.exigir_ok(
            api.post("/api/interfaces/vlan_settings/add_item", payload),
            f"crear VLAN {seg['tag']}",
        )
        creadas += 1

    if creadas:
        api.post("/api/interfaces/vlan_settings/reconfigure")

    dispositivos = {}
    for fila in api.get("/api/interfaces/vlan_settings/search_item").get("rows", []):
        # OJO: cuando la VLAN ya esta asignada, el controlador DECORA esta
        # columna con el nombre de la interfaz ("vlan01 [USERS]"). Si se usa
        # tal cual acaba escrita asi en config.xml y el dispositivo deja de
        # existir para OPNsense (incidencia #15).
        crudo = fila.get("vlanif") or fila.get("device", "")
        dispositivos[int(fila["tag"])] = crudo.split(" [")[0].strip()
    print(f"VLANs: {creadas} creadas, {len(dispositivos)} en total")
    for tag, dev in sorted(dispositivos.items()):
        print(f"   tag {tag:>2} -> {dev}")
    return dispositivos


def plan_asignacion(segs: list[dict], dispositivos: dict[int, str]) -> list[dict]:
    """Empareja cada segmento con su dispositivo VLAN y su nombre optN."""
    plan = []
    for indice, seg in enumerate(segs, start=1):
        dev = dispositivos.get(seg["tag"])
        if not dev:
            raise SystemExit(f"FALLO: la VLAN {seg['tag']} no tiene dispositivo")
        plan.append(
            {
                "interfaz": f"opt{indice}",
                "descr": seg["nombre"],
                "if": dev,
                "ipaddr": seg["ip"],
                "subnet": str(seg["mascara"]),
            }
        )
    return plan


PHP_ASIGNAR = """<?php
require_once("config.inc");
require_once("util.inc");
global $config;

$plan = json_decode(<<<'JSONPLAN'
%(plan)s
JSONPLAN, true);

$cambios = 0;
foreach ($plan as $p) {
    $k = $p["interfaz"];
    $actual = isset($config["interfaces"][$k]) ? $config["interfaces"][$k] : array();
    $igual = isset($actual["if"]) && $actual["if"] === $p["if"]
          && isset($actual["descr"]) && $actual["descr"] === $p["descr"]
          && isset($actual["ipaddr"]) && $actual["ipaddr"] === $p["ipaddr"]
          && isset($actual["subnet"]) && $actual["subnet"] === $p["subnet"]
          && isset($actual["enable"]);
    if ($igual) { continue; }
    $config["interfaces"][$k] = array(
        "enable"    => "1",
        "if"        => $p["if"],
        "descr"     => $p["descr"],
        "ipaddr"    => $p["ipaddr"],
        "subnet"    => $p["subnet"],
        "ipaddrv6"  => "",
        "subnetv6"  => "",
        "media"     => "",
        "mediaopt"  => "",
        "gateway"   => "",
        "gatewayv6" => "",
    );
    $cambios++;
    echo "asignada {$k} = {$p['descr']} ({$p['if']}) {$p['ipaddr']}/{$p['subnet']}\\n";
}

if ($cambios > 0) {
    write_config("netseg: asignar interfaces VLAN del laboratorio");
}
echo "cambios={$cambios}\\n";
"""

SH_ASIGNAR = """#!/bin/sh
# Asignacion de las interfaces VLAN del lab. Se ejecuta DENTRO de fw01.
set -eu
cp -f /conf/config.xml /root/config-antes-asignacion.xml
cat > /tmp/netseg-asignar.php <<'PHPEOF'
%(php)s
PHPEOF
/usr/local/bin/php /tmp/netseg-asignar.php
echo "--- interfaces en config.xml:"
grep -oE "<opt[0-9]+>" /conf/config.xml | sort -u | tr '\\n' ' '; echo
"""


def asignar_interfaces(plan: list[dict]) -> None:
    """Escribe las entradas <optN> en config.xml usando write_config()."""
    sys.path.insert(0, str(REPO / "scripts"))
    from fw_run import ejecutar_script_largo  # noqa: PLC0415

    php = PHP_ASIGNAR % {"plan": json.dumps(plan, indent=1)}
    guion = SH_ASIGNAR % {"php": php}
    rc, salida = ejecutar_script_largo(guion)
    print(salida.rstrip())
    if rc != 0:
        raise SystemExit(f"FALLO al asignar interfaces (rc={rc})")


def aplicar_interfaces(api: Opnsense, plan: list[dict]) -> None:
    """Aplica cada interfaz asignada (equivale a Guardar en la GUI)."""
    for entrada in plan:
        api.post(f"/api/interfaces/overview/reload_interface/{entrada['interfaz']}")
    print(f"aplicadas {len(plan)} interfaces")


def verificar(api: Opnsense, plan: list[dict]) -> bool:
    """Comprueba contra el firewall que cada interfaz existe con su IP."""
    vistas = {}
    for fila in api.get("/api/interfaces/overview/export"):
        ident = fila.get("identifier")
        if ident:
            vistas[ident] = (fila.get("device"), (fila.get("addr4") or ""))
    ok = True
    print("\nverificacion contra el firewall:")
    for entrada in plan:
        dev, ip = vistas.get(entrada["interfaz"], ("", ""))
        esperado = f"{entrada['ipaddr']}/{entrada['subnet']}"
        bien = dev == entrada["if"] and ip.startswith(entrada["ipaddr"])
        ok = ok and bien
        marca = "OK " if bien else "MAL"
        print(
            f"   {marca} {entrada['interfaz']:<5} {entrada['descr']:<8} "
            f"{dev or '(sin device)':<12} {ip or '(sin ip)':<18} esperado {esperado}"
        )
    return ok


# Rangos de DHCP del spec §4. No van en flows.yml porque ese fichero es
# la fuente de verdad de la matriz de flujos, no del direccionamiento.
# Los segmentos que no aparecen aqui son de direccionamiento estatico.
POOLS_DHCP = {
    "USERS": ("10.10.10.100", "10.10.10.200"),
    "IOT": ("10.10.40.50", "10.10.40.150"),
    "GUEST": ("10.10.99.10", "10.10.99.250"),
}


def asegurar_dhcp(api: Opnsense, segs: list[dict], plan: list[dict]) -> None:
    """Habilita Kea DHCPv4 y crea una subred por segmento con pool."""
    por_nombre = {e["descr"]: e for e in plan}
    con_dhcp = [s for s in segs if s["nombre"] in POOLS_DHCP]
    interfaces = [por_nombre[s["nombre"]]["interfaz"] for s in con_dhcp]

    actual = api.get("/api/kea/dhcpv4/get")["dhcpv4"]
    general = {
        "enabled": "1",
        "interfaces": ",".join(interfaces),
        "valid_lifetime": actual["general"].get("valid_lifetime") or "4000",
        "fwrules": "0",  # las reglas las gobierna flows.yml, no Kea
    }
    api.exigir_ok(api.post("/api/kea/dhcpv4/set", {"dhcpv4": {"general": general}}), "activar Kea")
    print(f"Kea activado en: {', '.join(interfaces)}")

    existentes = {
        fila.get("subnet") for fila in api.get("/api/kea/dhcpv4/search_subnet").get("rows", [])
    }
    creadas = 0
    for seg in con_dhcp:
        if seg["cidr"] in existentes:
            continue
        inicio, fin = POOLS_DHCP[seg["nombre"]]
        payload = {
            "subnet4": {
                "subnet": seg["cidr"],
                "description": seg["nombre"],
                "pools": f"{inicio}-{fin}",
                "option_data": {
                    "routers": seg["ip"],
                    "domain_name_servers": seg["ip"],
                    "domain_name": "lab.interno",
                    "ntp_servers": seg["ip"],
                },
            }
        }
        api.exigir_ok(
            api.post("/api/kea/dhcpv4/add_subnet", payload), f"crear subred {seg['cidr']}"
        )
        creadas += 1
        print(f"   subred {seg['cidr']:<16} pool {inicio}-{fin}")

    api.post("/api/kea/service/reconfigure")
    estado = api.get("/api/kea/service/status").get("status", "?")
    print(f"subredes: {creadas} creadas, servicio Kea: {estado}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--solo-plan", action="store_true", help="no toca nada, solo muestra el plan")
    args = p.parse_args()

    segs = segmentos()
    print(f"segmentos con VLAN en flows.yml: {[s['nombre'] for s in segs]}")

    api = Opnsense()
    dispositivos = {} if args.solo_plan else asegurar_vlans(api, segs)
    if args.solo_plan:
        for seg in segs:
            print(f"   {seg['nombre']:<8} vlan {seg['tag']:>2} -> {seg['ip']}/{seg['mascara']}")
        return 0

    plan = plan_asignacion(segs, dispositivos)
    print("\nplan de asignacion:")
    for e in plan:
        print(f"   {e['interfaz']} = {e['descr']:<8} {e['if']:<12} {e['ipaddr']}/{e['subnet']}")

    print("\nasignando en config.xml (write_config)...")
    asignar_interfaces(plan)

    print("\naplicando las interfaces...")
    aplicar_interfaces(api, plan)

    if not verificar(api, plan):
        print("\nHAY INTERFACES QUE NO QUEDARON COMO SE ESPERABA", file=sys.stderr)
        return 1
    print("\nlas 6 interfaces VLAN estan asignadas, con IP y activas")

    print("\nconfigurando DHCP (Kea)...")
    asegurar_dhcp(api, segs, plan)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError as exc:
        print(f"ERROR de API: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
