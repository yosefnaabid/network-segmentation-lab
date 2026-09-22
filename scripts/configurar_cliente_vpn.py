#!/usr/bin/env python3
"""Configura el tunel WireGuard dentro de ext01, el cliente de fuera del lab.

ext01 vive en vmbr0, fuera del perimetro: es el unico sitio desde el que se
puede comprobar de verdad que la VPN llega a MGMT y a nada mas (spec §4).
La configuracion se transporta por el mismo wrapper acotado al pool que usa
la suite, sin abrir SSH a los invitados.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fw_run import ejecutar  # noqa: E402

EXT01 = 601
CONF = Path.home() / ".netseg-vpn" / "ext01-wg0.conf"


def main() -> int:
    if not CONF.exists():
        print(f"FALLO: no existe {CONF}. Ejecuta antes opnsense/setup_vpn.py", file=sys.stderr)
        return 1

    rc, out, err = ejecutar(
        ["/bin/sh", "-c", "command -v wg-quick || apk add --no-cache wireguard-tools"],
        vmid=EXT01,
        timeout=300,
    )
    if rc != 0:
        print(f"FALLO instalando wireguard-tools: {err.strip()[:200]}", file=sys.stderr)
        return 1

    b64 = base64.b64encode(CONF.read_text(encoding="utf-8").encode()).decode()
    orden = (
        f"mkdir -p /etc/wireguard && echo {b64} | base64 -d > /etc/wireguard/wg0.conf && "
        "chmod 600 /etc/wireguard/wg0.conf && "
        "(wg-quick down wg0 2>/dev/null; wg-quick up wg0) && wg show wg0"
    )
    rc, out, err = ejecutar(["/bin/sh", "-c", orden], vmid=EXT01, timeout=180)
    print(out.strip() or err.strip()[:300])
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
