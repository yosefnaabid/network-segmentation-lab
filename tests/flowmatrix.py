"""Derivacion de la matriz de flujos: producto cartesiano menos lo permitido.

La pieza central del proyecto (spec §6): ``flows.yml`` solo declara *allows*;
este modulo genera la lista COMPLETA de expectativas (permitido/bloqueado)
para cada (origen, host destino, sonda). Un bloqueo no listado aqui no
existe: la exhaustividad sale por construccion, no por enumeracion manual.

Tambien genera la bateria de sondas por host origen (un unico comando por
origen, timeout de 1 s, TCP connect -nunca SYN scan: los LXC sin privilegios
no tienen sockets raw-) y parsea su salida.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

import yaml

WAN = "WAN"


@dataclass(frozen=True)
class Origin:
    """Maquina desde la que se sondea."""

    name: str
    segment: str


@dataclass(frozen=True)
class Target:
    """Destino sondeable: host fijo de flows.yml o el pseudo-destino WAN."""

    name: str
    segment: str
    ip: str


@dataclass(frozen=True)
class Expectation:
    src: str
    src_segment: str
    dst: str
    dst_segment: str
    dst_ip: str
    port: int  # 0 para icmp
    proto: str  # tcp | udp | icmp
    allowed: bool
    allow_id: str | None


def load_flows(path: str | Path) -> dict:
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def parse_portproto(s: str) -> tuple[int, str]:
    port, proto = s.split("/", 1)
    return int(port), proto


class FlowMatrix:
    """Decide permitido/bloqueado para cada tupla y deriva las expectativas."""

    def __init__(self, flows: dict):
        self.flows = flows
        self.segments: dict = flows["segments"]
        self.hosts: dict = flows["hosts"]
        self.allows: list[dict] = flows["allow"]
        self.probe_ports = [parse_portproto(p) for p in flows["probes"]["ports"]]
        self.probe_icmp = bool(flows["probes"].get("icmp", False))

    # -- resolucion de coincidencias -------------------------------------
    def _src_matches(self, allow_src, origin: Origin) -> bool:
        """src puede ser un nombre o una lista de segmentos."""
        candidatos = allow_src if isinstance(allow_src, list) else [allow_src]
        return any(c in (origin.name, origin.segment) for c in candidatos)

    def _dst_matches(self, allow_dst: str, target: Target) -> bool:
        # FIREWALL (servicios del propio cortafuegos) no es un destino sondeado:
        # la matriz mide trafico entre maquinas, no hacia el router.
        return allow_dst in (target.name, target.segment)

    def decide(
        self, origin: Origin, target: Target, port: int, proto: str
    ) -> tuple[bool, str | None]:
        """True/False + id del allow que lo permite (si alguno)."""
        for entry in self.allows:
            if not self._src_matches(entry["src"], origin):
                continue
            if not self._dst_matches(entry["dst"], target):
                continue
            if proto == "icmp":
                if entry.get("icmp", False):
                    return True, entry["id"]
                continue
            if f"{port}/{proto}" in entry["ports"]:
                return True, entry["id"]
        return False, None

    # -- derivacion completa ---------------------------------------------
    def expectations(self, origins: list[Origin], targets: list[Target]) -> list[Expectation]:
        """Producto cartesiano origen x destino x sonda, menos lo sin sentido.

        Se excluyen los pares del MISMO segmento: ese trafico no cruza el
        firewall (misma L2), asi que la matriz perimetral no puede afirmarlo
        ni negarlo. El aislamiento interno de GUEST se verifica aparte.
        """
        out: list[Expectation] = []
        for o in origins:
            for t in targets:
                if o.name == t.name or o.segment == t.segment:
                    continue
                probes: list[tuple[int, str]] = list(self.probe_ports)
                if self.probe_icmp:
                    probes.append((0, "icmp"))
                for port, proto in probes:
                    allowed, aid = self.decide(o, t, port, proto)
                    out.append(
                        Expectation(
                            o.name, o.segment, t.name, t.segment, t.ip, port, proto, allowed, aid
                        )
                    )
        return out

    def summary(self, expectations: list[Expectation]) -> dict:
        allowed = sum(1 for e in expectations if e.allowed)
        return {
            "total": len(expectations),
            "permitidos": allowed,
            "bloqueados": len(expectations) - allowed,
        }


# ---------------------------------------------------------------------------
# Bateria de sondas por origen. Formato de salida, una linea por sonda:
#   R <ip> <puerto> <proto> <estado>
# estados tcp: open | closed | timeout      (closed = RST; timeout = drop)
# estados udp: closed | noanswer            (closed = ICMP unreachable)
# estados icmp: open | timeout | error
# ---------------------------------------------------------------------------

_PY_PROBE = r"""
import socket, subprocess, sys
timeout = float(sys.argv[1])
checks = sys.argv[2:]
for chk in checks:
    ip, port, proto = chk.split(":")
    if proto == "tcp":
        try:
            socket.create_connection((ip, int(port)), timeout=timeout).close()
            st = "open"
        except socket.timeout:
            st = "timeout"
        except OSError:
            st = "closed"
    elif proto == "udp":
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(b"x", (ip, int(port)))
            s.recvfrom(64)
            st = "noanswer"  # respondio un servicio: para la matriz cuenta igual que sin respuesta
        except socket.timeout:
            st = "noanswer"
        except OSError:
            st = "closed"
        finally:
            s.close()
    else:
        rc = subprocess.run(["ping", "-c", "1", "-W", str(int(timeout) or 1), ip],
                            capture_output=True).returncode
        st = "open" if rc == 0 else ("timeout" if rc == 1 else "error")
    print(f"R {ip} {port} {proto} {st}")
