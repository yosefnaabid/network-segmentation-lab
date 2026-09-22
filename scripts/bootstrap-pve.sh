#!/usr/bin/env bash
# bootstrap-pve.sh - Bootstrap idempotente del hipervisor pve01 para el lab netseg.
#
# Ejecutar como root en pve01. Ejecutarlo dos veces no duplica nada.
# Hace, en orden:
#   1. Diagnostico del nodo e inventario de invitados existentes
#   2. Bridge vmbr2 VLAN-aware sin puerto fisico (vmbr1 pertenece a otro lab: NO SE TOCA)
#   3. Contenedor de control ctrl01 (ubuntu-24.04, vmbr0/DHCP, fuera del pool)
#   4. Pool netseg
#   5. Roles de privilegios minimos + usuario terraform@pve + ACLs
#   6. Token de API -> /root/.netseg.env (600) y copia a ctrl01 via pct push
#   7. Cuenta 'netseg' + wrapper /usr/local/sbin/netseg-exec.sh + sudoers
#   8. Validacion: crea y borra una VM de prueba con el token de terraform
#
# El secreto del token no se imprime nunca: va directo a /root/.netseg.env.
set -euo pipefail

NODE=pve01
BRIDGE=vmbr2
POOL=netseg
CTRL_VMID=900
CTRL_STORAGE=local-lvm
TPL_STORAGE=local
TF_USER=terraform@pve
TOKEN_ID=netseg
ENV_FILE=/root/.netseg.env
WRAPPER=/usr/local/sbin/netseg-exec.sh
EXEC_ACCOUNT=netseg
TEST_VMID=999
TEST_CTID=998
# VMIDs reservadas del lab (incluye 998/999 de validacion). Los ACL por VMID
# existen porque la API de PVE comprueba algunos permisos de creacion de LXC
# contra /vms/<vmid> y no contra el pool (incidencia #8).
LAB_VMIDS="600 601 610 620 621 630 640 650 699 998 999"
# Clave publica que se inyecta en ctrl01: la del propio root del hipervisor,
# o la que se indique en NETSEG_PUBKEY_FILE. No se incrusta en el repo.
PUBKEY_FILE="${NETSEG_PUBKEY_FILE:-/root/.ssh/authorized_keys}"

log(){ printf '\n== %s\n' "$*"; }

# ---------------------------------------------------------------- 1. diagnostico
log "PASO 1: diagnostico"
pveversion
printf 'nucleos con vmx/svm: %s\n' "$(grep -c -E 'vmx|svm' /proc/cpuinfo)"
ls -l /dev/kvm
free -h | sed -n '1,2p'
pvesm status
log "inventario de invitados existentes"
qm list
pct list || true
if [ ! -f /root/netseg-bootstrap/inventario-inicial.txt ]; then
  { date; qm list; pct list; } > /root/netseg-bootstrap/inventario-inicial.txt
fi

# ---------------------------------------------------------------- 2. bridge vmbr2
log "PASO 2: bridge $BRIDGE (VLAN-aware, sin puerto fisico)"
if [ ! -f /etc/network/interfaces.d/netseg-vmbr2 ]; then
  cat > /etc/network/interfaces.d/netseg-vmbr2 <<'BRIDGECFG'
# netseg: bridge interno VLAN-aware del laboratorio (sin puerto fisico).
# Gestionado por scripts/bootstrap-pve.sh - no editar a mano.
# vmbr1 pertenece a otro laboratorio y no se toca.
auto vmbr2
iface vmbr2 inet manual
    bridge-ports none
    bridge-stp off
    bridge-fd 0
    bridge-vlan-aware yes
    bridge-vids 2-4094
BRIDGECFG
  ifreload -a
fi
ip -br link show "$BRIDGE"
log "verificacion de vmbr0 tras el cambio de red"
# El direccionamiento de la red de gestion se DESCUBRE: asi el script vale en
# cualquier instalacion y el repositorio no publica la red de nadie.
PVE_IP=$(ip -4 -o addr show vmbr0 | awk '{print $4}' | cut -d/ -f1 | head -1)
GATEWAY=$(ip route | awk '/^default/ {print $3; exit}')
[ -n "$PVE_IP" ] || { echo "FALLO CRITICO: vmbr0 sin IP"; exit 1; }
[ -n "$GATEWAY" ] || { echo "FALLO CRITICO: sin ruta por defecto"; exit 1; }
ping -c1 -W2 "$GATEWAY" >/dev/null && echo "vmbr0 OK: con IP, ruta por defecto y puerta de enlace alcanzable"

