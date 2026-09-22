# ADR 0008: LXC en el propio Proxmox frente a WSL2 como nodo de control

- Estado: aceptada
- Fecha: 2026-07-25

## Contexto

El toolchain (Terraform, Ansible, linters, Python, la suite) necesita un Linux donde
vivir. El PC anfitrión es Windows, así que la opción cómoda sería WSL2. Pero este
Proxmox corre anidado en VMware Workstation con *Virtualize Intel VT-x/EPT*. Activar
WSL2 implica activar Hyper-V, y con Hyper-V como hipervisor raíz VMware pierde la
virtualización anidada: el Proxmox se quedaría sin `/dev/kvm` y no podría arrancar ni
una VM. Además, los ficheros creados en Windows arrastran CRLF, que rompe scripts de
shell (confirmado durante el bootstrap de este mismo repo).

## Decisión

Contenedor LXC Ubuntu 24.04 `ctrl01` dentro del propio Proxmox: 2 GB de RAM, red en
`vmbr0` por DHCP y fuera del pool `netseg`, de modo que la automatización no puede
tocar la máquina desde la que se ejecuta. El repo vive en `~/network-segmentation-lab`
y las credenciales en `~/.netseg.env`, nunca en el repo.

## Alternativas descartadas

- WSL2: incompatible con la virtualización anidada de VMware (Hyper-V roba VT-x al
  resto).
- El propio host Windows con binarios nativos: CRLF, path mangling y ausencia de
  tooling POSIX para los scripts del harness.
- Una VM de control separada: más RAM (el presupuesto son 11 GB) y más lenta de
  ciclo que un LXC, sin ganancia: el control no necesita kernel propio.
- Ejecutar todo en pve01 como root: es justo el antipatrón que el proyecto quiere
  demostrar que se puede evitar (autorización explícita y acotada).

## Consecuencias

- ctrl01 queda fuera del pool: el wrapper `netseg-exec.sh` rechaza su VMID (verificado
  en el bootstrap) y Terraform no puede destruirlo.
- Si el lab se lleva a otro hipervisor no anidado, WSL2 volvería a ser una opción; el
  repo no depende de dónde viva el nodo de control.
- El acceso SSH directo Windows→ctrl01 puede ser caprichoso por la traducción de MACs
  de la red anidada; el camino garantizado es entrar por pve01.
