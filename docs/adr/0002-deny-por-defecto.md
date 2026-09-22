# ADR 0002: Denegación por defecto y `flows.yml` solo con `allow`

- Estado: aceptada
- Fecha: 2026-07-25

## Contexto

La tesis del proyecto es que entre segmentos solo pasa el tráfico que el diseño permite
de forma explícita, y que eso se demuestra con tests. Hay dos formas de declarar una
política así: enumerar permisos y denegaciones, o enumerar solo permisos y tratar todo lo
demás como denegado. Enumerar denegaciones a mano tiene un defecto de fondo: solo se
verifica lo que a alguien se le ocurrió escribir, y cada hueco queda sin probar en
silencio.

## Decisión

Política `deny all` con logging en todas las interfaces del firewall. `opnsense/flows.yml`
declara únicamente los `allow`, cada uno con un `reason` que explica el porqué (no el
qué). Las denegaciones esperadas se derivan por producto cartesiano
(origen × host destino × puertos de sondeo) restando lo permitido, tanto para generar la
suite de tests como la matriz publicada. Añadir una entrada `deny` al fichero es un error
de esquema.

## Alternativas descartadas

- Matriz completa allow+deny en el fichero: duplica información, se desincroniza y
  convierte la suite en "comprobé unos cuantos bloqueos" en vez de una verificación
  exhaustiva por construcción.
- Confiar en la política por defecto sin tests de bloqueo: una regla `any→any`
  olvidada pasaría desapercibida, y es justo el fallo que `make chaos` demuestra
  que detectamos.

## Consecuencias

- Salen ~300 comprobaciones derivadas; con `drop` silencioso cada denegación cuesta un
  timeout, así que la suite usa timeout de 1 s, agrupa la batería por host origen
  (~9 invocaciones) y paraleliza por origen.
- La matriz publicada en `docs/matriz-flujos.md` es un artefacto generado: editarla a
  mano no tiene sentido y la CI/su generador la pisará.
- Si las reglas vivas y `flows.yml` divergen, es un bug detectable (`make check`).
