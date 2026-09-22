# ADR 0006: firewalld en los hosts y verificación de la política combinada

- Estado: aceptada
- Fecha: 2026-07-25

## Contexto

Con OPNsense en el perímetro y firewalld en cada AlmaLinux hay dos capas de filtrado
independientes. Se podría desactivar firewalld "para simplificar", pero eso no refleja
ninguna red real: la defensa en profundidad implica que el host no confía ciegamente en
el perímetro. La contrapartida está en el diagnóstico: cuando una sonda falla, hay que
saber si bloqueó el firewall perimetral o el host.

## Decisión

firewalld queda activo en todos los servidores con los servicios mínimos abiertos, y la
suite verifica la política efectiva combinada, que es la que protege. Para que cada
fallo sea atribuible, el reporte consulta el log de denegaciones de OPNsense: si la
denegación aparece ahí, bloqueó el perímetro; si la sonda murió sin rastro en el
perímetro, bloqueó (o no escuchaba) el host. `docs/matriz-flujos.md` refleja esa
atribución por celda.

## Alternativas descartadas

- Desactivar firewalld y verificar solo OPNsense: la matriz saldría más "limpia",
  pero certificaría una política que ningún host real ejecuta; además elimina el material
  de SELinux/firewalld que es objetivo del lab.
- Verificar cada capa por separado con dos suites: duplica el coste de ejecución y
  no responde a la pregunta operativa ("¿puede A hablar con B?"); la atribución por log
  da el mismo diagnóstico a mitad de precio.

## Consecuencias

- El rol `common` de Ansible gestiona firewalld de forma declarativa (servicios/puertos
  por grupo de hosts), y los permisos de la matriz deben abrirse en las dos capas para
  que un `allow` pase.
- El reporte necesita acceso de solo lectura al log del firewall: se usa la API de
  OPNsense, y ese acceso se configura en la fase 3.
- Un `allow` de la matriz que falla por firewalld se trata como un hallazgo: significa
  que la política del host y la del perímetro divergen.
