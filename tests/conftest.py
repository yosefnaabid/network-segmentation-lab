"""Fixtures del harness: ejecucion FUERA DE BANDA en los invitados.

El runner (ctrl01) jamas entra por SSH a la red bajo prueba: habla con el
hipervisor via la cuenta 'netseg' y el wrapper sudo netseg-exec.sh, que valida
la VMID contra el pool y despacha a `qm guest exec` (VMs, JSON) o `pct exec`
(LXC, salida cruda). Ver spec §8 y ADR 0007.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(REPO / "scripts"))

# Alias de ssh, no IP: el direccionamiento concreto vive en ~/.ssh/config del
# nodo de control y no se versiona.
PVE_HOST = os.environ.get("NETSEG_PVE", "pve01")
PVE_USER = os.environ.get("NETSEG_PVE_USER", "netseg")
WRAPPER = "/usr/local/sbin/netseg-exec.sh"
RESULTS_DIR = TESTS_DIR / ".results"

# En las VMs, todo comando pasa por el entrypoint del agente: bajo SELinux
# enforcing los hijos de guest-exec quedan confinados en virt_qemu_ga_t (sin
# red, sin ver binarios con dominio propio); este entrypoint, etiquetado
# virt_qemu_ga_unconfined_exec_t en la imagen, los transiciona a un dominio
# sin confinar (incidencia #9). En LXC no aplica: pct exec entra directo.
QEMU_RUNNER = "/usr/local/sbin/netseg-agent-runner"


@dataclass
class ExecResult:
    rc: int
    stdout: str
    stderr: str


def _wrapper_argv(vmid: int, cmd: list[str]) -> list[str]:
    """argv local de ssh; el comando remoto va con cada arg entrecomillado
    (ssh aplana los argv remotos en una unica cadena de shell)."""
    remote = " ".join(shlex.quote(a) for a in ["sudo", "-n", WRAPPER, str(vmid), "--", *cmd])
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        f"{PVE_USER}@{PVE_HOST}",
        remote,
    ]


def guest_exec(machine: dict, cmd: list[str], timeout: int = 90) -> ExecResult:
    """Ejecuta cmd dentro del invitado descrito por machine {vmid,type}."""
    # El entrypoint solo existe en las VMs AlmaLinux del lab; fw01 es FreeBSD
    # y no lo necesita (su agente no corre bajo la politica de RHEL).
    if machine["type"] == "qemu" and machine.get("segment") != "FW":
        cmd = [QEMU_RUNNER, *cmd]
    proc = subprocess.run(
        _wrapper_argv(machine["vmid"], cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if machine["type"] == "qemu":
        # qm guest exec envuelve el resultado en JSON:
        # {"exitcode": N, "out-data": "...", "err-data": "..."}
        try:
            data = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            return ExecResult(proc.returncode or 1, proc.stdout, proc.stderr)
        return ExecResult(
            int(data.get("exitcode", 1)), data.get("out-data", ""), data.get("err-data", "")
        )
    return ExecResult(proc.returncode, proc.stdout, proc.stderr)


@pytest.fixture(scope="session")
def flows() -> dict:
    return yaml.safe_load((REPO / "opnsense" / "flows.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def matrix(flows):
    from flowmatrix import FlowMatrix

    return FlowMatrix(flows)


@pytest.fixture(scope="session")
def inventory() -> dict:
    """Mapa nombre -> {vmid,type,segment,ip} desde el output de terraform."""
    out = subprocess.run(
        ["terraform", "output", "-json", "machines"],
        cwd=REPO / "terraform",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


@pytest.fixture(scope="session")
def pve_exec(inventory):
    """Callable(nombre, cmd[, timeout]) -> ExecResult, via el wrapper del pool."""

    def _run(name: str, cmd: list[str], timeout: int = 90) -> ExecResult:
        return guest_exec(inventory[name], cmd, timeout)

    return _run