# ---------------------------------------------------------------- 3. ctrl01
log "PASO 3: contenedor de control ctrl01 (VMID $CTRL_VMID, fuera del pool)"
if ! pct status "$CTRL_VMID" >/dev/null 2>&1; then
  pveam update >/dev/null
  TPL=$(pveam available --section system | awk '{print $2}' | grep '^ubuntu-24.04-standard' | sort -V | tail -1)
  [ -n "$TPL" ] || { echo "FALLO: plantilla ubuntu-24.04-standard no disponible"; exit 1; }
  pveam list "$TPL_STORAGE" | grep -q "$TPL" || pveam download "$TPL_STORAGE" "$TPL"
  [ -s "$PUBKEY_FILE" ] || { echo "FALLO: sin clave publica en $PUBKEY_FILE"; exit 1; }
  head -1 "$PUBKEY_FILE" > /root/netseg-bootstrap/ctrl01-authorized.pub
  pct create "$CTRL_VMID" "$TPL_STORAGE:vztmpl/$TPL" \
    --hostname ctrl01 --memory 2048 --swap 512 --cores 2 \
    --rootfs "$CTRL_STORAGE:16" \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --onboot 1 --unprivileged 1 --features nesting=1 \
    --description "Nodo de control del lab netseg. Fuera del pool a proposito." \
    --ssh-public-keys /root/netseg-bootstrap/ctrl01-authorized.pub
fi
pct status "$CTRL_VMID" | grep -q running || pct start "$CTRL_VMID"
CTRL_IP=""
for _ in $(seq 1 45); do
  CTRL_IP=$(pct exec "$CTRL_VMID" -- ip -4 -o addr show dev eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1) || true
  [ -n "$CTRL_IP" ] && break
  sleep 2
done
[ -n "$CTRL_IP" ] || { echo "FALLO: ctrl01 sin IP por DHCP"; exit 1; }
echo "ctrl01 en marcha con IP: $CTRL_IP"
pct exec "$CTRL_VMID" -- bash -c 'command -v sshd >/dev/null || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server; }'
pct exec "$CTRL_VMID" -- bash -c 'systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1; systemctl is-active ssh || systemctl is-active sshd'
pct exec "$CTRL_VMID" -- bash -c '[ -f /root/.ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N "" -f /root/.ssh/id_ed25519 -C ctrl01-netseg'
CTRL_PUB=$(pct exec "$CTRL_VMID" -- cat /root/.ssh/id_ed25519.pub)

# ---------------------------------------------------------------- 4. pool
log "PASO 4: pool $POOL"
pvesh get "/pools/$POOL" >/dev/null 2>&1 || pvesh create /pools --poolid "$POOL"
pvesh get /pools --output-format json

# ---------------------------------------------------------------- 5. roles, usuario y ACLs
log "PASO 5: roles de privilegios minimos, $TF_USER y ACLs"
ensure_role(){
  local name=$1 privs=$2
  if pvesh get "/access/roles/$name" >/dev/null 2>&1; then
    pveum role modify "$name" --privs "$privs"
  else
    pveum role add "$name" --privs "$privs"
  fi
}
# Guest: gestion completa de invitados, pero solo donde se conceda (el pool).
# Pool.Allocate es necesario para crear invitados directamente dentro del pool.
ensure_role NetsegTFGuest "VM.Allocate,VM.Clone,VM.Audit,VM.Console,VM.PowerMgmt,VM.Snapshot,VM.Snapshot.Rollback,VM.Config.CDROM,VM.Config.CPU,VM.Config.Cloudinit,VM.Config.Disk,VM.Config.HWType,VM.Config.Memory,VM.Config.Network,VM.Config.Options,Pool.Allocate,Pool.Audit"
# Storage: la asignacion de espacio NO es pool-scoped; se concede por datastore.
ensure_role NetsegTFStorage "Datastore.AllocateSpace,Datastore.AllocateTemplate,Datastore.Audit"
# SDN: usar un bridge exige SDN.Use sobre /sdn/zones/localnetwork/<bridge>.
ensure_role NetsegTFSDN "SDN.Use"
# Node: el provider consulta recursos del nodo (solo lectura).
ensure_role NetsegTFNode "Sys.Audit"
pvesh get "/access/users/$TF_USER" >/dev/null 2>&1 || pveum user add "$TF_USER" --comment "Automatizacion Terraform del lab netseg"
pveum acl modify "/pool/$POOL" --users "$TF_USER" --roles NetsegTFGuest
pveum acl modify /storage/local --users "$TF_USER" --roles NetsegTFStorage
pveum acl modify /storage/local-lvm --users "$TF_USER" --roles NetsegTFStorage
pveum acl modify "/sdn/zones/localnetwork/$BRIDGE" --users "$TF_USER" --roles NetsegTFSDN
pveum acl modify /sdn/zones/localnetwork/vmbr0 --users "$TF_USER" --roles NetsegTFSDN
pveum acl modify "/nodes/$NODE" --users "$TF_USER" --roles NetsegTFNode
# ACL por VMID reservada: al crear LXC, PVE comprueba VM.Config.* contra
# /vms/<vmid> aunque se pase pool=; el rol del pool no cubre ese caso.
for vmid in $LAB_VMIDS; do
  pveum acl modify "/vms/$vmid" --users "$TF_USER" --roles NetsegTFGuest
