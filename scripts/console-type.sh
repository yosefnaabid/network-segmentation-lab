#!/usr/bin/env bash
# console-type.sh - teclea en la consola de un invitado del pool netseg via la
# API (sendkey), con el token acotado al pool.
#
# OJO: sendkey manda POSICIONES de tecla (layout US). La consola de fw01 tiene
# keymap ESPANOL, asi que los simbolos se traducen: para un "-" hay que mandar
# la tecla que en US es "/", etc. La tabla de abajo hace esa conversion.
#
# Uso: console-type.sh <VMID> "texto" [--ret] ["mas texto" ...]
set -euo pipefail
VMID=$1; shift
TOKEN=$(sed -n "s/^export PROXMOX_VE_API_TOKEN=\"\(.*\)\"$/\1/p" /root/.netseg.env)
CFG=$(mktemp); chmod 600 "$CFG"
printf "header = \"Authorization: PVEAPIToken=%s\"\ninsecure\nsilent\nshow-error\n" "$TOKEN" > "$CFG"
trap "rm -f $CFG" EXIT

send(){ curl -K "$CFG" -X PUT "https://127.0.0.1:8006/api2/json/nodes/pve01/qemu/$VMID/sendkey" \
        --data-urlencode "key=$1" >/dev/null; }

for arg in "$@"; do
  if [ "$arg" = "--ret" ]; then send ret; continue; fi
  i=0
  while [ $i -lt ${#arg} ]; do
    c=${arg:$i:1}
    case "$c" in
      [a-z0-9]) send "$c" ;;
      [A-Z])    send "shift-$(printf %s "$c" | tr "A-Z" "a-z")" ;;
      " ")  send spc ;;
      "-")  send slash ;;           # ES: el guion esta donde US tiene /
      ".")  send dot ;;
      ",")  send comma ;;
      ":")  send "shift-dot" ;;
      ";")  send "shift-comma" ;;
      "/")  send "shift-7" ;;
      "_")  send "shift-slash" ;;
      "&")  send "shift-6" ;;
      "(")  send "shift-8" ;;
      ")")  send "shift-9" ;;
      "=")  send "shift-0" ;;
      "?")  send "shift-minus" ;;
      "\"") send "shift-2" ;;
      "'")  send minus ;;
      *) echo "caracter no soportado en keymap ES: [$c]" >&2; exit 2 ;;
    esac
    i=$((i+1))
  done
done