"""


def build_probe_script(
    flavor: str, checks: list[tuple[str, int, str]], timeout: int = 1
) -> list[str]:
    """Devuelve el argv COMPLETO a ejecutar en el origen para toda su bateria.

    flavor 'alma'  -> python3 (VMs AlmaLinux y pc01)
    flavor 'alpine'-> shell POSIX con busybox nc/ping (iot01, guest01, ext01)
    """
    if flavor == "alma":
        args = [f"{ip}:{port}:{proto}" for ip, port, proto in checks]
        return ["python3", "-c", _PY_PROBE, str(timeout), *args]

    if flavor == "alpine":
        lines = ["#!/bin/sh"]
        for ip, port, proto in checks:
            ipq, portq = shlex.quote(ip), shlex.quote(str(port))
            if proto == "tcp":
                # busybox nc devuelve el mismo codigo para un RST (cerrado) que
                # para el silencio de un drop, asi que se MIDE EL TIEMPO: un
                # rechazo vuelve al instante y un descarte agota la espera.
                # Por eso aqui el tiempo de espera es mayor que en la sonda de
                # Python: con 1 s no hay margen para distinguirlos en segundos.
                espera = max(timeout, 2)
                lines.append(
                    f"t0=$(date +%s); nc -z -w {espera} {ipq} {portq} 2>/dev/null; rc=$?; "
                    f"t1=$(date +%s); "
                    f"if [ $rc -eq 0 ]; then st=open; "
                    f"elif [ $((t1-t0)) -ge {espera} ]; then st=timeout; else st=closed; fi; "
                    f'echo "R {ip} {port} tcp $st"'
                )
            elif proto == "udp":
                # busybox nc -u con -z no es fiable; enviamos un byte y solo
                # distinguimos 'closed' si nc muere por ICMP unreachable.
                lines.append(
                    f"if printf x | nc -u -w {timeout} {ipq} {portq} >/dev/null 2>&1; "
                    f'then st=noanswer; else st=closed; fi; echo "R {ip} {port} udp $st"'
                )
            else:
                lines.append(
                    f"if ping -c 1 -W {timeout} {ipq} >/dev/null 2>&1; then st=open; "
                    f'else st=timeout; fi; echo "R {ip} 0 icmp $st"'
                )
        return ["/bin/sh", "-c", "\n".join(lines)]

    raise ValueError(f"flavor desconocido: {flavor}")


def parse_probe_output(text: str) -> dict[tuple[str, int, str], str]:
    """Parsea las lineas 'R ip puerto proto estado' de una bateria."""
    results: dict[tuple[str, int, str], str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 5 and parts[0] == "R":
            _, ip, port, proto, status = parts
            results[(ip, int(port), proto)] = status
    return results
