# Makefile del laboratorio de segmentacion de red.
# Objetivos definidos en docs/spec.md §9: up, provision, test, report, check, chaos, destroy.
# Extras de conveniencia: venv, test-unit, lint, fmt.

SHELL := /bin/bash
TF_DIR := terraform
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: help venv up destroy provision test test-unit report check rules network nat vpn chaos lint fmt

help: ## lista los objetivos disponibles
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

venv: ## crea el entorno de Python para tests y validadores
	@test -d $(VENV) || python3 -m venv $(VENV)
	@$(PIP) -q install -r requirements-dev.txt

# Alias de ssh del hipervisor (se resuelve en ~/.ssh/config del nodo de control).
NETSEG_PVE ?= pve01
PVE_SSH ?= netseg@$(NETSEG_PVE)

up: ## levanta toda la infraestructura del lab (terraform apply)
	@# PVE borra los ACL de /vms/<vmid> al destruir invitados (incidencia #8):
	@# se re-siembran antes de aplicar para que destroy/up cierre el ciclo.
	ssh -o BatchMode=yes $(PVE_SSH) sudo -n /usr/local/sbin/netseg-acl-restore.sh
	@# parallelism limitado: en el hipervisor anidado, 5 imports de disco en
	@# paralelo provocaron fallos transitorios de la API en el arranque en frio
	source ~/.netseg.env && cd $(TF_DIR) && terraform init -input=false && terraform apply -auto-approve -parallelism=4

destroy: ## destruye la infraestructura del lab (solo recursos del pool netseg)
	source ~/.netseg.env && cd $(TF_DIR) && terraform destroy -auto-approve

provision: ## aplica los playbooks de Ansible (fase 4: requiere el firewall enrutando)
	cd ansible && ansible-playbook site.yml

test: venv ## suite completa contra el lab levantado (marcador "lab")
	$(PYTEST) tests -m lab -q

test-unit: venv ## tests unitarios que corren sin lab (los mismos que la CI)
	$(PYTEST) tests -m "not lab" -q

report: venv ## regenera docs/matriz-flujos.md a partir de los resultados de la suite
	$(PY) tests/report.py

check: venv ## valida flows.yml y compara las reglas vivas del firewall contra el fichero
	$(PY) scripts/validate_flows.py opnsense/flows.yml
	source ~/.netseg-fw.env && $(PY) opnsense/apply.py --check

rules: venv ## sincroniza las reglas del firewall con flows.yml
	source ~/.netseg-fw.env && $(PY) opnsense/apply.py

network: venv ## crea VLANs, interfaces y DHCP en el firewall (fase 2)
	source ~/.netseg-fw.env && $(PY) opnsense/setup_red.py

chaos: venv ## inserta una regla any->any y exige que la suite se ponga en rojo
	source ~/.netseg-fw.env && $(PY) scripts/chaos.py

vpn: venv ## monta WireGuard y configura el cliente externo (fase 6)
	source ~/.netseg-fw.env && $(PY) opnsense/setup_vpn.py
	$(PY) scripts/configurar_cliente_vpn.py

nat: venv ## publica la web de la DMZ por NAT (fase 3)
	source ~/.netseg-fw.env && $(PY) opnsense/setup_nat.py

lint: venv ## linters de todo el repo: ruff, yamllint, ansible-lint, terraform fmt+validate
	$(VENV)/bin/ruff check scripts tests
	yamllint .
	ansible-lint ansible
	cd $(TF_DIR) && terraform init -backend=false -input=false > /dev/null && terraform fmt -check -recursive && terraform validate

fmt: ## formatea terraform y python
	cd $(TF_DIR) && terraform fmt -recursive
	$(VENV)/bin/ruff format scripts tests
