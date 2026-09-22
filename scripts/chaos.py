#!/usr/bin/env python3
"""`make chaos`: rompe la politica a proposito y exige que la suite lo detecte.

Inserta una regla any->any en el segmento de invitados -el mas restringido de
la matriz-, ejecuta la suite y comprueba que se pone en ROJO. Despues la
retira y vuelve a comprobar que queda en verde.

Una suite que solo pasa cuando todo esta bien no demuestra nada: hay que ver
que tambien falla cuando debe.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "opnsense"))
from api import Opnsense  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DESCRIPCION = "CAOS: regla any-any insertada a proposito por make chaos"
SEGMENTO_ROTO = "opt6"  # GUEST: en la matriz no puede alcanzar nada interno


def suite() -> bool:
    """Ejecuta la matriz. True si pasa."""
    proc = subprocess.run(
        [str(REPO / ".venv/bin/pytest"), "tests/test_connectivity.py", "-m", "lab", "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    ultima = [ln for ln in proc.stdout.splitlines() if ln.strip()][-1:]
    print(f"      -> {ultima[0].strip() if ultima else 'sin salida'}")
    return proc.returncode == 0


def main() -> int:
    api = Opnsense()

    print("1. la suite debe estar en VERDE antes de romper nada")
    if not suite():
        print("ABORTADO: la suite ya fallaba antes del caos", file=sys.stderr)
        return 2

    print(f"\n2. insertando regla any->any en {SEGMENTO_ROTO} (GUEST)")
    creada = api.post(
        "/api/firewall/filter/add_rule",
        {
            "rule": {
                "enabled": "1",
                "action": "pass",
                "quick": "1",
                "interface": SEGMENTO_ROTO,
                "direction": "in",
                "ipprotocol": "inet",
                "protocol": "any",
                "source_net": "any",
                "destination_net": "any",
                "description": DESCRIPCION,
            }
        },
    )
    uuid = creada.get("uuid", "")
    api.post("/api/firewall/filter/apply")

    try:
        print("\n3. la suite debe ponerse en ROJO")
        sigue_verde = suite()
    finally:
        print("\n4. retirando la regla de caos")
        if uuid:
            api.post(f"/api/firewall/filter/del_rule/{uuid}")
            api.post("/api/firewall/filter/apply")

    if sigue_verde:
        print("\nFALLO DEL EXPERIMENTO: la suite no detecto la politica rota", file=sys.stderr)
        return 1

    print("\n5. comprobando que el lab vuelve a estar en VERDE")
    if not suite():
        print("ATENCION: la suite no se recupero tras retirar la regla", file=sys.stderr)
        return 1

    print("\nCAOS SUPERADO: la suite detecta una politica rota y se recupera al arreglarla")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
