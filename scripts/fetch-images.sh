#!/usr/bin/env bash
# fetch-images.sh - Descarga idempotente de los artefactos que el lab necesita
# y que Terraform no descarga por si mismo (decision: mantener rapido el ciclo
# destroy/up y no dar al token permisos de descarga de URLs).
#
#   - Plantillas LXC: almalinux-9-default y alpine-3.x (la mas reciente)
#   - Imagen cloud AlmaLinux 9 GenericCloud -> local:import (para import_from)
#   - ISO OPNsense 25.7 dvd (verificando sha256 del .bz2 antes de descomprimir)
#
# Ejecutar como root en pve01. Re-ejecutarlo no descarga nada que ya exista.
set -euo pipefail

IMPORT_DIR=/var/lib/vz/import
ISO_DIR=/var/lib/vz/template/iso
ALMA_FILE=AlmaLinux-9-GenericCloud-latest.x86_64.qcow2
ALMA_URL="https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/$ALMA_FILE"
OPN_VER=25.7
OPN_ISO="OPNsense-$OPN_VER-dvd-amd64.iso"
OPN_MIRROR="https://mirror.dns-root.de/opnsense/releases/$OPN_VER"

log(){ printf '\n== %s\n' "$*"; }

# 1) habilitar contenido 'import' en el storage local (aditivo, no quita nada)
log "contenido 'import' en storage local"
CUR=$(pvesh get /storage/local --output-format json | python3 -c 'import sys,json; print(json.load(sys.stdin)["content"])')
case ",$CUR," in
  *,import,*) echo "ya habilitado: $CUR" ;;
  *) pvesh set /storage/local --content "$CUR,import"; echo "habilitado: $CUR,import" ;;
esac
mkdir -p "$IMPORT_DIR"

# 2) plantillas LXC
log "plantillas LXC (almalinux-9, alpine)"
pveam update >/dev/null
for pat in almalinux-9-default alpine-3; do
  TPL=$(pveam available --section system | awk '{print $2}' | grep "^$pat" | sort -V | tail -1)
  [ -n "$TPL" ] || { echo "FALLO: no hay plantilla que empiece por $pat"; exit 1; }
  if pveam list local | grep -q "$TPL"; then
    echo "ya presente: $TPL"
  else
    pveam download local "$TPL"
  fi
done

# 3) imagen cloud AlmaLinux
log "imagen cloud AlmaLinux 9"
if [ -s "$IMPORT_DIR/$ALMA_FILE" ]; then
  echo "ya presente: $ALMA_FILE"
else
  curl -fL --retry 3 -o "$IMPORT_DIR/$ALMA_FILE.part" "$ALMA_URL"
  mv "$IMPORT_DIR/$ALMA_FILE.part" "$IMPORT_DIR/$ALMA_FILE"
fi

# 4) ISO OPNsense (bz2 + verificacion sha256 + descompresion)
log "ISO OPNsense $OPN_VER"
if [ -s "$ISO_DIR/$OPN_ISO" ]; then
  echo "ya presente: $OPN_ISO"
else
  cd /tmp
  curl -fL --retry 3 -o "$OPN_ISO.bz2" "$OPN_MIRROR/$OPN_ISO.bz2"
  curl -fL --retry 3 -o opn-checksums.sha256 "$OPN_MIRROR/OPNsense-$OPN_VER-checksums-amd64.sha256"
  EXP=$(grep "$OPN_ISO.bz2" opn-checksums.sha256 | grep -oE '[0-9a-f]{64}' | head -1)
  ACT=$(sha256sum "$OPN_ISO.bz2" | awk '{print $1}')
  if [ -z "$EXP" ] || [ "$EXP" != "$ACT" ]; then
    echo "FALLO: checksum del ISO no coincide (esperado=$EXP obtenido=$ACT)"; exit 1
  fi
  echo "sha256 del .bz2 verificado"
  bunzip2 -f "$OPN_ISO.bz2"
  mv "$OPN_ISO" "$ISO_DIR/"
  rm -f opn-checksums.sha256
fi

# 5) variante netseg de la imagen cloud: RHEL/AlmaLinux traen el agente QEMU
#    con una allow-list de RPCs que NO incluye guest-exec (incidencia #9), y
#    sin guest-exec el harness fuera de banda (spec §8) no puede funcionar.
#    Se monta el qcow2 con qemu-nbd y se amplia la allow-list; el resultado
#    es la imagen que Terraform importa de verdad.
NETSEG_IMG=AlmaLinux-9-GenericCloud-netseg.x86_64.qcow2
log "variante netseg de la imagen (guest-exec habilitado)"
if [ -s "$IMPORT_DIR/$NETSEG_IMG" ]; then
  echo "ya presente: $NETSEG_IMG"
else
  cp "$IMPORT_DIR/$ALMA_FILE" /tmp/netseg-img.qcow2
  modprobe nbd max_part=8
  qemu-nbd --disconnect /dev/nbd0 >/dev/null 2>&1 || true
  qemu-nbd --connect=/dev/nbd0 /tmp/netseg-img.qcow2
  trap 'umount /mnt/netseg-img 2>/dev/null || true; qemu-nbd --disconnect /dev/nbd0 >/dev/null 2>&1 || true' EXIT
  sleep 2
  mkdir -p /mnt/netseg-img
  ROOTP=""
  for p in /dev/nbd0p4 /dev/nbd0p3 /dev/nbd0p2; do
    mount "$p" /mnt/netseg-img 2>/dev/null || continue
    if [ -f /mnt/netseg-img/etc/sysconfig/qemu-ga ]; then ROOTP="$p"; break; fi
    umount /mnt/netseg-img
  done
  [ -n "$ROOTP" ] || { echo "FALLO: no encuentro la particion raiz de la imagen"; exit 1; }
  echo "raiz de la imagen en $ROOTP"
  python3 - <<'PYEOF'
