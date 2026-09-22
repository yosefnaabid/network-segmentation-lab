"""Traduccion de flows.yml a reglas de firewall de OPNsense.

Funcion pura y testeable sin lab: dado el contenido de flows.yml devuelve la
lista de reglas que DEBEN existir en el firewall. Tanto `apply.py` (que las
escribe) como `make check` (que detecta deriva) usan esta misma traduccion,
de modo que no puedan divergir entre si.

Convenios:
  - Cada regla lleva en la descripcion el prefijo netseg:<id> para poder
    reconocer las reglas gestionadas y no tocar las creadas a mano.
  - Solo se generan reglas `pass`: la denegacion es la politica por defecto
    del firewall (ADR 0002), no una regla escrita.
  - El sentido es siempre `in` sobre la interfaz de ORIGEN: es donde el
    trafico entra al firewall y donde OPNsense evalua el filtro.
  - Palabras reservadas: WAN (Internet) y FIREWALL (los servicios del propio
    cortafuegos: resolver y hora del laboratorio).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

WAN = "WAN"
FIREWALL = "FIREWALL"
PREFIJO = "netseg"
# Como se refiere OPNsense a "esta maquina" en el destino de una regla.
DESTINO_FIREWALL = "(self)"
# Alias con todas las redes del laboratorio. "Salir a Internet" se traduce como
# "a cualquier sitio MENOS las redes internas": con un destino `any` a secas, un
# permiso de navegacion abre tambien el camino hacia los demas segmentos
# (incidencia #19, detectada por la propia suite).
ALIAS_INTERNAS = "netseg_redes_internas"


@dataclass(frozen=True, order=True)
class Regla:
    """Una regla de filtro tal y como la espera la API de automatizacion."""

    descripcion: str
    interfaz: str
    protocolo: str
    origen: str
    destino: str
    puerto_destino: str
    accion: str = "pass"
    direccion: str = "in"
    version_ip: str = "inet"
    log: str = "1"
    destino_negado: str = "0"

    def como_payload(self) -> dict:
        return {
            "enabled": "1",
            "action": self.accion,
            "quick": "1",
            "interface": self.interfaz,
            "direction": self.direccion,
            "ipprotocol": self.version_ip,
            "protocol": self.protocolo,
            "source_net": self.origen,
            "destination_net": self.destino,
            "destination_not": self.destino_negado,
            "destination_port": self.puerto_destino,
            "log": self.log,
            "description": self.descripcion,
        }

    def clave(self) -> tuple:
        """Identidad de la regla para comparar contra las reglas vivas."""
        return (
            self.descripcion,
            self.interfaz,
            self.protocolo,
            self.origen,
            self.destino,
            self.puerto_destino,
            self.accion,
            self.destino_negado,
        )


def _puertos(puertos: list[str]) -> list[tuple[str, str]]:
    """['445/tcp','53/udp'] -> [('TCP','445'), ('UDP','53')].

    Una regla por puerto: el campo de puerto de OPNsense admite un valor, un
    rango o un alias, pero NO una lista separada por comas (incidencia #16).
    Ademas asi cada permiso de la matriz es rastreable regla a regla.
    """
    return [
        (proto.upper(), numero)
        for numero, proto in (entrada.split("/", 1) for entrada in puertos)
    ]


def origenes_de(permiso: dict) -> list[str]:
    """src admite un segmento, un host o una lista de segmentos."""
    src = permiso["src"]
    return list(src) if isinstance(src, list) else [src]


def generar(flows: dict, interfaces: dict[str, str]) -> list[Regla]:
    """Devuelve las reglas que deben existir.

    `interfaces` mapea nombre de segmento -> interfaz de OPNsense
    (p.ej. {'USERS': 'opt1', ...}); WAN se traduce a la interfaz 'wan'.
    Los origenes cuyo segmento aun no tiene interfaz se omiten y se pueden
    consultar con flujos_pendientes().
    """
    segmentos = flows["segments"]
    hosts = flows["hosts"]
    reglas: list[Regla] = []

    for permiso in flows["allow"]:
        destino = permiso["dst"]

        # Destino: host concreto, red del segmento, Internet o el propio firewall.
        negado = "0"
        if destino == WAN:
            red_destino, negado = ALIAS_INTERNAS, "1"  # "fuera", no "cualquier sitio"
        elif destino == FIREWALL:
            red_destino = DESTINO_FIREWALL
        elif destino in hosts:
            red_destino = f"{hosts[destino]['ip']}/32"
        elif destino in segmentos:
            red_destino = segmentos[destino]["cidr"]
        else:
            raise ValueError(f"{permiso['id']}: destino desconocido {destino}")

        for origen in origenes_de(permiso):
            # Interfaz donde entra el trafico: la del segmento de origen.
            if origen == WAN:
                interfaz, red_origen = "wan", "any"
            elif origen in hosts:
                segmento = hosts[origen]["segment"]
                if segmento not in interfaces:
                    continue  # su interfaz aun no existe
                interfaz, red_origen = interfaces[segmento], f"{hosts[origen]['ip']}/32"
            elif origen in segmentos:
                if origen not in interfaces:
                    continue  # p.ej. VPN antes de que exista WireGuard (fase 6)
                interfaz, red_origen = interfaces[origen], segmentos[origen]["cidr"]
            else:
                raise ValueError(f"{permiso['id']}: origen desconocido {origen}")

            for proto, puerto in _puertos(permiso["ports"]):
                reglas.append(
                    Regla(
                        descripcion=f"{PREFIJO}:{permiso['id']} - {permiso['reason']}",
                        interfaz=interfaz,
                        protocolo=proto,
                        origen=red_origen,
                        destino=red_destino,
                        puerto_destino=puerto,
                        destino_negado=negado,
                    )
                )

    return sorted(reglas)


def flujos_pendientes(flows: dict, interfaces: dict[str, str]) -> list[str]:
    """Ids de flows.yml que aun no pueden traducirse por falta de interfaz.

    No es un error: la VPN no existe hasta la fase 6. Pero tiene que verse,
    porque un flujo declarado y no aplicado es justo el tipo de hueco que el
    proyecto quiere evitar.
    """
    hosts = flows["hosts"]
    pendientes = []
    for permiso in flows["allow"]:
        for origen in origenes_de(permiso):
            if origen == WAN:
                continue
            segmento = hosts[origen]["segment"] if origen in hosts else origen
            if segmento not in interfaces and permiso["id"] not in pendientes:
                pendientes.append(permiso["id"])
    return pendientes


def es_gestionada(descripcion: str) -> bool:
    """True si la regla la gestiona este repo (y por tanto puede reemplazarse)."""
    return descripcion.startswith(f"{PREFIJO}:")


def como_dicts(reglas: list[Regla]) -> list[dict]:
    return [asdict(r) for r in reglas]
