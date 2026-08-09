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
import hashlib
import http.cookies
import http.server
import json
import os
import socketserver
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import auth
import conteneurs
import services
import supervision
import systeme
import widgets

VERSION = "1.0.0"
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
# Une variable definie mais vide (frequent avec les modeles de conteneurs, qui
# exportent les champs laisses blancs) doit valoir « non renseignee », sinon la
# liste d autorisation devient vide et le relais refuse toute destination.
_allow = (os.environ.get("ATRIUM_ALLOW_NET") or "").strip() or _DEFAULT_ALLOW
ALLOW_HOSTS = tuple(h.strip() for h in _allow.split(",") if h.strip())

FORWARD_HEADERS = ("x-api-key", "authorization", "content-type", "accept", "x-plex-token")


def host_allowed(url):
    """N'autorise le relais que vers le reseau local : le proxy ne doit pas
    devenir un rebond vers l'exterieur."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return False
    return any(host == h or host.startswith(h) for h in ALLOW_HOSTS)


def charger_config():
    # utf-8-sig : tolere un BOM, qu'ajoutent certains editeurs et outils
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---------- collecte de fond ----------
# Toutes les mesures sont prises par le serveur, a intervalle fixe. L'interface
# ne fait que lire le dernier releve : elle ne declenche aucun appel vers les
# services, et l'etat reste connu meme quand aucun navigateur n'est ouvert.
CYCLE = 30
ECHECS_AVANT_DOUTE = 3   # avant de mettre en cause une cle d'API

_releve = {"widgets": {}, "hote": {"disponible": False}, "maj": 0, "a": 0}

# Les conteneurs ne sont lus que lorsqu'on regarde la page Serveurs : mesurer
# la consommation de chacun coute un appel par conteneur, inutile en continu.
AGE_CONTENEURS = 15
_conteneurs = {"a": 0, "liste": []}
_verrou_conteneurs = threading.Lock()


def conteneurs_recents():
    with _verrou_conteneurs:
        if time.time() - _conteneurs["a"] > AGE_CONTENEURS:
            _conteneurs["liste"] = conteneurs.liste()
            _conteneurs["a"] = time.time()
        return _conteneurs["liste"]


_echecs_widget = {}
_deja_lu = set()   # services ayant deja livre leurs donnees au moins une fois


def _collecter():
    cfg = charger_config()
    apps = cfg.get("apps") or []
    supervision.sonder_apps(apps)
    hote = systeme.mesures(CONFIG_DIR)
    systeme.enregistrer(hote)
    etats = supervision.etats()

    tuiles, erreurs, maj = {}, {}, 0
    for a in apps:
        nom, url = a.get("nom"), a.get("url") or ""
        # Les fiches creees avant la detection automatique n'ont pas de type :
        # leur nom sert alors d'indice, faute de quoi elles resteraient muettes.
        type_service = a.get("type") or services.deviner_type(nom)
        if not nom or not url or not host_allowed(url):
            continue
        # Home Assistant range son jeton dans « token », les autres dans
        # « apiKey » ; les fiches anciennes melangent les deux.
        cle = (a.get("token") or a.get("apiKey")) if type_service == "ha" \
            else (a.get("apiKey") or a.get("token"))
        if type_service == "ha" and cle:
            n = widgets.maj_ha(url, cle)
            if n:
                maj += n
        if type_service not in widgets.REGISTRE:
            continue
        stats = widgets.mesurer(type_service, url, cle or "")
        if stats:
            tuiles[nom] = stats
            _deja_lu.add(nom)
            _echecs_widget.pop(nom, None)
        elif nom in _deja_lu:
            _echecs_widget[nom] = _echecs_widget.get(nom, 0) + 1
            if _echecs_widget[nom] < ECHECS_AVANT_DOUTE:
                # un creux passager ne doit pas vider la tuile sous les yeux de
                # l'utilisateur : on garde le dernier chiffre connu
                anciens = _releve["widgets"].get(nom)
                if anciens:
                    tuiles[nom] = anciens
            elif cle and (etats.get(nom) or {}).get("en_ligne"):
                # On ne met en cause une cle que si ce service a deja livre ses
                # donnees : sans cette preuve, un service qui n'en expose tout
                # simplement pas serait signale a tort.
                erreurs[nom] = "Service joignable, données refusées : vérifiez la clé d'API"

    _releve.update(widgets=tuiles, hote=hote, maj=maj, a=time.time())
    supervision.evaluer(apps, hote, maj, erreurs, tuiles)


def boucle_collecte():
    while True:
        try:
            _collecter()
        except Exception as e:                       # une sonde ne doit jamais
            if os.environ.get("ATRIUM_DEBUG"):       # interrompre la boucle
                sys.stderr.write("collecte : %s\n" % e)
        time.sleep(CYCLE)


def demarrer_collecte():
    threading.Thread(target=boucle_collecte, daemon=True).start()


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

    def reply(self, code, data, ctype="application/json; charset=utf-8", cookie=None):
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
        return charger_config()

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

    def jeton_courant(self):
        brut = self.headers.get("Cookie")
        if not brut:
            return None
        try:
            c = http.cookies.SimpleCookie(brut)
        except http.cookies.CookieError:
            return None
        m = c.get(auth.COOKIE)
        return m.value if m else None

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
        elif route == "/api/system":
            if self.exiger_session():
                self.reply(200, json.dumps(systeme.mesures(CONFIG_DIR), ensure_ascii=False).encode())
        elif route == "/api/widgets":
            if self.exiger_session():
                self.reply(200, json.dumps(_releve["widgets"], ensure_ascii=False).encode())
        elif route == "/api/supervision":
            if self.exiger_session():
                self.supervision_get()
        elif route == "/api/sessions":
            if self.exiger_session():
                self.sessions_get()
        elif route == "/api/diagnostic":
            if self.exiger_session():
                self.diagnostic_get()
        elif route == "/api/conteneurs":
            if self.exiger_session():
                self.reply(200, json.dumps({"docker": conteneurs.disponible(),
                                            "liste": conteneurs.noms()},
                                           ensure_ascii=False).encode())
        elif route == "/api/serveur":
            if self.exiger_session():
                self.reply(200, json.dumps({
                    "hote": _releve["hote"],
                    "historique": systeme.historique(),
                    "docker": conteneurs.disponible(),
                    "conteneurs": conteneurs_recents(),
                    "releve": _releve["a"],
                }, ensure_ascii=False).encode())
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
        elif route == "/api/detect":
            if self.exiger_session():
                self.detecter()
        elif route == "/api/login":
            self.login()
        elif route == "/api/logout":
            self.logout()
        elif route == "/api/password":
            self.changer_mdp()
        elif route == "/api/setup":
            self.installer()
        elif route == "/api/alertes/lues":
            if self.exiger_session():
                supervision.marquer_lues()
                self.reply(200, b'{"ok":true}')
        elif route == "/api/sessions/revoquer":
            if self.exiger_session():
                self.sessions_revoquer()
        elif route == "/api/sonder":
            if self.exiger_session():
                self.sonder_maintenant()
        elif route == "/api/conteneur/redemarrer":
            if self.exiger_session():
                self.redemarrer_conteneur()
        else:
            self.send_error(404)

    def detecter(self):
        """Reconnait le service derriere une URL, et verifie la cle si fournie."""
        d = self.json_recu() or {}
        url = str(d.get("url", "")).strip()
        cle = str(d.get("cle", ""))
        if not url:
            self.reply(400, json.dumps({"error": "URL requise"}, ensure_ascii=False).encode())
            return
        if not host_allowed(url if url.startswith(("http://", "https://")) else "http://" + url):
            self.reply(403, json.dumps({"error": "Adresse hors du réseau privé"}, ensure_ascii=False).encode())
            return
        self.reply(200, json.dumps(services.identifier(url, cle), ensure_ascii=False).encode())

    def _app_nommee(self, nom):
        return next((a for a in (charger_config().get("apps") or [])
                     if a.get("nom") == nom), None)

    def sonder_maintenant(self):
        """Resonde une application a la demande, sans attendre le cycle."""
        nom = str((self.json_recu() or {}).get("nom", "")).strip()
        app = self._app_nommee(nom)
        if not app:
            self.reply(404, json.dumps({"error": "Application inconnue."}, ensure_ascii=False).encode())
            return
        etat = supervision.sonder_un(app)
        tuiles = dict(_releve["widgets"])
        type_service = app.get("type") or services.deviner_type(nom)
        url = app.get("url") or ""
        if type_service in widgets.REGISTRE and url and host_allowed(url):
            cle = (app.get("token") or app.get("apiKey")) if type_service == "ha" \
                else (app.get("apiKey") or app.get("token"))
            stats = widgets.mesurer(type_service, url, cle or "")
            if stats:
                tuiles[nom] = stats
                _deja_lu.add(nom)
                _echecs_widget.pop(nom, None)
                _releve["widgets"] = tuiles
        self.reply(200, json.dumps({"etat": etat, "stats": tuiles.get(nom)},
                                   ensure_ascii=False).encode())

    def redemarrer_conteneur(self):
        """Redemarrage explicite d'un conteneur, demande depuis une tuile."""
        nom = str((self.json_recu() or {}).get("nom", "")).strip()
        if not nom:
            self.reply(400, json.dumps({"error": "Nom requis."}, ensure_ascii=False).encode())
            return
        ok, msg = conteneurs.redemarrer(nom)
        _conteneurs["a"] = 0          # le tableau doit repartir d'une lecture fraiche
        code = 200 if ok else (503 if "socket" in msg else 502)
        self.reply(code, json.dumps({"ok": ok, "error": msg}, ensure_ascii=False).encode())

    def sessions_get(self):
        """Sessions ouvertes du compte courant. Le jeton n'est jamais renvoye :
        seule une empreinte courte, qui suffit a distinguer les lignes."""
        courant = self.session_user()
        actuel = self.jeton_courant()
        sortie = []
        for s in SESSIONS.lister(courant):
            sortie.append({
                "id": hashlib.sha256(s["jeton"].encode()).hexdigest()[:12],
                "actuelle": s["jeton"] == actuel,
                "agent": s.get("agent", ""),
                "ip": s.get("ip", ""),
                "cree": s.get("cree", 0),
                "vue": s.get("vue", 0),
                "exp": s.get("exp", 0),
            })
        self.reply(200, json.dumps({"utilisateur": courant, "sessions": sortie},
                                   ensure_ascii=False).encode())

    def sessions_revoquer(self):
        n = SESSIONS.revoquer_autres(self.jeton_courant(), self.session_user())
        self.reply(200, json.dumps({"ok": True, "fermees": n}).encode())

    def diagnostic_get(self):
        """Verifications de bon fonctionnement, telles qu'Atrium les constate."""
        cfg = charger_config()
        inscriptible = False
        try:
            sonde = os.path.join(CONFIG_DIR, ".ecriture")
            with open(sonde, "w") as f:
                f.write("ok")
            os.remove(sonde)
            inscriptible = True
        except OSError:
            pass
        apps = cfg.get("apps") or []
        etats = supervision.etats()
        joignables = sum(1 for a in apps if (etats.get(a.get("nom")) or {}).get("en_ligne"))
        avec_url = sum(1 for a in apps if (a.get("url") or "").strip())
        self.reply(200, json.dumps({
            "version": VERSION,
            "config": {"ok": inscriptible, "chemin": CONFIG_FILE},
            "mesures": {"ok": _releve["hote"].get("disponible", False)},
            "docker": {"ok": conteneurs.disponible()},
            "services": {"ok": avec_url == 0 or joignables > 0,
                         "joignables": joignables, "total": avec_url},
            "releve": _releve["a"],
            "cycle": CYCLE,
        }, ensure_ascii=False).encode())

    def supervision_get(self):
        """Dernier releve complet : etat et temps de reponse de chaque service,
        mesures de l'hote, tuiles, alertes. Les identifiants restent au serveur,
        le navigateur ne recoit que des chiffres."""
        self.reply(200, json.dumps({
            "etats": supervision.etats(),
            "alertes": supervision.resume(),
            "hote": _releve["hote"],
            "widgets": _releve["widgets"],
            "maj": _releve["maj"],
            "releve": _releve["a"],
            "cycle": CYCLE,
        }, ensure_ascii=False).encode())

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
        jeton = SESSIONS.creer(nom, longue, self.headers.get("User-Agent", ""),
                               self.client_address[0])
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
        jeton = SESSIONS.creer(courant, False, self.headers.get("User-Agent", ""),
                               self.client_address[0]) if cible == courant else None
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
        jeton = SESSIONS.creer(nom, True, self.headers.get("User-Agent", ""),
                               self.client_address[0])
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
        """La configuration, empreintes de mots de passe retirees.

        L'interface a seulement besoin de savoir si un profil est protege, pas
        de son empreinte. Les envoyer permettrait a n'importe quelle session
        d'emporter celle des autres comptes et de l'attaquer hors ligne."""
        cfg = charger_config()
        for u in (cfg.get("users") or []):
            u["pwd"] = "1" if u.get("pwd") else ""
        self.reply(200, json.dumps(cfg, ensure_ascii=False).encode())

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
    systeme.demarrer_echantillonnage()
    demarrer_collecte()
    Server(("0.0.0.0", PORT), Handler).serve_forever()
