#!/usr/bin/env bash
# setup-ctrl01.sh - Instala el toolchain del nodo de control ctrl01 (idempotente).
# Terraform desde el repo oficial de HashiCorp; ansible, ansible-lint y ruff por
# pipx (aislados y con binarios en /usr/local/bin); el resto por apt.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

log(){ printf '\n== %s\n' "$*"; }

log "paquetes base (apt)"
apt-get update -qq
apt-get install -y -qq git make curl gnupg lsb-release ca-certificates \
  python3 python3-venv python3-pip pipx yamllint jq unzip nodejs npm openssh-client

log "repositorio HashiCorp + terraform"
if [ ! -f /usr/share/keyrings/hashicorp-archive-keyring.gpg ]; then
  curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
fi
if [ ! -f /etc/apt/sources.list.d/hashicorp.list ]; then
  echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
    > /etc/apt/sources.list.d/hashicorp.list
  apt-get update -qq
fi
apt-get install -y -qq terraform

log "pipx: ansible, ansible-lint, ruff"
export PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin
pipx list 2>/dev/null | grep -q "package ansible "      || pipx install --include-deps ansible
pipx list 2>/dev/null | grep -q "package ansible-lint " || pipx install ansible-lint
pipx list 2>/dev/null | grep -q "package ruff "         || pipx install ruff

log "versiones instaladas"
terraform version | head -1
ansible --version | head -1
ansible-lint --version 2>/dev/null | head -1
yamllint --version
ruff --version
python3 --version
node --version
npm --version
git --version
make --version | head -1
