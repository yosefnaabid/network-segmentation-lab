"""Tests unitarios de scripts/sanitize.py. Corren SIN lab (CI)."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sanitize  # noqa: E402

XML_CON_SECRETOS = """<opnsense>
  <system>
    <user>
      <name>root</name>
      <password>$2y$10$hashDeVerdad</password>
      <apikeys><item><key>k</key><secret>c2VjcmV0bw==</secret></item></apikeys>
    </user>
  </system>
  <wireguard><server><privkey>abc123==</privkey><pubkey>pub456==</pubkey></server></wireguard>
  <snmpd><rocommunity>public</rocommunity><community>interna</community></snmpd>
  <interfaces><lan><if>vtnet0</if></lan></interfaces>
</opnsense>
"""


def _root(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_find_secrets_detecta_los_sensibles():
    found = sanitize.find_secrets(_root(XML_CON_SECRETOS))
    # password, secret, privkey y community: 4. pubkey NO es secreto; <name>,
    # <if> y <rocommunity> (etiqueta distinta) tampoco estan en la lista.
    assert len(found) == 4
    assert any(p.endswith("/password") for p in found)
    assert any(p.endswith("/secret") for p in found)
    assert any(p.endswith("/privkey") for p in found)
    assert any(p.endswith("/community") for p in found)


def test_sanitize_tree_reemplaza_y_es_idempotente():
    root = _root(XML_CON_SECRETOS)
    assert sanitize.sanitize_tree(root) == 4
    assert sanitize.find_secrets(root) == []
    textos = {e.tag: (e.text or "") for e in root.iter()}
    assert textos["password"] == sanitize.PLACEHOLDER
    assert textos["pubkey"] == "pub456=="  # lo publico se conserva
    # segunda pasada: nada que hacer
    assert sanitize.sanitize_tree(root) == 0


def test_xml_limpio_pasa_el_check():
    limpio = "<opnsense><interfaces><lan><if>vtnet0</if></lan></interfaces></opnsense>"
    assert sanitize.find_secrets(_root(limpio)) == []
