# ADR 0003: DNS interno obligatorio (sin salida DNS directa)

- Estado: aceptada
- Fecha: 2026-07-25

## Contexto

En una red segmentada, el DNS directo a Internet es una vía de exfiltración clásica
(túneles DNS) y un punto ciego para el control de la política: si cada cliente resuelve
contra 8.8.8.8, nadie ve qué se resuelve ni puede aplicarse una zona interna coherente.
El laboratorio además necesita una zona propia (`lab.interno`) con vistas directa e
inversa para que los tests y el bastión trabajen con nombres y no con IPs sueltas.

## Decisión

Unbound en el firewall actúa de forwarder para todos los segmentos; `srv-dns` (BIND,
paquete `bind`, servicio `named`) es autoritativo de `lab.interno` con su zona inversa.
Los clientes solo pueden resolver contra el firewall o contra `srv-dns`: el puerto 53
hacia WAN está bloqueado para todos los segmentos, incluido IOT, que queda obligado a
usar el resolver interno.

## Alternativas descartadas

- DNS público directo por cliente: sin visibilidad, sin zona interna, y deja un canal
  de salida sin control que contradice la tesis del lab.
- BIND como resolver general además de autoritativo: mezclaría los roles de resolución
  y autoridad en el mismo servicio; el forwarder del firewall ya existe de serie y separa
  responsabilidades.

## Consecuencias

- Las entradas ② y ④ de la matriz no incluyen 53/udp hacia WAN, y la suite verifica que
  ese puerto está efectivamente bloqueado desde cada segmento.
- La zona `lab.interno` y su inversa forman parte del rol `named` de Ansible.
- Un fallo del resolver interno tira la resolución de todo el lab: aceptable en
  laboratorio y detectable por `test_services.py`.
