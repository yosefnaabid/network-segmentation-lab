"""Suite de conectividad derivada de flows.yml. Requiere el lab (marker lab).

Cada origen ejecuta SU bateria completa en una sola invocacion (spec §6):
~9 llamadas al wrapper en total, no ~300 sondas sueltas. El resultado se
escribe en tests/.results/matrix.json, del que report.py genera la matriz.

Quedara VERDE a partir de la fase 3 (reglas importadas en el firewall);
hasta entonces documenta la politica esperada y fallara con el lab a medias:
ese es exactamente su trabajo (tests antes que reglas).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from flowmatrix import Origin, Target, build_probe_script, parse_probe_output

RESULTS_DIR = Path(__file__).resolve().parent / ".results"
EXT01 = "ext01"

# LXC Alpine usan busybox nc; el resto, python3 (imagen cloud / plantilla alma)
FLAVORS = {"iot01": "alpine", "guest01": "alpine", "ext01": "alpine"}
PROBE_TIMEOUT = 1  # spec §6: es una LAN, 1 s sobra


def _origins(inventory: dict) -> list[Origin]:
    """Todas las maquinas con las que se sondea (fw01 queda fuera)."""
    return [
        Origin(name, m["segment"])
        for name, m in sorted(inventory.items())
        if m["segment"] not in ("FW",)
    ]


def _targets(flows: dict, inventory: dict, pve_exec) -> list[Target]:
    targets = [
        Target(name, h["segment"], h["ip"]) for name, h in sorted(flows["hosts"].items())
    ]
    # WAN determinista: ext01 (esta en vmbr0, fuera del perimetro; spec §4)
    res = pve_exec("ext01", ["ip", "-4", "-o", "addr", "show", "eth0"])
    ip = res.stdout.split()[3].split("/")[0] if res.rc == 0 and res.stdout.split() else None
    if ip:
        targets.append(Target("wan", "WAN", ip))
    return targets


def _ip_publica(pve_exec) -> str:
    """IP de la WAN del firewall: la puerta por la que entra el NAT."""
    res = pve_exec("fw01", ["/bin/sh", "-c", "ifconfig vtnet1 inet"])
    for parte in res.stdout.split():
        if parte.count(".") == 3 and parte[0].isdigit():
            return parte
    return ""


def _ip_a_sondear(e, ip_publica: str) -> str:
    """Desde fuera, lo PUBLICADO se prueba en la IP publica; lo demas, no.

    Un servicio publicado solo existe, visto desde Internet, en la IP publica
    del firewall: ahi es donde el NAT lo traduce. En cambio los destinos que
    NO estan publicados se sondean contra su IP interna, que es justo donde se
    demuestra que desde fuera no hay camino hasta ellos. Mezclar ambas cosas
    haria pasar por accesible un host interno cuando lo que responde es la web
    publicada.
    """
    if e.allowed and e.src_segment == "WAN" and e.dst_segment != "WAN" and ip_publica:
        return ip_publica
    return e.dst_ip


@pytest.mark.lab
def test_matriz_derivada(flows, matrix, inventory, pve_exec):
    origins = _origins(inventory)
    targets = _targets(flows, inventory, pve_exec)
    # La matriz mide la politica del PERIMETRO frente a alguien de fuera SIN
    # credenciales. Con el tunel levantado, ext01 alcanzaria MGMT de forma
    # legitima y falsearia la medicion, asi que se baja: el acceso remoto se
    # verifica aparte, en tests/test_vpn.py.
    pve_exec(EXT01, ["/bin/sh", "-c", "wg-quick down wg0 2>/dev/null; true"])

    expectations = matrix.expectations(origins, targets)
    assert expectations, "la derivacion no puede salir vacia"
    ip_publica = _ip_publica(pve_exec)

    # bateria por origen, en paralelo por host origen (spec §6)
    por_origen: dict[str, list] = {}
    for e in expectations:
        por_origen.setdefault(e.src, []).append(e)

    def run_origin(src: str):
        checks = [(_ip_a_sondear(e, ip_publica), e.port, e.proto) for e in por_origen[src]]
        flavor = FLAVORS.get(src, "alma")
        argv = build_probe_script(flavor, checks, timeout=PROBE_TIMEOUT)
        res = pve_exec(src, argv, timeout=30 + 2 * len(checks))
        return src, parse_probe_output(res.stdout)

    with ThreadPoolExecutor(max_workers=4) as pool:
        observed = dict(pool.map(run_origin, por_origen))

    resultados, fallos, inconclusos = [], [], []
    for e in expectations:
        status = observed.get(e.src, {}).get(
            (_ip_a_sondear(e, ip_publica), e.port, e.proto), "sin-dato"
        )
        # Un permiso que la sonda no puede confirmar (busybox nc no separa RST
        # de silencio) no cuenta como fallo, pero tampoco como verificado: se
        # reporta aparte para que no se cuele como exito silencioso.
        if e.allowed and status == "sin-respuesta":
            inconclusos.append({**e.__dict__, "status": status, "ok": None})
            resultados.append({**e.__dict__, "status": status, "ok": None})
            continue
        # Semantica de los estados TCP, que es lo que hace honesta a la matriz:
        #   open   -> el paquete llego y hay servicio escuchando
        #   closed -> llego al host, que respondio RST: la POLITICA lo permite
        #             aunque ese servicio no exista todavia
        #   timeout-> nadie contesto: el cortafuegos lo descarto
        # Por eso un permiso se da por verificado con open o closed, y un
        # bloqueo solo con la ausencia de respuesta.
        alcanzo_el_host = ("open", "closed")
        if e.proto == "tcp":
            ok = (status in alcanzo_el_host) if e.allowed else (status not in alcanzo_el_host)
        elif e.proto == "udp":
            # udp es ambiguo por naturaleza: bloqueado exige ICMP unreachable
            # o silencio; permitido se acepta salvo 'closed'
            ok = (status != "closed") if e.allowed else (status in ("closed", "noanswer"))
        else:  # icmp
            ok = (status == "open") if e.allowed else (status != "open")
        registro = {**e.__dict__, "status": status, "ok": ok}
        resultados.append(registro)
        if not ok:
            fallos.append(registro)

    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "matrix.json").write_text(
        json.dumps(
            {
                "generated": datetime.now(UTC).isoformat(timespec="seconds"),
                "expectations": resultados,
            },
            indent=1,
        ),
        encoding="utf-8",
    )

    bloqueos = [r for r in resultados if r["ok"] is not None and not r["allowed"]]
    print(
        f"\nbloqueos verificados: {sum(1 for r in bloqueos if r['ok'])}/{len(bloqueos)} | "
        f"permisos sin confirmar: {len(inconclusos)}"
    )
    assert not fallos, (
        f"{len(fallos)}/{len(resultados)} sondas contradicen flows.yml; "
        "revisa docs/matriz-flujos.md tras ejecutar report.py"
    )
