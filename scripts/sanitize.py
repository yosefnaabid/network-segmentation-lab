#!/usr/bin/env python3
"""Limpia secretos de un export ``config.xml`` de OPNsense antes de commitear.

Uso:
    scripts/sanitize.py fichero.xml [...]    # sanea in situ
    scripts/sanitize.py --check fichero.xml  # solo comprueba; rc=1 si hay secretos
    git show :opnsense/config.xml | scripts/sanitize.py --check -   # desde stdin

El hook ``hooks/pre-commit`` usa el modo ``--check`` para bloquear commits con
hashes de contraseñas, claves privadas de WireGuard, claves de API, comunidades
SNMP o PSKs. La lista de etiquetas es deliberadamente amplia: ante la duda, un
falso positivo se revisa a mano; un falso negativo acaba en el historial de git.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PLACEHOLDER = "***SANITIZADO***"

# Etiquetas cuyo TEXTO se considera secreto alli donde aparezcan.
SENSITIVE_TAGS = {
    "password",
    "passwd",
    "pass",
    "apikey",
    "apisecret",
    "secret",
    "privatekey",
    "private-key",
    "privkey",
    "prv",  # clave privada de certificados en config.xml
    "community",  # comunidades SNMP
    "presharedkey",
    "preshared-key",
    "psk",
    "authkey",
    "radius_secret",
    "lighttpd_ls_password",
}


def _iter_with_paths(root: ET.Element):
    """Recorre el arbol devolviendo (elemento, ruta legible)."""
    stack = [(root, "/" + root.tag)]
    while stack:
        elem, path = stack.pop()
        yield elem, path
        for child in elem:
            stack.append((child, f"{path}/{child.tag}"))


def find_secrets(root: ET.Element) -> list[str]:
    """Devuelve las rutas de los elementos que aun contienen secretos."""
    found = []
    for elem, path in _iter_with_paths(root):
        tag = elem.tag.lower()
        text = (elem.text or "").strip()
        if tag in SENSITIVE_TAGS and text and text != PLACEHOLDER:
            found.append(path)
    return sorted(found)


def sanitize_tree(root: ET.Element) -> int:
    """Sustituye el texto de los elementos sensibles. Devuelve cuantos cambio."""
    changed = 0
    for elem, _path in _iter_with_paths(root):
        tag = elem.tag.lower()
        text = (elem.text or "").strip()
        if tag in SENSITIVE_TAGS and text and text != PLACEHOLDER:
            elem.text = PLACEHOLDER
            changed += 1
    return changed


def _parse(source: str) -> ET.ElementTree:
    if source == "-":
        return ET.ElementTree(ET.fromstring(sys.stdin.read()))
    return ET.parse(source)


def main(argv: list[str]) -> int:
    args = list(argv)
    check_only = "--check" in args
    if check_only:
        args.remove("--check")
    if not args:
        print(__doc__, file=sys.stderr)
        return 2

    rc = 0
    for source in args:
        try:
            tree = _parse(source)
        except (ET.ParseError, OSError) as exc:
            print(f"sanitize: no puedo leer {source}: {exc}", file=sys.stderr)
            return 2
        root = tree.getroot()
        if check_only:
            secrets = find_secrets(root)
            if secrets:
                rc = 1
                print(f"sanitize: {source} contiene {len(secrets)} secreto(s):")
                for path in secrets:
                    print(f"  - {path}")
        else:
            changed = sanitize_tree(root)
            if source == "-":
                tree.write(sys.stdout.buffer, encoding="utf-8", xml_declaration=True)
            else:
                tree.write(source, encoding="utf-8", xml_declaration=True)
                # se conserva el salto de linea final que git espera
                with Path(source).open("ab") as fh:
                    fh.write(b"\n")
            print(f"sanitize: {source}: {changed} valor(es) sustituidos por {PLACEHOLDER}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