import os
import re

ROOT = "/mnt/netseg-img"

# (a) allow-list del agente: anadir guest-exec y guest-exec-status
path = f"{ROOT}/etc/sysconfig/qemu-ga"
with open(path) as fh:
    s = fh.read()
m = re.search(r'^FILTER_RPC_ARGS="--allow-rpcs=([^"]*)"', s, re.M)
assert m, "formato inesperado de /etc/sysconfig/qemu-ga"
rpcs = m.group(1).split(",")
for extra in ("guest-exec", "guest-exec-status"):
    if extra not in rpcs:
        rpcs.append(extra)
s = s[: m.start(1)] + ",".join(rpcs) + s[m.end(1) :]
with open(path, "w") as fh:
    fh.write(s)
print("allow-rpcs ampliado con guest-exec y guest-exec-status")

# (b) primer arranque: boolean del vendor para que los hijos de guest-exec
#     corran sin el confinamiento de virt_qemu_ga_t. SELinux SIGUE enforcing
#     (regla dura del proyecto); sin esto, execve muere con EACCES.
#     Verificado offline: la politica trae virt_qemu_ga_run_unconfined.
script = f"{ROOT}/usr/local/sbin/netseg-firstboot.sh"
with open(script, "w") as fh:
    fh.write(
        "#!/bin/bash\n"
        "# Primer arranque del lab netseg: habilita la ejecucion del agente QEMU\n"
        "# bajo SELinux via el boolean del vendor. Enforcing se queda como esta.\n"
        "set -euo pipefail\n"
        "if [ ! -f /var/lib/netseg-firstboot.done ]; then\n"
        "  setsebool -P virt_qemu_ga_run_unconfined on\n"
        "  touch /var/lib/netseg-firstboot.done\n"
        "fi\n"
        "systemctl disable netseg-firstboot.service || true\n"
    )
os.chmod(script, 0o755)

unit = f"{ROOT}/etc/systemd/system/netseg-firstboot.service"
with open(unit, "w") as fh:
    fh.write(
        "[Unit]\n"
        "Description=Primer arranque del lab netseg (boolean SELinux del agente QEMU)\n"
        "ConditionPathExists=!/var/lib/netseg-firstboot.done\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/local/sbin/netseg-firstboot.sh\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
os.chmod(unit, 0o644)

wants_dir = f"{ROOT}/etc/systemd/system/multi-user.target.wants"
os.makedirs(wants_dir, exist_ok=True)
link = f"{wants_dir}/netseg-firstboot.service"
if not os.path.islink(link):
    os.symlink("../netseg-firstboot.service", link)

# (b2) entrypoint del harness: bajo virt_qemu_ga_t los hijos de guest-exec
#     siguen confinados (sin red y sin ver binarios con dominio propio, p.ej.
#     hostname_exec_t). El mecanismo del vendor es un entrypoint etiquetado
#     virt_qemu_ga_unconfined_exec_t que, con el boolean activo, transiciona
#     a virt_qemu_ga_unconfined_t. El harness SIEMPRE ejecuta a traves de el.
#     Verificado en vivo: id -Z => virt_qemu_ga_unconfined_t y sockets OK.
runner = f"{ROOT}/usr/local/sbin/netseg-agent-runner"
with open(runner, "w") as fh:
    fh.write(
        "#!/bin/bash\n"
        "# Entrypoint del harness netseg (spec §8). Etiquetado como\n"
        "# virt_qemu_ga_unconfined_exec_t: con virt_qemu_ga_run_unconfined=on,\n"
        "# los hijos de guest-exec transicionan a un dominio sin confinar.\n"
        "# SELinux permanece en enforcing.\n"
        'exec "$@"\n'
    )
os.chmod(runner, 0o755)

# (c) etiquetas SELinux: los ficheros creados offline no tienen xattr y el
#     guest los veria como unlabeled_t; se etiquetan a mano.
def label(path_, ctx, symlink=False):
    os.setxattr(path_, "security.selinux", ctx.encode() + b"\x00", follow_symlinks=not symlink)

label(script, "system_u:object_r:bin_t:s0")
label(unit, "system_u:object_r:systemd_unit_file_t:s0")
label(link, "system_u:object_r:systemd_unit_file_t:s0", symlink=True)
label(runner, "system_u:object_r:virt_qemu_ga_unconfined_exec_t:s0")
print("unidad de primer arranque y entrypoint del harness instalados y etiquetados")
PYEOF
  umount /mnt/netseg-img
  qemu-nbd --disconnect /dev/nbd0
  trap - EXIT
  mv /tmp/netseg-img.qcow2 "$IMPORT_DIR/$NETSEG_IMG"
fi

log "resumen de artefactos"
ls -lh "$IMPORT_DIR/$ALMA_FILE" "$IMPORT_DIR/$NETSEG_IMG" "$ISO_DIR/$OPN_ISO"
pveam list local
