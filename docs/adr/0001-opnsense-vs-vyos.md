# ADR 0001: OPNsense frente a VyOS como firewall perimetral

- Estado: aceptada
- Fecha: 2026-07-25

## Contexto

El laboratorio necesita un único dispositivo que haga de router entre 6 VLANs, firewall
con denegación por defecto, servidor DHCP por segmento, forwarder DNS y concentrador
WireGuard. Tiene que poder gestionarse por API o por fichero de configuración declarativo,
porque la matriz de flujos se deriva de `flows.yml` y las reglas no se escriben a mano.
Corre como VM con 2 GB de RAM dentro de un Proxmox anidado, así que el coste de memoria
del sistema base importa. También cuenta la evidencia visual: el proyecto es material de
portfolio y un dashboard con logging de denegaciones en vivo comunica más rápido que un
`show log`.

## Decisión

OPNsense 25.x, instalado sobre UFS (no ZFS: el ARC es agresivo con la memoria y en un
hipervisor anidado no compensa). Las reglas se gestionan preferentemente vía API con el
plugin `os-firewall`, partiendo siempre de un export real de `config.xml`.

## Alternativas descartadas

- VyOS: configuración declarativa excelente y ligera, pero la imagen estable requiere
  suscripción o compilación propia, no trae GUI (peor evidencia visual para el README) y
  el DHCP/VPN se configura en el mismo plano que el firewall, con menos separación.
- pfSense CE: equivalente funcional, pero su cadencia de releases y el historial
  reciente del proyecto lo hacen menos atractivo que su fork; la API REST nativa es de pago.
- firewalld/nftables en un Linux pelado: máxima transparencia, pero habría que
  construir DHCP, DNS, VPN y logging a mano, y el objetivo del lab es la matriz verificada.

## Consecuencias

- El instalador de OPNsense es interactivo: la instalación inicial es manual y documentada,
  el resto se automatiza contra la API.
- FreeBSD no tiene buen soporte de ballooning: la VM del firewall va con memoria fija.
- El `config.xml` exportado contiene secretos: entra en juego `scripts/sanitize.py` y el
  hook de pre-commit antes de versionar nada.
