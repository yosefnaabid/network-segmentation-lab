"""Cliente de la GUI de OPNsense para lo que aun no tiene API MVC.

En 25.7 no existe API para el NAT de destino (port forward): esa pagina sigue
siendo legacy. En vez de escribir el XML a mano -que es justo lo que el
proyecto prohibe (no inventar el esquema)-, se envia el mismo formulario que
manda el navegador, dejando que OPNsense valide y escriba su propia
estructura.

Detalle: el campo anti-CSRF tiene NOMBRE ALEATORIO por sesion, pero la
comprobacion tambien acepta la cabecera X-CSRFToken, que es lo que se usa.
"""

from __future__ import annotations

import http.cookiejar
import os
import re
import ssl
import urllib.parse
import urllib.request

HIDDEN = re.compile(r'<input\s+type="hidden"\s+name="([^"]+)"\s+value="([^"]+)"')
TOKEN_JS = re.compile(r'X-CSRFToken",\s*"([^"]+)"')


class Gui:
    def __init__(self, url: str | None = None, usuario: str = "root", clave: str | None = None):
        self.base = (url or os.environ["OPNSENSE_URL"]).rstrip("/")
        self.usuario = usuario
        self.clave = clave or os.environ.get("OPNSENSE_GUI_PASS", "opnsense")
        self._csrf = ""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=ctx),
        )
        self.opener.addheaders = [
            ("User-Agent", "netseg-lab/1.0"),
            ("Referer", self.base + "/"),
        ]

    def get(self, ruta: str) -> str:
        with self.opener.open(self.base + ruta, timeout=30) as r:
            return r.read().decode("utf-8", "replace")

    def post(self, ruta: str, datos: dict | list) -> str:
        cuerpo = urllib.parse.urlencode(datos, doseq=True).encode()
        req = urllib.request.Request(self.base + ruta, data=cuerpo, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if self._csrf:
            req.add_header("X-CSRFToken", self._csrf)
        with self.opener.open(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")

    def login(self) -> bool:
        html = self.get("/")
        campo = HIDDEN.search(html)
        datos = {campo.group(1): campo.group(2)} if campo else {}
        datos.update({"usernamefld": self.usuario, "passwordfld": self.clave, "login": "1"})
        respuesta = self.post("/index.php", datos)
        if "passwordfld" in respuesta:
            return False
        token = TOKEN_JS.search(respuesta) or TOKEN_JS.search(self.get("/firewall_nat.php"))
        self._csrf = token.group(1) if token else ""
        return True
