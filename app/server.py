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
import http.cookies
import http.server
import json
import os
import socketserver
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

import auth

PORT = int(os.environ.get("ATRIUM_PORT", "8420"))
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

_default_cfg = "/config" if os.path.isdir("/config") else os.path.join(HERE, "..", "data")
CONFIG_DIR = os.path.abspath(os.environ.get("ATRIUM_CONFIG_DIR", _default_cfg))
CONFIG_FILE = os.path.join(CONFIG_DIR, "atrium.json")
SESSION_FILE = os.path.join(CONFIG_DIR, "sessions.json")

SESSIONS = auth.Sessions(SESSION_FILE)
LIMITEUR = auth.Limiteur()

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

    def end_headers(self):
        # L'interface ne doit jamais rester en cache : sinon une mise a jour du
        # conteneur laisse tourner l'ancien code dans le navigateur.
        if self.path.split("?")[0].rstrip("/") in ("", "/index.html") or self.path.endswith(".html"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    # ---------- utilitaires ----------
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def reply(self, code, data, ctype="application/json", cookie=None):
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def json_recu(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length or length > 20 * 1024 * 1024:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    # ---------- etat / session ----------
    def lire_config(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def msg_ecriture(self, e):
        return ("Impossible d'écrire la configuration dans %s (%s). "
                "Vérifiez les droits du volume monté sur /config." % (CONFIG_DIR, e))

    def ecrire_config(self, cfg):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass

    def session_user(self):
        brut = self.headers.get("Cookie")
        if not brut:
            return None
        try:
            c = http.cookies.SimpleCookie(brut)
        except http.cookies.CookieError:
            return None
        m = c.get(auth.COOKIE)
        return SESSIONS.lire(m.value) if m else None

    def cookie_session(self, jeton, longue):
        parts = [
            "%s=%s" % (auth.COOKIE, jeton),
            "Path=/",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if longue:
            parts.append("Max-Age=%d" % auth.SESSION_TTL)
        return "; ".join(parts)

    def cookie_efface(self):
        return "%s=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" % auth.COOKIE

    def protection_active(self):
        """La protection ne s'applique qu'une fois un compte cree : une instance
        neuve doit rester accessible pour l'installation."""
        cfg = self.lire_config()
        return bool(cfg.get("users"))

    def exiger_session(self):
        """True si la requete peut continuer, sinon repond 401 et retourne False."""
        if not self.protection_active() or self.session_user():
            return True
        self.reply(401, json.dumps({"error": "authentification requise"}).encode())
        return False

    # ---------- routage ----------
    def do_GET(self):
        route = self.path.split("?")[0]
        if self.path.startswith("/px?"):
            if self.exiger_session():
                self.proxy()
        elif route in ("/cfg", "/api/config"):
            if self.exiger_session():
                self.cfg_get()
        elif route == "/api/health":
            self.reply(200, json.dumps({"ok": True, "app": "atrium"}).encode())
        elif route == "/api/session":
            self.session_get()
        else:
            super().do_GET()

    def do_POST(self):
        route = self.path.split("?")[0]
        if self.path.startswith("/px?"):
            if self.exiger_session():
                self.proxy()
        elif route in ("/cfg", "/api/config"):
            if self.exiger_session():
                self.cfg_post()
        elif route == "/api/login":
            self.login()
        elif route == "/api/logout":
            self.logout()
        elif route == "/api/password":
            self.changer_mdp()
        elif route == "/api/setup":
            self.installer()
        else:
            self.send_error(404)

    # ---------- authentification ----------
    def session_get(self):
        """Etat courant : l'interface sait quoi afficher avant tout le reste."""
        cfg = self.lire_config()
        users = cfg.get("users") or []
        self.reply(200, json.dumps({
            "installe": bool(users),
            "utilisateur": self.session_user(),
            "profils": [
                {"nom": u.get("nom"), "photo": u.get("photo", ""), "protege": bool(u.get("pwd"))}
                for u in users if u.get("nom")
            ],
        }, ensure_ascii=False).encode())

    def login(self):
        d = self.json_recu() or {}
        nom = str(d.get("nom", "")).strip()
        mdp = str(d.get("motdepasse", ""))
        longue = bool(d.get("memoriser"))
        cle = self.client_address[0]

        if not LIMITEUR.autorise(cle):
            self.reply(429, json.dumps({"error": "Trop de tentatives. Réessayez dans quelques minutes."}, ensure_ascii=False).encode())
            return

        cfg = self.lire_config()
        u = next((x for x in (cfg.get("users") or []) if x.get("nom") == nom), None)

        # meme reponse et meme cout, que le compte existe ou non
        ok = auth.verifier(mdp, u.get("pwd")) if u and u.get("pwd") else False
        if u and not u.get("pwd"):
            ok = True  # profil sans mot de passe : acces libre, comme prevu
        if not ok:
            LIMITEUR.echec(cle)
            self.reply(401, json.dumps({"error": "Nom d'utilisateur ou mot de passe incorrect."}, ensure_ascii=False).encode())
            return

        LIMITEUR.reussite(cle)
        jeton = SESSIONS.creer(nom, longue)
        self.reply(200, json.dumps({"ok": True, "utilisateur": nom}, ensure_ascii=False).encode(),
                   cookie=self.cookie_session(jeton, longue))

    def logout(self):
        brut = self.headers.get("Cookie")
        if brut:
            try:
                c = http.cookies.SimpleCookie(brut)
                m = c.get(auth.COOKIE)
                if m:
                    SESSIONS.supprimer(m.value)
            except http.cookies.CookieError:
                pass
        self.reply(200, b'{"ok":true}', cookie=self.cookie_efface())

    def changer_mdp(self):
        """Changement de mot de passe : l'ancien est exige."""
        courant = self.session_user()
        if not courant:
            self.reply(401, json.dumps({"error": "authentification requise"}).encode())
            return
        d = self.json_recu() or {}
        ancien = str(d.get("ancien", ""))
        nouveau = str(d.get("nouveau", ""))
        cible = str(d.get("cible", "")).strip() or courant

        cfg = self.lire_config()
        users = cfg.get("users") or []
        moi = next((x for x in users if x.get("nom") == courant), None)
        u = next((x for x in users if x.get("nom") == cible), None)
        if not u:
            self.reply(404, json.dumps({"error": "Profil introuvable."}, ensure_ascii=False).encode())
            return

        # l'ancien mot de passe est celui du demandeur ; il n'est pas exige
        # lorsqu'aucun mot de passe n'est encore defini sur son compte
        if moi and moi.get("pwd") and not auth.verifier(ancien, moi.get("pwd")):
            self.reply(403, json.dumps({"error": "Ancien mot de passe incorrect."}, ensure_ascii=False).encode())
            return
        if nouveau and len(nouveau) < 6:
            self.reply(400, json.dumps({"error": "Le mot de passe doit faire au moins 6 caractères."}, ensure_ascii=False).encode())
            return

        u["pwd"] = auth.hacher(nouveau) if nouveau else ""
        try:
            self.ecrire_config(cfg)
        except OSError as e:
            self.reply(500, json.dumps({"error": self.msg_ecriture(e)}, ensure_ascii=False).encode())
            return
        SESSIONS.supprimer_utilisateur(cible)  # les autres sessions tombent
        jeton = SESSIONS.creer(courant, False) if cible == courant else None
        self.reply(200, json.dumps({"ok": True}).encode(),
                   cookie=self.cookie_session(jeton, False) if jeton else None)

    def installer(self):
        """Creation du premier compte : refusee des qu'un compte existe.
        (Ne pas nommer cette methode « setup » : BaseRequestHandler.setup est
        appelee a chaque connexion.)"""
        cfg = self.lire_config()
        if cfg.get("users"):
            self.reply(409, json.dumps({"error": "Atrium est déjà configuré."}, ensure_ascii=False).encode())
            return
        d = self.json_recu() or {}
        nom = str(d.get("nom", "")).strip()
        mdp = str(d.get("motdepasse", ""))
        if not nom:
            self.reply(400, json.dumps({"error": "Nom d'utilisateur requis."}, ensure_ascii=False).encode())
            return
        if mdp and len(mdp) < 6:
            self.reply(400, json.dumps({"error": "Le mot de passe doit faire au moins 6 caractères."}, ensure_ascii=False).encode())
            return
        cfg["users"] = [{"nom": nom, "pwd": auth.hacher(mdp) if mdp else "", "photo": ""}]
        cfg.setdefault("apps", d.get("apps") or [])
        cfg["lockReq"] = bool(mdp)
        try:
            self.ecrire_config(cfg)
        except OSError as e:
            self.reply(500, json.dumps({"error": self.msg_ecriture(e)}, ensure_ascii=False).encode())
            return
        jeton = SESSIONS.creer(nom, True)
        self.reply(200, json.dumps({"ok": True, "utilisateur": nom}, ensure_ascii=False).encode(),
                   cookie=self.cookie_session(jeton, True))

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
        recu = self.json_recu()
        if recu is None or not isinstance(recu, dict):
            self.reply(400, json.dumps({"error": "json invalide"}).encode())
            return

        actuel = self.lire_config()
        users_actuels = actuel.get("users") or []

        # Garde-fou : un navigateur qui repart d'un etat vide ne doit pas
        # effacer les comptes existants. Supprimer un profil passe par une
        # liste qui en contient encore au moins un.
        if users_actuels and not (recu.get("users") or []):
            self.reply(409, json.dumps(
                {"error": "Refus : cette requête supprimerait tous les profils."},
                ensure_ascii=False).encode())
            return

        # Les empreintes de mots de passe restent l'affaire du serveur : le
        # navigateur ne peut ni les lire ni les remplacer via cette route.
        anciens = {u.get("nom"): u.get("pwd", "") for u in users_actuels}
        for u in (recu.get("users") or []):
            u["pwd"] = anciens.get(u.get("nom"), "")

        try:
            self.ecrire_config(recu)
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


def verifier_config_inscriptible():
    """Un volume non inscriptible est la panne la plus frequente : on la signale
    au demarrage plutot que de la decouvrir a la premiere sauvegarde."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        sonde = os.path.join(CONFIG_DIR, ".ecriture")
        with open(sonde, "w") as f:
            f.write("ok")
        os.remove(sonde)
        return True
    except OSError as e:
        print("ATTENTION : %s n'est pas inscriptible (%s)." % (CONFIG_DIR, e), flush=True)
        print("            Corrigez les droits du volume monte sur /config,", flush=True)
        print("            sinon aucune configuration ne pourra etre enregistree.", flush=True)
        return False


if __name__ == "__main__":
    print("Atrium — http://0.0.0.0:%d" % PORT, flush=True)
    print("Configuration : %s" % CONFIG_FILE, flush=True)
    verifier_config_inscriptible()
    Server(("0.0.0.0", PORT), Handler).serve_forever()