done
pveum acl list | grep -E 'netseg|terraform' | head -25 || true

# ---------------------------------------------------------------- 6. token de API
log "PASO 6: token de API (el secreto va directo a $ENV_FILE, nunca a la terminal)"
have_token(){ pvesh get "/access/users/$TF_USER/token/$TOKEN_ID" >/dev/null 2>&1; }
if [ -s "$ENV_FILE" ] && grep -q PROXMOX_VE_API_TOKEN "$ENV_FILE" && have_token; then
  echo "token existente y $ENV_FILE presente: se conservan"
else
  have_token && pveum user token remove "$TF_USER" "$TOKEN_ID" >/dev/null
  umask 077
  {
    echo "# Credenciales del lab netseg. NO subir a git."
    echo "export PROXMOX_VE_ENDPOINT=\"https://${PVE_IP}:8006/\""
    echo "export PROXMOX_VE_INSECURE=\"true\""
    pveum user token add "$TF_USER" "$TOKEN_ID" --privsep 0 --comment "terraform lab netseg" --output-format json \
      | python3 -c 'import sys,json; d=json.load(sys.stdin); print("export PROXMOX_VE_API_TOKEN=\"%s=%s\"" % (d["full-tokenid"], d["value"]))'
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
printf '%s: %s lineas, permisos %s\n' "$ENV_FILE" "$(grep -c . "$ENV_FILE")" "$(stat -c %a "$ENV_FILE")"
pct push "$CTRL_VMID" "$ENV_FILE" /root/.netseg.env --perms 0600
pct exec "$CTRL_VMID" -- ls -l /root/.netseg.env

# ---------------------------------------------------------------- 7. cuenta netseg + wrapper + sudoers
log "PASO 7: cuenta $EXEC_ACCOUNT, wrapper y sudoers"
# La instalacion minima de PVE 9 no incluye sudo (incidencia #4): se instala.
if ! command -v visudo >/dev/null 2>&1; then
  echo "instalando sudo (la instalacion minima de PVE no lo trae)"
  apt-get install -y -qq sudo || { apt-get update -qq || true; apt-get install -y -qq sudo; }
fi
id -u "$EXEC_ACCOUNT" >/dev/null 2>&1 || useradd -m -s /bin/bash "$EXEC_ACCOUNT"
install -d -m 700 -o "$EXEC_ACCOUNT" -g "$EXEC_ACCOUNT" "/home/$EXEC_ACCOUNT/.ssh"
touch "/home/$EXEC_ACCOUNT/.ssh/authorized_keys"
grep -qF "$CTRL_PUB" "/home/$EXEC_ACCOUNT/.ssh/authorized_keys" || printf '%s\n' "$CTRL_PUB" >> "/home/$EXEC_ACCOUNT/.ssh/authorized_keys"
chown "$EXEC_ACCOUNT:$EXEC_ACCOUNT" "/home/$EXEC_ACCOUNT/.ssh/authorized_keys"
chmod 600 "/home/$EXEC_ACCOUNT/.ssh/authorized_keys"

cat > "$WRAPPER" <<'WRAP'
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
WRAP
chmod 755 "$WRAPPER"

# Segundo helper delegado: PVE borra los ACL de /vms/<vmid> cuando el invitado
# se destruye (con o sin purge), asi que tras `make destroy` los LXC no pueden
# recrearse (incidencia #8). Este script re-siembra los ACL de las VMIDs
# RESERVADAS del lab; sin argumentos: superficie de sudoers minima.
ACL_RESTORE=/usr/local/sbin/netseg-acl-restore.sh
cat > "$ACL_RESTORE" <<RESTORE
#!/usr/bin/env bash
# netseg-acl-restore.sh - re-siembra los ACL por VMID reservada del lab.
# Los borrados de invitados eliminan los ACL de /vms/<vmid>; 'make up' invoca
# esto (via sudoers) antes de terraform para que el ciclo destroy/up cierre.
set -euo pipefail
for vmid in $LAB_VMIDS; do
  pveum acl modify "/vms/\$vmid" --users $TF_USER --roles NetsegTFGuest
done
echo "ACLs restaurados para: $LAB_VMIDS"
RESTORE
chmod 755 "$ACL_RESTORE"

{
  printf '%s ALL=(root) NOPASSWD: %s\n' "$EXEC_ACCOUNT" "$WRAPPER"
  printf '%s ALL=(root) NOPASSWD: %s\n' "$EXEC_ACCOUNT" "$ACL_RESTORE"
} > /etc/sudoers.d/netseg-exec.tmp
visudo -cf /etc/sudoers.d/netseg-exec.tmp
install -m 440 /etc/sudoers.d/netseg-exec.tmp /etc/sudoers.d/netseg-exec
rm -f /etc/sudoers.d/netseg-exec.tmp
sudo -u "$EXEC_ACCOUNT" sudo -n "$ACL_RESTORE" >/dev/null
echo "wrapper, restaurador de ACLs y sudoers instalados y probados"

# ---------------------------------------------------------------- 8. validacion
log "PASO 8: validacion del rol (crear/borrar VM de prueba como $TF_USER) y del wrapper"
TOKEN_LINE=$(grep PROXMOX_VE_API_TOKEN "$ENV_FILE" | sed -e 's/^export PROXMOX_VE_API_TOKEN="//' -e 's/"$//')
CURLCFG=$(mktemp); chmod 600 "$CURLCFG"
printf 'header = "Authorization: PVEAPIToken=%s"\ninsecure\nsilent\nshow-error\n' "$TOKEN_LINE" > "$CURLCFG"
api(){ local method=$1 path=$2; shift 2; curl -K "$CURLCFG" -X "$method" "https://127.0.0.1:8006/api2/json$path" "$@"; }

# limpieza sin --purge: purge borraria los ACL de /vms/<vmid> (incidencia #8)
qm status "$TEST_VMID" >/dev/null 2>&1 && { qm stop "$TEST_VMID" >/dev/null 2>&1 || true; qm destroy "$TEST_VMID" >/dev/null 2>&1 || true; }
CREATE_OUT=$(api POST "/nodes/$NODE/qemu" \
  --data-urlencode "vmid=$TEST_VMID" \
  --data-urlencode "name=tf-perm-test" \
  --data-urlencode "pool=$POOL" \
  --data-urlencode "memory=128" \
  --data-urlencode "net0=virtio,bridge=$BRIDGE,tag=99" \
  --data-urlencode "scsi0=local-lvm:1")
echo "$CREATE_OUT" | grep -q 'UPID' || { echo "FALLO validacion (create): $CREATE_OUT"; exit 1; }
ok=""
for _ in $(seq 1 20); do qm status "$TEST_VMID" >/dev/null 2>&1 && { ok=1; break; }; sleep 1; done
[ -n "$ok" ] || { echo "FALLO validacion: la VM de prueba no llego a existir"; exit 1; }
pvesh get "/pools/$POOL" --output-format json | grep -q "\"vmid\":$TEST_VMID" || { echo "FALLO: la VM de prueba no esta en el pool"; exit 1; }
echo "create OK: tf-perm-test ($TEST_VMID) existe y esta en el pool $POOL"

echo "-- wrapper, caso positivo (VM del pool, sin agente: debe llegar a qm guest exec y fallar ahi)"
set +e
POS_ERR=$(sudo -u "$EXEC_ACCOUNT" sudo -n "$WRAPPER" "$TEST_VMID" -- /bin/true 2>&1)
POS_RC=$?
set -e
if echo "$POS_ERR" | grep -qi 'DENEGADO'; then echo "FALLO: el wrapper rechazo una VMID del pool"; exit 1; fi
echo "wrapper despacho a qm (rc=$POS_RC): $(echo "$POS_ERR" | head -1)"

echo "-- wrapper, casos negativos (fuera del pool: ctrl01=$CTRL_VMID, otro lab=111, inexistente=54321)"
for bad in "$CTRL_VMID" 111 54321; do
  set +e
  NEG_ERR=$(sudo -u "$EXEC_ACCOUNT" sudo -n "$WRAPPER" "$bad" -- /bin/true 2>&1)
  NEG_RC=$?
  set -e
  if [ "$NEG_RC" -eq 3 ] && echo "$NEG_ERR" | grep -q DENEGADO; then
    echo "rechazada VMID $bad: OK"
  else
    echo "FALLO: el wrapper NO rechazo la VMID $bad (rc=$NEG_RC: $NEG_ERR)"; exit 1
  fi
done

DEL_OUT=$(api DELETE "/nodes/$NODE/qemu/$TEST_VMID")
echo "$DEL_OUT" | grep -q 'UPID' || { echo "FALLO validacion (delete): $DEL_OUT"; exit 1; }
ok=""
for _ in $(seq 1 20); do qm status "$TEST_VMID" >/dev/null 2>&1 || { ok=1; break; }; sleep 1; done
[ -n "$ok" ] || { echo "FALLO validacion: la VM de prueba no se borro"; exit 1; }
echo "delete OK: la VM de prueba ya no existe"

echo "-- validacion LXC (los checks de permisos difieren de los de QEMU)"
TPL_CT=$(pveam list local 2>/dev/null | awk '/vztmpl\/(alpine|almalinux)/ {print $1; exit}')
wait_ct_unlock(){
  for _ in $(seq 1 45); do
    pct config "$TEST_CTID" 2>/dev/null | grep -q '^lock:' || return 0
    sleep 2
  done
  return 1
}
if [ -n "$TPL_CT" ]; then
  if pct status "$TEST_CTID" >/dev/null 2>&1; then
    wait_ct_unlock || true
    pct destroy "$TEST_CTID" >/dev/null 2>&1 || true
  fi
  CT_OUT=$(api POST "/nodes/$NODE/lxc" \
    --data-urlencode "vmid=$TEST_CTID" \
    --data-urlencode "hostname=tf-perm-test-ct" \
    --data-urlencode "ostemplate=$TPL_CT" \
    --data-urlencode "pool=$POOL" \
    --data-urlencode "memory=128" \
    --data-urlencode "rootfs=local-lvm:1" \
    --data-urlencode "net0=name=eth0,bridge=$BRIDGE,tag=99" \
    --data-urlencode "unprivileged=1" \
    --data-urlencode "description=validacion de permisos netseg" \
    --data-urlencode "tags=netseg" \
    --data-urlencode "onboot=0")
  echo "$CT_OUT" | grep -q 'UPID' || { echo "FALLO validacion (create CT): $CT_OUT"; exit 1; }
  ok=""
  for _ in $(seq 1 30); do pct status "$TEST_CTID" >/dev/null 2>&1 && { ok=1; break; }; sleep 1; done
  [ -n "$ok" ] || { echo "FALLO validacion: el CT de prueba no llego a existir"; exit 1; }
  # la tarea de creacion mantiene el CT bloqueado (lock=create) unos segundos
  wait_ct_unlock || { echo "FALLO validacion: el CT de prueba sigue bloqueado"; exit 1; }
  CTDEL_OUT=$(api DELETE "/nodes/$NODE/lxc/$TEST_CTID")
  echo "$CTDEL_OUT" | grep -q 'UPID' || { echo "FALLO validacion (delete CT): $CTDEL_OUT"; exit 1; }
  ok=""
  for _ in $(seq 1 30); do pct status "$TEST_CTID" >/dev/null 2>&1 || { ok=1; break; }; sleep 1; done
  [ -n "$ok" ] || { echo "FALLO validacion: el CT de prueba no se borro"; exit 1; }
  echo "validacion LXC OK: crear y borrar CT en el pool funciona"
else
  echo "AVISO: no hay plantilla LXC en local; validacion de CT omitida (ejecuta fetch-images.sh)"
fi
rm -f "$CURLCFG"

log "BOOTSTRAP COMPLETO"
echo "ctrl01: VMID $CTRL_VMID, IP $CTRL_IP (usuario root, clave del usuario inyectada)"
echo "pool: $POOL | bridge lab: $BRIDGE | usuario API: $TF_USER!$TOKEN_ID"
echo "wrapper: $WRAPPER via cuenta '$EXEC_ACCOUNT'"
