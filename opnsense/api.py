"""Cliente minimo de la API de OPNsense para el laboratorio.

Las credenciales llegan por entorno (~/.netseg-fw.env), nunca por el repo:
    OPNSENSE_URL, OPNSENSE_KEY, OPNSENSE_SECRET

Detalle importante: OPNsense no emite un desafio 401, asi que la
autenticacion basica hay que enviarla de forma PREVENTIVA en cada peticion;
el manejador estandar de urllib no lo hace y siempre devolveria HTML de login.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import urllib.error
import urllib.request


class ApiError(RuntimeError):
    pass


class Opnsense:
    def __init__(self, url: str | None = None, key: str | None = None, secret: str | None = None):
        self.url = (url or os.environ["OPNSENSE_URL"]).rstrip("/")
        key = key or os.environ["OPNSENSE_KEY"]
        secret = secret or os.environ["OPNSENSE_SECRET"]
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # certificado autofirmado del lab
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        cred = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self._auth = f"Basic {cred}"

    def _peticion(self, path: str, payload: dict | None = None) -> dict:
        datos = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + path, data=datos)
        req.add_header("Authorization", self._auth)
        if datos is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=40) as r:
                cuerpo = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise ApiError(f"{path}: HTTP {exc.code} {exc.read()[:200]!r}") from exc
        try:
            return json.loads(cuerpo)
        except json.JSONDecodeError as exc:
            raise ApiError(f"{path}: respuesta no JSON: {cuerpo[:200]!r}") from exc

    def get(self, path: str) -> dict:
        return self._peticion(path)

    def post(self, path: str, payload: dict | None = None) -> dict:
        return self._peticion(path, payload if payload is not None else {})

    # -- utilidades ---------------------------------------------------------
    @staticmethod
    def seleccionado(campo: dict) -> str:
        """Extrae el valor elegido de un enumerado del formato de OPNsense."""
        if not isinstance(campo, dict):
            return str(campo)
        for clave, valor in campo.items():
            if isinstance(valor, dict) and valor.get("selected"):
                return clave
        return ""

    def exigir_ok(self, respuesta: dict, contexto: str) -> dict:
        estado = respuesta.get("result") or respuesta.get("status")
        if estado not in ("ok", "saved", "deleted"):
            raise ApiError(f"{contexto}: respuesta inesperada {json.dumps(respuesta)[:300]}")
        return respuesta
