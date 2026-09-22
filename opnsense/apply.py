#!/usr/bin/env python3
"""Aplica en fw01 las reglas derivadas de flows.yml, o comprueba la deriva.

    apply.py            # sincroniza el firewall con flows.yml
    apply.py --check    # no toca nada; sale con rc=1 si hay deriva

Solo gestiona las reglas cuya descripcion empieza por "netseg:": cualquier
regla creada a mano en la GUI se respeta y se reporta aparte. La denegacion
no se escribe nunca: es la politica por defecto (ADR 0002).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api import ApiError, Opnsense  # noqa: E402
from reglas import (  # noqa: E402
    ALIAS_INTERNAS,
    Regla,
    es_gestionada,
    flujos_pendientes,
    generar,
)

REPO = Path(__file__).resolve().parent.parent
FLOWS = REPO / "opnsense" / "flows.yml"


def mapa_interfaces(api: Opnsense, flows: dict) -> dict[str, str]:
    """Segmento -> interfaz de OPNsense, cruzando descripciones con la API."""
    mapa = {}
    for fila in api.get("/api/interfaces/overview/export"):
        ident, descr = fila.get("identifier"), (fila.get("description") or "").upper()
        if ident and descr:
            mapa[descr] = ident
    faltan = [s for s in flows["segments"] if "vlan" in flows["segments"][s] and s not in mapa]
    if faltan:
        raise SystemExit(f"FALLO: sin interfaz asignada para {faltan}. Ejecuta setup_red.py")

    # La VPN no es una VLAN: OPNsense expone el trafico de WireGuard en un
    # grupo de interfaz propio, disponible solo cuando el servicio esta activo.
    if api.get("/api/wireguard/general/get")["general"].get("enabled") == "1":
        mapa["VPN"] = "wireguard"
    return mapa


def asegurar_alias_internas(api: Opnsense, flows: dict) -> None:
    """Mantiene el alias con TODAS las redes del lab, derivado de flows.yml.

    Lo usan las reglas de salida a Internet en modo negado: "a cualquier sitio
    menos aqui dentro". Sin el, permitir navegar abre tambien el trafico hacia
    los demas segmentos.
    """
    redes = sorted(seg["cidr"] for seg in flows["segments"].values())
    contenido = "\n".join(redes)
    payload = {
        "alias": {
            "enabled": "1",
            "name": ALIAS_INTERNAS,
            "type": "network",
            "content": contenido,
            "description": "Redes internas del laboratorio (derivado de flows.yml)",
        }
    }
    existente = next(
        (
            fila
            for fila in api.get("/api/firewall/alias/search_item").get("rows", [])
            if fila.get("name") == ALIAS_INTERNAS
        ),
        None,
    )
    if existente is None:
        api.exigir_ok(api.post("/api/firewall/alias/add_item", payload), "crear alias")
        print(f"alias {ALIAS_INTERNAS} creado con {len(redes)} redes")
    else:
        api.exigir_ok(
            api.post(f"/api/firewall/alias/set_item/{existente['uuid']}", payload),
            "actualizar alias",
        )
    api.post("/api/firewall/alias/reconfigure")


def _modelo_reglas(api: Opnsense) -> dict[str, dict]:
    """Reglas de automatizacion tal y como estan en el firewall: {uuid: regla}.

    Se lee el MODELO y no /search_rule: ese endpoint devuelve 0 filas aunque
    las reglas existan (incidencia #17), asi que la deriva saldria siempre
    falseada como "faltan todas".
    """
    return api.get("/api/firewall/filter/get")["filter"]["rules"]["rule"]


def reglas_vivas(api: Opnsense) -> dict[tuple, str]:
    """Reglas gestionadas que hay ahora en el firewall: {clave: uuid}."""
    vivas = {}
    for uuid, regla in _modelo_reglas(api).items():
        descripcion = regla.get("description", "")
        if not es_gestionada(descripcion):
            continue
        clave = (
            descripcion,
            Opnsense.seleccionado(regla.get("interface")),
            Opnsense.seleccionado(regla.get("protocol")).upper(),
            regla.get("source_net", ""),
            regla.get("destination_net", ""),
            regla.get("destination_port", ""),
            Opnsense.seleccionado(regla.get("action")),
            str(regla.get("destination_not", "0")),
        )
        vivas[clave] = uuid
    return vivas


def ajenas(api: Opnsense) -> list[str]:
    """Reglas de automatizacion NO gestionadas por este repo (no se tocan)."""
    return [
        regla.get("description", "(sin descripcion)")
        for regla in _modelo_reglas(api).values()
        if not es_gestionada(regla.get("description", ""))
    ]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="solo informar de la deriva")
    args = p.parse_args()

    flows = yaml.safe_load(FLOWS.read_text(encoding="utf-8"))
    api = Opnsense()
    interfaces = mapa_interfaces(api, flows)
    if not args.check:
        asegurar_alias_internas(api, flows)

    esperadas: list[Regla] = generar(flows, interfaces)
    esperadas_por_clave = {r.clave(): r for r in esperadas}
    vivas = reglas_vivas(api)

    sobran = [c for c in vivas if c not in esperadas_por_clave]
    faltan = [c for c in esperadas_por_clave if c not in vivas]

    pendientes = flujos_pendientes(flows, interfaces)
    if pendientes:
        print(f"flujos aun sin interfaz (llegan en fases posteriores): {', '.join(pendientes)}")
    print(f"reglas derivadas de flows.yml: {len(esperadas)}")
    print(f"reglas gestionadas en el firewall: {len(vivas)}")
    print(f"faltan por crear: {len(faltan)} | sobran por borrar: {len(sobran)}")
    otras = ajenas(api)
    if otras:
        print(f"reglas ajenas (no se tocan): {len(otras)}")
        for descripcion in otras:
            print(f"   - {descripcion[:80]}")

    if args.check:
        for clave in faltan:
            print(f"   FALTA  {clave[0][:60]} [{clave[1]} {clave[2]} {clave[5]}]")
        for clave in sobran:
            print(f"   SOBRA  {clave[0][:60]} [{clave[1]} {clave[2]} {clave[5]}]")
        if faltan or sobran:
            print("\nDERIVA: el firewall no coincide con flows.yml", file=sys.stderr)
            return 1
        print("\nsin deriva: las reglas vivas coinciden con flows.yml")
        return 0

    for clave in sobran:
        api.exigir_ok(
            api.post(f"/api/firewall/filter/del_rule/{vivas[clave]}"), f"borrar {clave[0]}"
        )
        print(f"   borrada  {clave[0][:70]}")

    for clave in faltan:
        regla = esperadas_por_clave[clave]
        api.exigir_ok(
            api.post("/api/firewall/filter/add_rule", {"rule": regla.como_payload()}),
            f"crear {regla.descripcion}",
        )
        print(f"   creada   {regla.descripcion[:70]}")

    if faltan or sobran:
        api.post("/api/firewall/filter/apply")
        print("\nruleset aplicado")
    else:
        print("\nnada que hacer: el firewall ya coincide con flows.yml")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError as exc:
        print(f"ERROR de API: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
