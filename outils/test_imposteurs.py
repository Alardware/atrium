"""Aucune signature ne doit se contenter d une reponse quelconque.

Un service inconnu — l ingress de Home Assistant, une page d accueil, un 404
de reverse proxy — repond a toutes les adresses qu on lui demande. Une
detection qui accepte « le serveur a repondu » comme preuve d identite se
trompe alors de service, et l interface reclame la cle d une API qui n existe
pas. C est arrive deux fois : UniFi sur un 401, Pi-hole sur le mot « version ».

Ce test dresse trois imposteurs devant tout le catalogue et exige un verdict
vide. Il verifie ensuite que les vrais services, eux, restent reconnus.

Sortie : 0 si aucune signature ne mord, 1 sinon.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import services  # noqa: E402

ECHECS = []

PAGE_HA = (b"<!DOCTYPE html><html><head><title>Home Assistant</title></head>"
           b"<body><home-assistant></home-assistant>"
           b"<script>window.version='2026.8.1';</script></body></html>")


def servir(repondre):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _rep(self):
            code, type_contenu, corps = repondre(self.path)
            self.send_response(code)
            self.send_header("Content-Type", type_contenu)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        do_GET = _rep
        do_POST = _rep

    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.server_address[1]


# --- les imposteurs ----------------------------------------------------------
def ingress(chemin):
    """Home Assistant derriere son ingress : 401 partout, en JSON."""
    return 401, "application/json", json.dumps({"message": "401: Unauthorized"}).encode()


def accueil(chemin):
    """Une application a page unique : la meme page pour toute adresse."""
    return 200, "text/html", PAGE_HA


def refus(chemin):
    """Un reverse proxy qui ne connait pas la route."""
    return 404, "text/html", b"<html><head><title>404 Not Found</title></head></html>"


def api_polie(chemin):
    """Une API qui dit oui a tout, sans rien nommer."""
    return 200, "application/json", json.dumps({"ok": True, "status": "ok"}).encode()


# --- les vrais, pour ne pas casser la detection en la durcissant --------------
def vrai_pihole(chemin):
    if chemin.startswith("/admin/api.php"):
        return 200, "application/json", json.dumps(
            {"status": "enabled", "dns_queries_today": 1234}).encode()
    return 404, "text/html", b""


def vrai_adguard(chemin):
    if chemin == "/control/status":
        return 200, "application/json", json.dumps(
            {"version": "v0.107", "dns_addresses": ["192.168.0.1"], "running": True}).encode()
    return 404, "text/html", b""


def vrai_unifi(chemin):
    if chemin == "/status":
        return 200, "application/json", json.dumps(
            {"meta": {"rc": "ok", "up": True, "server_version": "8.0.28"}, "data": []}).encode()
    return 404, "text/html", b""


def vrai_glances(chemin):
    corps = {"cpu": {"total": 3.0}, "mem": {"percent": 24.0}, "status": {"version": "4.0.4"}}
    greffon = chemin.rsplit("/", 1)[-1]
    if chemin.startswith("/api/4/") and greffon in corps:
        return 200, "application/json", json.dumps(corps[greffon]).encode()
    return 404, "text/html", b""


def vrai_ha(chemin):
    if chemin == "/api/":
        return 200, "application/json", json.dumps({"message": "API running."}).encode()
    return 401, "application/json", b'{"message":"Unauthorized"}'


def vrai_sonarr(chemin):
    if chemin.startswith("/api/v3/system/status"):
        return 200, "application/json", json.dumps(
            {"appName": "Sonarr", "version": "4.0.9"}).encode()
    return 404, "text/html", b""


def glances_protege(chemin, entetes=None):
    """Le module Glances de Home Assistant : nginx exige un compte HA."""
    return 401, "text/html", b"<html><head><title>401 Authorization Required</title></head></html>"


def vrai_syncthing(chemin):
    if chemin == "/rest/noauth/health":
        return 200, "application/json", json.dumps({"status": "OK"}).encode()
    return 403, "text/plain", b"CSRF Error"


def vrai_portainer(chemin):
    if chemin == "/api/status":
        return 200, "application/json", json.dumps(
            {"Version": "2.21.0", "InstanceID": "x"}).encode()
    return 404, "text/html", b""


def main():
    imposteurs = [("ingress Home Assistant (401 partout)", ingress),
                  ("page unique (200 partout)", accueil),
                  ("reverse proxy (404 partout)", refus),
                  ("API qui dit oui a tout", api_polie)]
    vrais = [("Pi-hole", vrai_pihole, "pihole"),
             ("AdGuard Home", vrai_adguard, "adguard"),
             ("UniFi", vrai_unifi, "unifi"),
             ("Glances", vrai_glances, "glances"),
             ("Home Assistant", vrai_ha, "ha"),
             ("Sonarr", vrai_sonarr, "sonarr"),
             ("Portainer", vrai_portainer, "portainer"),
             ("Syncthing", vrai_syncthing, "syncthing")]

    print("1. aucun imposteur ne doit etre reconnu")
    for titre, fn in imposteurs:
        s, url = servir(fn)
        try:
            vu = services.identifier(url, "")
            type_vu = vu.get("type") or ""
            nom_vu = vu.get("nom") or ""
            ok = not type_vu
            print("    %-38s %s" % (titre, "aucun service reconnu" if ok
                                    else "!!! reconnu comme %s (%s)" % (type_vu, nom_vu)))
            if not ok:
                ECHECS.append("%s -> %s" % (titre, type_vu))
        finally:
            s.shutdown()

    print("2. l adresse d un module vue depuis Home Assistant se nomme")
    s, url = servir(accueil)
    try:
        vu = services.identifier(url, "")
        verdict = vu.get("indice") or "(aucun indice)"
        ok = verdict == "ha_frontend"
        print("    %-38s %s" % ("page de Home Assistant reconnue comme telle",
                                verdict if ok else "!!! " + verdict))
        if not ok:
            ECHECS.append("indice ha_frontend absent")
    finally:
        s.shutdown()

    print("3. les vrais services restent reconnus")
    for titre, fn, attendu in vrais:
        s, url = servir(fn)
        try:
            vu = services.identifier(url, "")
            ok = (vu.get("type") or "") == attendu
            print("    %-38s %s" % (titre, vu.get("type") or "(aucun)"
                                    if ok else "!!! %s au lieu de %s"
                                    % (vu.get("type") or "(aucun)", attendu)))
            if not ok:
                ECHECS.append("%s non reconnu" % titre)
        finally:
            s.shutdown()

    print("4. un service protege se detecte avec sa cle, pas sans")
    import base64 as _b64
    attendu = "Basic " + _b64.b64encode(b"guillaume:secret").decode()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.headers.get("Authorization") != attendu:
                corps = b"<html><title>401 Authorization Required</title></html>"
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Home Assistant Authentication"')
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)
                return
            code, type_contenu, corps = vrai_glances(self.path)
            self.send_response(code)
            self.send_header("Content-Type", type_contenu)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        sans = services.identifier(url, "")
        avec = services.identifier(url, "guillaume:secret")
        print("    %-38s %s" % ("sans la cle", sans.get("type") or "(aucun)"))
        print("    %-38s %s" % ("avec « utilisateur:secret »", avec.get("type") or "(aucun)"))
        if (avec.get("type") or "") != "glances":
            ECHECS.append("service protege non detecte avec sa cle")
        if sans.get("type"):
            ECHECS.append("service protege detecte sans cle")
    finally:
        srv.shutdown()

    print()
    if ECHECS:
        print("RESUME : %d signature(s) trop indulgente(s)" % len(ECHECS))
        for e in ECHECS:
            print("  - " + e)
        return 1
    print("RESUME : les signatures demandent une vraie preuve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
