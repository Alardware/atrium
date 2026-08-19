# -*- coding: utf-8 -*-
"""Nginx Proxy Manager : reconnu a sa reponse, lu avec un compte.

Les versions 2.x ne portent plus leur nom dans « /api/ » : elles rendent un
etat, un drapeau d installation et une version en trois nombres. La signature
qui cherchait la chaine « Nginx Proxy Manager » ne trouvait donc plus rien, et
le service restait « joignable, non reconnu » avec une API pourtant intacte.

Ce test monte un faux NPM au format d aujourd hui, un autre au format d hier,
et un service quelconque qui repond « OK » — celui-la ne doit pas passer pour
un proxy.

Sortie : 0 si la reconnaissance et la lecture tiennent, 1 sinon.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RACINE, "app"))

import services   # noqa: E402
import widgets    # noqa: E402

COMPTE, SECRET = "admin@exemple.fr", "motdepasse"
ECHECS = []


def verifier(titre, obtenu, attendu):
    ok = obtenu == attendu
    print("    %-44s %-30s %s" % (titre, obtenu, "" if ok else "!!! attendu %s" % (attendu,)))
    if not ok:
        ECHECS.append(titre)


def servir(racine, hotes=None):
    """Un NPM reduit a ce qu Atrium lui demande : sa carte d identite, un
    jeton contre un compte, et la liste des hotes servis."""
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _rendre(self, code, corps):
            brut = json.dumps(corps).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(brut)))
            self.end_headers()
            self.wfile.write(brut)

        def do_GET(self):
            if self.path == "/api/":
                self._rendre(200, racine)
            elif self.path == "/api/nginx/proxy-hosts":
                if self.headers.get("Authorization") != "Bearer jeton-npm":
                    self._rendre(403, {"error": "forbidden"})
                else:
                    self._rendre(200, hotes or [])
            else:
                self._rendre(404, {})

        def do_POST(self):
            taille = int(self.headers.get("Content-Length") or 0)
            envoye = json.loads(self.rfile.read(taille) or b"{}")
            if self.path != "/api/tokens":
                self._rendre(404, {})
            elif envoye.get("identity") == COMPTE and envoye.get("secret") == SECRET:
                self._rendre(200, {"token": "jeton-npm", "expires": "2026-12-31T00:00:00.000Z"})
            else:
                self._rendre(401, {"error": {"message": "Invalid email or password"}})

    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.server_address[1]


# La reponse d un NPM 2.15, telle qu elle arrive vraiment.
MODERNE = {"status": "OK", "setup": True,
           "version": {"major": 2, "minor": 15, "revision": 1}}
# Celle des versions plus anciennes, qui se nommaient.
ANCIEN = {"status": "OK", "app": "Nginx Proxy Manager", "version": "2.9.18"}
HOTES = [{"id": 1, "domain_names": ["maison.exemple.fr"], "enabled": True},
         {"id": 2, "domain_names": ["photos.exemple.fr"], "enabled": True},
         {"id": 3, "domain_names": ["vieux.exemple.fr"], "enabled": False}]


def main():
    print("\n== un NPM d aujourd hui ==")
    srv, url = servir(MODERNE, HOTES)
    try:
        trouve = services.identifier(url)
        verifier("il est reconnu", trouve.get("type"), "npm")
        verifier("et nomme", trouve.get("nom"), "Nginx Proxy Manager")
        verifier("avec sa version", trouve.get("version"), "2.15.1")
        verifier("la fiche annonce qu un compte est requis",
                 bool(trouve.get("cle_libelle")), True)

        print("\n== ce que la tuile affiche ==")
        verifier("sans compte, rien n est invente", widgets.w_npm(url, ""), None)
        verifier("un mauvais compte non plus",
                 widgets.w_npm(url, "%s:faux" % COMPTE), None)
        stats = {m["id"]: m["val"] for m in (widgets.w_npm(url, "%s:%s" % (COMPTE, SECRET)) or [])}
        print("    " + " · ".join("%s %s" % (k, v) for k, v in stats.items()))
        verifier("les hotes servis", stats.get("hotes"), "3")
        verifier("dont ceux qui sont eteints", stats.get("arretes"), "1")
    finally:
        srv.shutdown()

    print("\n== un NPM d hier, qui se nommait ==")
    srv, url = servir(ANCIEN, HOTES)
    try:
        verifier("reconnu lui aussi", services.identifier(url).get("type"), "npm")
    finally:
        srv.shutdown()

    print("\n== un service quelconque qui repond « OK » ==")
    srv, url = servir({"status": "OK"})
    try:
        verifier("n est pas pris pour un proxy",
                 services.identifier(url).get("type"), "")
    finally:
        srv.shutdown()

    print()
    if ECHECS:
        print("RESUME : %d point(s) a corriger" % len(ECHECS))
        for e in ECHECS:
            print("  - " + e)
        return 1
    print("RESUME : Nginx Proxy Manager est reconnu, et lu quand on lui donne un compte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
