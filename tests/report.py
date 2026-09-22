#!/usr/bin/env python3
"""Genera docs/matriz-flujos.md desde los resultados de la suite.

La suite (fase 5) deja tests/.results/matrix.json con cada expectativa y su
resultado real. Este script lo convierte en la matriz publicada del README.
El fichero generado NO se edita a mano.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
RESULTS = TESTS_DIR / ".results" / "matrix.json"
OUTPUT = REPO / "docs" / "matriz-flujos.md"


def render_matrix(data: dict) -> str:
    """Renderiza el markdown. Funcion pura: testeable sin lab."""
    exps = data["expectations"]
    ok = [e for e in exps if e["ok"] is True]
    ko = [e for e in exps if e["ok"] is False]
    sin_confirmar = [e for e in exps if e["ok"] is None]
    permitidos = [e for e in exps if e["allowed"]]
    bloqueados = [e for e in exps if not e["allowed"]]

    # celdas por par de segmentos: verificadas / totales
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in exps:
        cells[(e["src_segment"], e["dst_segment"])].append(e)
    srcs = sorted({s for s, _ in cells})
    dsts = sorted({d for _, d in cells})

    lines = [
        "# Matriz de flujos verificada",
        "",
        "**GENERADO por `tests/report.py` — no editar a mano.**",
        "",
        f"- Fecha de generacion: {data.get('generated', 'desconocida')}",
        f"- Expectativas: {len(exps)} ({len(permitidos)} permitidas, {len(bloqueados)} bloqueadas)",
        f"- Verificadas: {len(ok)} · Discrepancias: {len(ko)} · "
        f"Sin confirmar: {len(sin_confirmar)}",
        f"- **Bloqueos verificados: {sum(1 for e in bloqueados if e['ok'])}/{len(bloqueados)}**",
        "",
        "Cada celda: sondas verificadas/total entre ese par de segmentos.",
        "",
    ]

    header = "| Origen \\ Destino | " + " | ".join(dsts) + " |"
    sep = "|---" * (len(dsts) + 1) + "|"
    lines += [header, sep]
    for s in srcs:
        row = [f"| **{s}**"]
        for d in dsts:
            entries = cells.get((s, d))
            if not entries:
                row.append(" —")
            else:
                good = sum(1 for e in entries if e["ok"] is True)
                malas = sum(1 for e in entries if e["ok"] is False)
                mark = "✅" if not malas else "❌"
                row.append(f" {mark} {good}/{len(entries)}")
        lines.append(" |".join(row) + " |")

    if ko:
        lines += ["", "## Discrepancias (politica declarada vs observada)", ""]
        for e in ko:
            esperado = "permitido" if e["allowed"] else "bloqueado"
            lines.append(
                f"- `{e['src']}` → `{e['dst']}:{e['port']}/{e['proto']}` "
                f"esperado **{esperado}**, observado `{e.get('status', '?')}`"
                + (f" (allow `{e['allow_id']}`)" if e.get("allow_id") else "")
            )

    if sin_confirmar:
        lines += [
            "",
            "## Permisos que la sonda no puede confirmar",
            "",
            "Origenes con busybox (`nc`) no distinguen un rechazo de un silencio, asi que",
            "estos permisos no se dan por verificados en ningun sentido:",
            "",
        ]
        for e in sin_confirmar:
            lines.append(f"- `{e['src']}` → `{e['dst']}:{e['port']}/{e['proto']}`")

    lines += [
        "",
        "Los bloqueos se derivan por producto cartesiano desde `opnsense/flows.yml`:",
        "todo lo no permitido explicitamente se espera bloqueado (ADR 0002).",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if not RESULTS.exists():
        print(
            "report: no hay resultados en tests/.results/matrix.json — "
            "ejecuta primero la suite contra el lab (make test, fase 5)",
            file=sys.stderr,
        )
        return 1
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    data.setdefault("generated", datetime.now(UTC).isoformat(timespec="seconds"))
    OUTPUT.write_text(render_matrix(data), encoding="utf-8")
    print(f"report: escrito {OUTPUT} ({len(data['expectations'])} expectativas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
