#!/usr/bin/env python3
"""Valida ``opnsense/flows.yml``: esquema + coherencia referencial.

Comprobaciones mas alla del JSON Schema:
  - ids de allow unicos
  - src/dst apuntan a un segmento, un host o la palabra reservada WAN
  - el segmento de cada host existe y su IP cae dentro del CIDR del segmento
  - los CIDR de los segmentos no se solapan
  - prohibida cualquier clave 'deny' (las denegaciones se DERIVAN, spec §6)
  - aviso (no error) si un allow usa puertos que no estan en probes.ports:
    ese permiso no podra verificarse con la bateria de sondas

Uso: scripts/validate_flows.py opnsense/flows.yml [--schema opnsense/flows.schema.json]
Salida: rc=0 valido (con resumen), rc=1 errores, rc=2 uso/IO.
"""

from __future__ import annotations

import ipaddress
import json
import sys
from pathlib import Path

import jsonschema
import yaml

RESERVED_WAN = "WAN"
RESERVED_FIREWALL = "FIREWALL"


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def schema_errors(flows: dict, schema: dict) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(flows), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "(raiz)"
        errors.append(f"esquema: {where}: {err.message}")
    return errors


def semantic_errors(flows: dict) -> tuple[list[str], list[str]]:
    """Devuelve (errores, avisos)."""
    errors: list[str] = []
    warnings: list[str] = []

    if "deny" in flows:
        errors.append("hay una clave 'deny': las denegaciones se derivan, no se declaran")

    segments = flows.get("segments", {}) or {}
    hosts = flows.get("hosts", {}) or {}
    probes = flows.get("probes", {}) or {}
    allows = flows.get("allow", []) or []

    # CIDRs validos y sin solapes
    nets: dict[str, ipaddress.IPv4Network] = {}
    for name, seg in segments.items():
        try:
            nets[name] = ipaddress.ip_network(seg["cidr"])
        except ValueError as exc:
            errors.append(f"segmento {name}: cidr invalido ({exc})")
    names = sorted(nets)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if nets[a].overlaps(nets[b]):
                errors.append(f"segmentos {a} y {b}: los CIDR se solapan")

    # hosts coherentes con su segmento
    for hname, host in hosts.items():
        seg = host.get("segment")
        if seg == RESERVED_WAN:
            errors.append(f"host {hname}: WAN no es un segmento asignable")
            continue
        if seg not in segments:
            errors.append(f"host {hname}: segmento inexistente '{seg}'")
            continue
        try:
            ip = ipaddress.ip_address(host["ip"])
        except ValueError as exc:
            errors.append(f"host {hname}: ip invalida ({exc})")
            continue
        if seg in nets and ip not in nets[seg]:
            errors.append(f"host {hname}: {ip} no pertenece a {seg} ({nets[seg]})")

    # allows: ids unicos y endpoints resolubles
    valid_endpoints = set(segments) | set(hosts) | {RESERVED_WAN, RESERVED_FIREWALL}
    seen_ids: set[str] = set()
    probe_ports = set(probes.get("ports", []))
    for entry in allows:
        eid = entry.get("id", "(sin id)")
        if eid in seen_ids:
            errors.append(f"allow {eid}: id duplicado")
        seen_ids.add(eid)
        origenes = entry["src"] if isinstance(entry.get("src"), list) else [entry.get("src")]
        for value in [*origenes, entry.get("dst")]:
            if value not in valid_endpoints:
                errors.append(
                    f"allow {eid}: '{value}' no es segmento, host, WAN ni FIREWALL"
                )
        if entry.get("dst") in origenes:
            errors.append(f"allow {eid}: src y dst identicos")
        if entry.get("src") == RESERVED_FIREWALL:
            errors.append(f"allow {eid}: FIREWALL solo puede ser destino")
        unprobed = [p for p in entry.get("ports", []) if p not in probe_ports]
        if unprobed:
            warnings.append(
                f"allow {eid}: puertos {unprobed} no estan en probes.ports; "
                "ese permiso no se verificara con la bateria de sondas"
            )

    return errors, warnings


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    flows_path = Path(argv[0])
    schema_path = Path("opnsense/flows.schema.json")
    if "--schema" in argv:
        schema_path = Path(argv[argv.index("--schema") + 1])
    if not schema_path.is_absolute() and not schema_path.exists():
        # permite ejecutarlo desde cualquier directorio del repo
        schema_path = flows_path.parent / "flows.schema.json"

    try:
        flows = load_yaml(flows_path)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        print(f"validate_flows: no puedo cargar la entrada: {exc}", file=sys.stderr)
        return 2

    errors = schema_errors(flows, schema)
    sem_errors, warnings = semantic_errors(flows)
    errors.extend(sem_errors)

    for w in warnings:
        print(f"AVISO: {w}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        print(f"validate_flows: {len(errors)} error(es)", file=sys.stderr)
        return 1

    n_ports = len(flows["probes"]["ports"]) + (1 if flows["probes"].get("icmp") else 0)
    print(
        f"flows.yml valido: {len(flows['segments'])} segmentos, {len(flows['hosts'])} hosts, "
        f"{len(flows['allow'])} allows, {n_ports} sondas por par, {len(warnings)} aviso(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
