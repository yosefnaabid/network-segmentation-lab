# ADR 0005: AlmaLinux 9 frente a Debian para los servidores

- Estado: aceptada
- Fecha: 2026-07-25

## Contexto

Uno de los objetivos del laboratorio es trabajar con SELinux en modo enforcing:
diagnosticar denegaciones con `ausearch`/`audit2allow` y resolverlas con `semanage`,
`setsebool` y `restorecon`, sin desactivarlo. También interesa que el perfil de sistema
se parezca al que domina en entornos corporativos (familia RHEL: firewalld, dnf,
postgresql-setup). El material de "lo que no funcionó a la primera" sale en gran parte
de ahí.

## Decisión

AlmaLinux 9 en todas las VMs de servicio (`srv-dns`, `srv-app`, `dmz-web`, `jump01`),
con SELinux enforcing y firewalld activo. Los clientes de prueba son LXC de AlmaLinux 9
y Alpine (ver ADR 0007). El nodo de control es Ubuntu por conveniencia de toolchain y
no forma parte de la red bajo prueba.

## Alternativas descartadas

- Debian en servidores: excelente base, pero su SELinux es de segunda clase
  (AppArmor es lo nativo) y perdería el objetivo formativo; además difiere en nombres
  (`bind9` vs `bind`, initdb automático de PostgreSQL) del mundo RHEL que se quiere
  practicar.
- Rocky Linux: equivalente funcional a AlmaLinux; la elección entre ambos es
  cosmética y se resolvió por familiaridad con las imágenes cloud de Alma.
- CentOS Stream: rolling respecto a RHEL; para un lab reproducible interesa una
  base estable.

## Consecuencias

- Peculiaridades a recordar: paquete `bind` y servicio `named` (config en
  `/etc/named.conf`, zonas en `/var/named/`), `postgresql-setup --initdb` manual,
  gestor `dnf`, firewalld como segunda capa de filtrado (ADR 0006).
- Las imágenes GenericCloud de Alma traen `qemu-guest-agent` y `virtio_balloon`, lo que
  habilita la ejecución fuera de banda (`qm guest exec`) y el ballooning del spec §4.
- SELinux enforcing implica trabajo real de política en los roles de Ansible
  (`semanage port`, `setsebool`, `restorecon`) y prohíbe el atajo de permissive.
