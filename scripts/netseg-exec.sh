#!/usr/bin/env bash
# netseg-exec.sh - ejecuta un comando dentro de un invitado del pool netseg.
#
# Uso: netseg-exec.sh <VMID> [--] <comando> [args...]
#
# Valida contra `pvesh get /pools/netseg` que la VMID pertenece al pool y
# despacha a `qm guest exec` (VMs, requiere qemu-guest-agent) o `pct exec`
# (LXC) segun el tipo. Toda VMID fuera del pool se rechaza: esta es la
# frontera que protege al resto de proyectos del hipervisor.
set -euo pipefail
POOL=netseg
if [ $# -lt 2 ]; then
  echo "uso: $(basename "$0") VMID [--] comando [args...]" >&2
  exit 2
fi
VMID=$1; shift
[ "${1:-}" = "--" ] && shift
case $VMID in (*[!0-9]*|'') echo "VMID invalida: $VMID" >&2; exit 2;; esac
TYPE=$(pvesh get "/pools/$POOL" --output-format json 2>/dev/null | python3 -c '
import json, sys
vmid = sys.argv[1]
data = json.load(sys.stdin)
for m in data.get("members", []):
    if str(m.get("vmid")) == vmid:
        print(m.get("type", ""))
        break
' "$VMID")
if [ -z "$TYPE" ]; then
  echo "DENEGADO: la VMID $VMID no pertenece al pool $POOL" >&2
  exit 3
fi
case $TYPE in
  qemu) exec qm guest exec "$VMID" --timeout 300 -- "$@" ;;
  lxc)  exec pct exec "$VMID" -- "$@" ;;
  *)    echo "tipo de invitado desconocido: $TYPE" >&2; exit 4 ;;
esac
