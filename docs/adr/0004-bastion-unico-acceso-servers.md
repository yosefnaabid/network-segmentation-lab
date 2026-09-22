# ADR 0004: Bastión como único acceso de administración a SERVERS

- Estado: aceptada
- Fecha: 2026-07-25

## Contexto

Si cada administrador (o cada cliente comprometido) puede abrir SSH directamente contra
los servidores, la superficie de ataque de SERVERS es la suma de todos los segmentos.
La práctica estándar es concentrar el acceso administrativo en un punto único auditable.
El lab quiere además que la VPN de acceso remoto aterrice en un sitio controlado y no
en toda la red.

## Decisión

`jump01` (VLAN 50, MGMT) es el único host con SSH alcanzable desde USERS y desde la VPN.
Desde MGMT sí se administra el resto (22/443/3389/161 según la matriz), de modo que el
camino operativo es siempre `cliente → jump01 → destino`. En `jump01`: autenticación
solo por clave, `AllowGroups`, y registro de sesiones.

## Alternativas descartadas

- SSH abierto de USERS a SERVERS: comodidad a cambio de perder el punto único de
  control y auditoría; contradice la matriz (USERS→SERVERS solo 445 y 53).
- VPN con acceso a todas las VLANs: convierte cada portátil remoto en un host
  interno total; la fase 6 verifica explícitamente que la VPN llega a MGMT y solo a MGMT.
- Teleport/Boundary u otro PAM completo: sobredimensionado para un lab cuyo objetivo
  es demostrar el patrón.

## Consecuencias

- MGMT es el segmento más privilegiado y por eso se define por servicios concretos,
  nunca `any→any`, y todo permiso hacia SERVERS se registra.
- `jump01` está dimensionado a 512 MB en fase 1; subirá a 4 GB cuando llegue Wazuh
  (fase 2), donde además hará de colector.
- Los tests de conectividad deben demostrar tanto el camino permitido (USERS→jump01:22
  no está en la matriz: el acceso de USERS es solo a servicios, no SSH) como los bloqueos
  directos USERS→SERVERS:22.
