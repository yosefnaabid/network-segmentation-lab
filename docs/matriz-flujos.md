# Matriz de flujos verificada

**GENERADO por `tests/report.py` — no editar a mano.**

- Fecha de generacion: 2026-07-25T20:21:43+00:00
- Expectativas: 297 (31 permitidas, 266 bloqueadas)
- Verificadas: 297 · Discrepancias: 0 · Sin confirmar: 0
- **Bloqueos verificados: 266/266**

Cada celda: sondas verificadas/total entre ese par de segmentos.

| Origen \ Destino | DMZ | MGMT | SERVERS | WAN |
|---|---|---|---|---|
| **DMZ** | — | ✅ 9/9 | ✅ 18/18 | ✅ 9/9 |
| **GUEST** | ✅ 9/9 | ✅ 9/9 | ✅ 18/18 | ✅ 9/9 |
| **IOT** | ✅ 9/9 | ✅ 9/9 | ✅ 18/18 | ✅ 9/9 |
| **MGMT** | ✅ 9/9 | — | ✅ 18/18 | ✅ 9/9 |
| **SERVERS** | ✅ 18/18 | ✅ 18/18 | — | ✅ 18/18 |
| **USERS** | ✅ 9/9 | ✅ 9/9 | ✅ 18/18 | ✅ 9/9 |
| **WAN** | ✅ 9/9 | ✅ 9/9 | ✅ 18/18 | — |

Los bloqueos se derivan por producto cartesiano desde `opnsense/flows.yml`:
todo lo no permitido explicitamente se espera bloqueado (ADR 0002).
