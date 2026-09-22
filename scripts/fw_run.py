#!/usr/bin/env python3
"""Ejecuta un script en fw01 a traves del wrapper acotado al pool.

El firewall no tiene SSH abierto (ni debe tenerlo): la via es la misma que usa
la suite de tests, `netseg-exec.sh` + `qm guest exec`, con la VMID validada
contra el pool netseg. El script se transporta en base64 para no pelearse con
el entrecomillado de ssh -> sudo -> qm.

Uso:
    fw_run.py fichero.sh            # ejecuta un script local en fw01
    fw_run.py -c "ifconfig vlan01"  # ejecuta un comando suelto
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _ip_local() -> str:
    """IP con la que este nodo sale hacia el hipervisor (para el fetch de vuelta)."""
    destino = subprocess.run(
        ["ssh", "-G", PVE_HOST], capture_output=True, text=True, check=False
    )
    anfitrion = next(
        (ln.split()[1] for ln in destino.stdout.splitlines() if ln.startswith("hostname ")),
        PVE_HOST,
    )
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((anfitrion, 80))
        return s.getsockname()[0]
    finally:
        s.close()

# El hipervisor se referencia por nombre, no por IP: el alias se resuelve en
# ~/.ssh/config del nodo de control (fuera del repo), asi que el direccionamiento
# de cada instalacion no acaba versionado.
PVE_HOST = os.environ.get("NETSEG_PVE", "pve01")
PVE_USER = os.environ.get("NETSEG_PVE_USER", "netseg")
WRAPPER = "/usr/local/sbin/netseg-exec.sh"
FW_VMID = 600
DESTINO_REMOTO = "/tmp/netseg-fw.sh"


def ejecutar(
    argv_remoto: list[str], timeout: int = 300, vmid: int = FW_VMID
) -> tuple[int, str, str]:
    """Lanza argv_remoto dentro del invitado y devuelve (rc, stdout, stderr).

    El entrecomillado importa: ssh aplana el argv remoto en una sola cadena que
    vuelve a pasar por una shell, asi que cada argumento se cita por separado.
    Sin esto, un `;` en el comando se ejecutaria en el hipervisor.
    """
    orden = " ".join(
        shlex.quote(a) for a in ["sudo", "-n", WRAPPER, str(vmid), "--", *argv_remoto]
    )
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{PVE_USER}@{PVE_HOST}", orden],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        return proc.returncode, "", proc.stderr
    try:
        datos = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.returncode, proc.stdout, proc.stderr
    return int(datos.get("exitcode", 1)), datos.get("out-data", ""), datos.get("err-data", "")


def ejecutar_script(texto: str, timeout: int = 300) -> tuple[int, str, str]:
    """Copia el script a fw01 (via base64) y lo ejecuta con sh."""
    b64 = base64.b64encode(texto.encode()).decode()
    orden = (
        f"echo {b64} | openssl base64 -d -A > {DESTINO_REMOTO} && /bin/sh {DESTINO_REMOTO}"
    )
    return ejecutar(["/bin/sh", "-c", orden], timeout=timeout)


def ejecutar_script_largo(texto: str, espera: int = 240) -> tuple[int, str]:
    """Ejecuta un script largo en fw01 SIN mantener abierta la llamada al agente.

    Un `qm guest exec` que tarda mas de la cuenta deja al agente QEMU en un
    estado del que no vuelve (hay que reiniciar el servicio). Por eso el script
    se lanza desacoplado y se consulta despues el registro, que termina con una
    linea centinela.
    """
    centinela = "===NETSEG-FIN==="
    completo = texto.rstrip() + f'\necho "{centinela} rc=$?"\n'

    # Transporte por HTTP y no como argumento del agente: un guest-exec con un
    # argumento de varios KB deja al agente QEMU colgado (hay que reiniciarlo).
    # El comando que se envia queda asi en un par de lineas.
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "netseg.sh").write_text(completo, encoding="utf-8")
        servidor = ThreadingHTTPServer(
            ("0.0.0.0", 0),
            partial(SimpleHTTPRequestHandler, directory=tmp),
        )
        servidor.timeout = 1
        hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
        hilo.start()
        puerto = servidor.server_address[1]
        try:
            origen = _ip_local()
            # doble fork y descriptores redirigidos: si el hijo conserva la
            # salida heredada, el agente se queda esperando el EOF
            lanzar = (
                f"fetch -q -o {DESTINO_REMOTO} http://{origen}:{puerto}/netseg.sh && "
                f"rm -f {DESTINO_REMOTO}.log && "
                f"( /bin/sh {DESTINO_REMOTO} > {DESTINO_REMOTO}.log 2>&1 < /dev/null & ) ; "
                f"echo lanzado"
            )
            rc, _out, err = ejecutar(["/bin/sh", "-c", lanzar], timeout=60)
        finally:
            servidor.shutdown()
            servidor.server_close()
    if rc != 0:
        return rc, err

    for _ in range(espera // 5):
        time.sleep(5)
        _rc, salida, _err = ejecutar(["/bin/cat", f"{DESTINO_REMOTO}.log"], timeout=60)
        if centinela in salida:
            cuerpo, _, cola = salida.partition(centinela)
            codigo = 0 if "rc=0" in cola else 1
            return codigo, cuerpo
    return 1, f"tiempo agotado ({espera}s) esperando al script en fw01"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("fichero", nargs="?", help="script local a ejecutar en fw01")
    p.add_argument("-c", "--comando", help="comando suelto a ejecutar")
    p.add_argument("--vmid", type=int, default=FW_VMID, help="invitado destino (por defecto fw01)")
    args = p.parse_args()

    if args.comando:
        rc, out, err = ejecutar(["/bin/sh", "-c", args.comando], vmid=args.vmid)
    elif args.fichero:
        if args.vmid != FW_VMID:
            p.error("los scripts largos solo estan soportados contra fw01")
        rc, out, err = ejecutar_script(Path(args.fichero).read_text(encoding="utf-8"))
    else:
        p.error("hace falta un fichero o -c")

    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print(err, end="" if err.endswith("\n") else "\n", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
