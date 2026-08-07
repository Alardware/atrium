#!/usr/bin/env python3
"""Atrium — serveur de l'application.

Sert l'interface, stocke la configuration et relaie les appels vers les API des
services (Plex, Unraid, UniFi, Home Assistant, ...) que le navigateur ne peut pas
joindre directement (CORS absent, certificats auto-signes).

Variables d'environnement :
  ATRIUM_PORT        port d'ecoute                (defaut 8420)
  ATRIUM_CONFIG_DIR  dossier de la configuration  (defaut /config, sinon ./data)
  ATRIUM_ALLOW_NET   prefixes reseau autorises pour le relais, separes par des
                     virgules (defaut : reseaux prives RFC1918 + loopback)
"""
import http.server
import json
import os
import socketserver
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

PORT = int(os.environ.get("ATRIUM_PORT", "8420"))
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

_default_cfg = "/config" if os.path.isdir("/config") else os.path.join(HERE, "..", "data")
CONFIG_DIR = os.path.abspath(os.environ.get("ATRIUM_CONFIG_DIR", _default_cfg))
CONFIG_FILE = os.path.join(CONFIG_DIR, "atrium.json")

_DEFAULT_ALLOW = "192.168.,10.,172.16.,172.17.,172.18.,172.19.,172.20.,172.21.,172.22.,172.23.,172.24.,172.25.,172.26.,172.27.,172.28.,172.29.,172.30.,172.31.,127.,localhost,host.docker.internal,homeassistant"
ALLOW_HOSTS = tuple(h.strip() for h in os.environ.get("ATRIUM_ALLOW_NET", _DEFAULT_ALLOW).split(",") if h.strip())

FORWARD_HEADERS = ("x-api-key", "authorization", "content-type", "accept", "x-plex-token")


def host_allowed(url):
    """N'autorise le relais que vers le reseau local : le proxy ne doit pas
    devenir un rebond vers l'exterieur."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return False
    return any(host == h or host.startswith(h) for h in ALLOW_HOSTS)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC, **kwargs)

    def log_message(self, fmt, *args):
        if os.environ.get("ATRIUM_DEBUG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---------- utilitaires ----------
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def reply(self, code, data, ctype="application/json"):
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------- routage ----------
    def do_GET(self):
        route = self.path.split("?")[0]
        if self.path.startswith("/px?"):
            self.proxy()
        elif route in ("/cfg", "/api/config"):
            self.cfg_get()
        elif route == "/api/health":
            self.reply(200, json.dumps({"ok": True, "app": "atrium"}).encode())
        else:
            super().do_GET()

    def do_POST(self):
        route = self.path.split("?")[0]
        if self.path.startswith("/px?"):
            self.proxy()
        elif route in ("/cfg", "/api/config"):
            self.cfg_post()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", ", ".join(FORWARD_HEADERS))
        self.send_header("Access-Control-Max-Age", "3600")
        self.end_headers()

    # ---------- configuration ----------
    def cfg_get(self):
        try:
            with open(CONFIG_FILE, "rb") as f:
                data = f.read()
        except OSError:
            data = b"{}"
        self.reply(200, data)

    def cfg_post(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            json.loads(body.decode("utf-8"))  # refuse d'ecrire un JSON invalide
        except (ValueError, UnicodeDecodeError) as e:
            self.reply(400, json.dumps({"error": "json invalide: %s" % e}).encode())
            return
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = CONFIG_FILE + ".tmp"
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, CONFIG_FILE)  # ecriture atomique
            self.reply(200, b'{"ok":true}')
        except OSError as e:
            self.reply(500, json.dumps({"error": str(e)}).encode())

    # ---------- relais vers les API des services ----------
    def proxy(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        target = (query.get("u") or [""])[0]
        if not target.startswith(("http://", "https://")):
            self.reply(400, b"cible invalide", "text/plain")
            return
        if not host_allowed(target):
            self.reply(403, b"cible hors reseau autorise", "text/plain")
            return

        body = None
        if self.command == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""

        req = urllib.request.Request(target, data=body, method=self.command)
        for h in FORWARD_HEADERS:
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # equipements locaux : certificats auto-signes
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                data, code = r.read(), r.status
                ctype = r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            data, code = e.read(), e.code
            ctype = e.headers.get("Content-Type", "text/plain")
        except Exception as e:
            data, code, ctype = str(e).encode(), 502, "text/plain"
        self.reply(code, data, ctype)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    os.makedirs(CONFIG_DIR, exist_ok=True)
    print("Atrium — http://0.0.0.0:%d" % PORT, flush=True)
    print("Configuration : %s" % CONFIG_FILE, flush=True)
    Server(("0.0.0.0", PORT), Handler).serve_forever()
