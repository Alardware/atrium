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
  ATRIUM_ADMIN       compte de secours cache, « nom:motdepasse ». Cree ou mis a
                     jour au demarrage, absent de l ecran de connexion et de
                     toutes les listes de profils. La variable peut etre retiree
                     ensuite : le compte, lui, reste.
"""
import hashlib
import http.cookies
import io as _io
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
import zipfile

import auth
import conteneurs
import historique
import metriques
import reseau
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

DEMARRE = time.time()
SESSIONS = auth.Sessions(SESSION_FILE)
LIMITEUR = auth.Limiteur()
JOURNAL = auth.Journal(os.path.join(CONFIG_DIR, "journal.json"))
HISTO = historique.Historique(os.path.join(CONFIG_DIR, "historique.json"))
MESURES = historique.Mesures(os.path.join(CONFIG_DIR, "mesures.json"))
JOURNAL_SRV = historique.Evenements(os.path.join(CONFIG_DIR, "evenements.json"))
# Dernier etat connu de chaque service, pour ne noter que les bascules.
_vu = {}

FORWARD_HEADERS = ("x-api-key", "authorization", "content-type", "accept", "x-plex-token")
CORPS_MAX = 8 * 1024 * 1024      # au-dela, la requete est refusee


def host_allowed(url):
    """N'autorise le relais que vers le reseau local. La decision porte sur
    l'adresse resolue : voir reseau.py."""
    return reseau.autorise(url)


def memoire_atrium():
    """Memoire residente du processus, en octets, ou None hors Linux.

    C est la consommation d Atrium lui-meme, pas celle de la machine : la page
    « A propos » decrit l application, la page Serveurs decrit l hote.
    """
    try:
        with open("/proc/self/status", "r") as f:
            for ligne in f:
                if ligne.startswith("VmRSS:"):
                    return int(ligne.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def charger_config():
    # utf-8-sig : tolere un BOM, qu'ajoutent certains editeurs et outils
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def profil_cache(u):
    """Ce profil est-il un compte de secours ?"""
    return bool((u or {}).get("cache"))


def visibles(users):
    """Les profils que l interface a le droit de connaitre."""
    return [u for u in (users or []) if not profil_cache(u)]


MDP_MINIMUM = 8


def installer_admin_cache():
    """Cree ou met a jour le compte de secours decrit par ATRIUM_ADMIN.

    Un compte cache n a d interet que s il reste protege : le dissimuler ne
    prouve rien, c est le mot de passe qui garde. Un secret trop court est donc
    refuse plutot que dilue dans le fichier.

    Le mot de passe n est ni journalise, ni renvoye : seule son empreinte est
    ecrite, comme pour les autres profils.
    """
    brut = (os.environ.get("ATRIUM_ADMIN") or "").strip()
    if not brut:
        return
    nom, _, mdp = brut.partition(":")
    nom, mdp = nom.strip(), mdp
    if not nom or not mdp:
        print("ATRIUM_ADMIN ignore : format attendu « nom:motdepasse »", flush=True)
        return
    if len(mdp) < MDP_MINIMUM:
        print("ATRIUM_ADMIN ignore : mot de passe de moins de %d caracteres"
              % MDP_MINIMUM, flush=True)
        return
    cfg = charger_config()
    users = cfg.get("users") or []
    u = next((x for x in users if x.get("nom") == nom), None)
    if u is not None and not profil_cache(u):
        # Reprendre le nom d un profil existant le ferait disparaitre de l ecran
        # de connexion et remplacerait son mot de passe : ce serait perdre un
        # compte en croyant en ajouter un.
        print("ATRIUM_ADMIN ignore : « %s » est deja un profil visible. "
              "Choisissez un autre nom." % nom, flush=True)
        return
    if u is None:
        # « av:admin » : la figure fournie pour ce compte, celle qui porte le
        # chevron. Elle se distingue au premier coup d oeil des profils
        # ordinaires, qui gardent leur initiale.
        users.append({"nom": nom, "pwd": auth.hacher(mdp), "photo": "av:admin",
                      "cache": True})
        action = "cree"
    else:
        u["pwd"] = auth.hacher(mdp)
        u["cache"] = True
        u.setdefault("photo", "")
        if not u["photo"]:
            u["photo"] = "av:admin"
        action = "mis a jour"
    cfg["users"] = users
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass
        print("Compte de secours « %s » %s (invisible a l ecran de connexion)"
              % (nom, action), flush=True)
    except OSError as e:
        print("Compte de secours : ecriture impossible (%s)" % e, flush=True)


# ---------- collecte de fond ----------
# Toutes les mesures sont prises par le serveur, a intervalle fixe. L'interface
# ne fait que lire le dernier releve : elle ne declenche aucun appel vers les
# services, et l'etat reste connu meme quand aucun navigateur n'est ouvert.
CYCLE = 30
ECHECS_AVANT_DOUTE = 3   # avant de mettre en cause une cle d'API

_releve = {"widgets": {}, "hote": {"disponible": False}, "maj": 0, "a": 0}

# Les conteneurs ne sont lus que lorsqu'on regarde la page Serveurs : mesurer
# la consommation de chacun coute un appel au demon, et celui-ci met une
# seconde a repondre. Vingt conteneurs, meme interroges par paquets de huit,
# font donc trois secondes — bien trop pour faire patienter une page.
AGE_CONTENEURS = 20
_conteneurs = {"a": 0, "liste": [], "encours": False}
_verrou_conteneurs = threading.Lock()


def _rafraichir_conteneurs():
    try:
        liste = conteneurs.liste()
        with _verrou_conteneurs:
            _conteneurs["liste"] = liste
            _conteneurs["a"] = time.time()
    finally:
        with _verrou_conteneurs:
            _conteneurs["encours"] = False


def conteneurs_recents():
    """Dernier releve connu, rendu immediatement.

    Si la mesure a vieilli, on en relance une en fond et on repond quand meme :
    la page affiche aussitot ce qu'on sait, et recoit la mise a jour au
    rafraichissement suivant. Elle n'attend jamais le demon.
    """
    with _verrou_conteneurs:
        vieux = time.time() - _conteneurs["a"] > AGE_CONTENEURS
        if vieux and not _conteneurs["encours"] and conteneurs.disponible():
            _conteneurs["encours"] = True
            threading.Thread(target=_rafraichir_conteneurs, daemon=True).start()
        return list(_conteneurs["liste"]), _conteneurs["a"]


_echecs_widget = {}
_deja_lu = set()   # services ayant deja livre leurs donnees au moins une fois


def _collecter():
    cfg = charger_config()
    apps = cfg.get("apps") or []
    etats_sondes = supervision.sonder_apps(apps)
    # Seuls les services reellement surveilles entrent dans l historique : une
    # fiche sans adresse n est pas « hors ligne », elle n est pas sondee.
    noms = {a.get("nom") for a in apps if a.get("nom") and (a.get("url") or "").strip()}
    for nom in noms:
        e = etats_sondes.get(nom)
        if e:
            HISTO.noter(nom, e.get("en_ligne"), e.get("latence_ms"))
    HISTO.oublier(noms)
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
        diag = {}
        stats = widgets.mesurer(type_service, url, cle or "", diag)
        if not stats and diag.get("refus"):
            # Le service a repondu et a refuse : on nomme ce qu il a refuse,
            # plutot que de laisser la tuile dire « indisponible ».
            erreurs[nom] = "Données refusées — " + " · ".join(diag["refus"][:3])
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

    # Chaque mesure numerique rejoint sa serie. Celles a texte libre — un
    # uptime, un « 23 / 24 » — n en ont pas : leur nombre ne veut rien dire hors
    # de leur phrase, et metriques.historisable le dit.
    releves = {}
    for nom, stats in tuiles.items():
        gardees = set()
        for s in stats:
            ident = s.get("id")
            if ident and metriques.historisable(ident) and s.get("num") is not None:
                MESURES.noter(nom, ident, s["num"])
                gardees.add(ident)
        releves[nom] = gardees
    # « noms » : les services configures. « releves » : ceux qui ont repondu a ce
    # cycle. Un service muet garde son historique — son silence ne dit rien de
    # ses mesures passees.
    MESURES.oublier(noms, releves)

    _releve.update(widgets=tuiles, hote=hote, maj=maj, a=time.time())
    supervision.evaluer(apps, hote, maj, erreurs, tuiles)
    _noter_evenements(noms, etats_sondes)


def _noter_evenements(noms, etats_sondes):
    """Consigne ce qui a change, et seulement ce qui a change.

    Un journal qui reciterait chaque sonde toutes les trente secondes serait
    illisible : mille lignes par nuit disant que tout va bien. On n y garde que
    les bascules d etat, les seuils franchis et rentres dans l ordre, et la
    premiere apparition d un service.
    """
    graves = {}
    for a in supervision.alertes():
        if a.get("code") == "seuil" and a.get("service"):
            graves.setdefault(a["service"], {})[a["param"].get("metrique")] = a["niveau"]

    for nom in noms:
        e = etats_sondes.get(nom)
        if not e:
            continue
        avant = _vu.get(nom)
        if avant is None:
            JOURNAL_SRV.noter(nom, "suivi")
        elif avant.get("en_ligne") != e.get("en_ligne"):
            JOURNAL_SRV.noter(nom, "en_ligne" if e.get("en_ligne") else "hors_ligne",
                              {"echecs": e.get("echecs", 0)})
        # Seuils : franchi, aggrave, ou rentre dans l ordre.
        anciens = (avant or {}).get("seuils") or {}
        courants = graves.get(nom, {})
        for mes, niveau in courants.items():
            if anciens.get(mes) != niveau:
                JOURNAL_SRV.noter(nom, "seuil", {"metrique": mes, "niveau": niveau})
        for mes in anciens:
            if mes not in courants:
                JOURNAL_SRV.noter(nom, "seuil_fin", {"metrique": mes})
        _vu[nom] = {"en_ligne": e.get("en_ligne"), "seuils": courants}

    for perdu in [n for n in _vu if n not in noms]:
        del _vu[perdu]
    JOURNAL_SRV.oublier(noms)


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


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    """Un service local autorise pourrait renvoyer une redirection vers
    l'exterieur : le relais l'y suivrait et redeviendrait un rebond. On les
    refuse toutes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_ctx_relais = ssl.create_default_context()
_ctx_relais.check_hostname = False
_ctx_relais.verify_mode = ssl.CERT_NONE
OUVREUR = urllib.request.build_opener(
    _SansRedirection, urllib.request.HTTPSHandler(context=_ctx_relais))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC, **kwargs)

    def log_message(self, fmt, *args):
        if os.environ.get("ATRIUM_DEBUG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def end_headers(self):
        """Entetes de securite communs a toutes les reponses, pages comme API.

        Aucun en-tete CORS : l'interface et l'API partagent la meme origine, si
        bien qu'aucune page tierce n'a besoin de lire ces reponses — et le
        navigateur le lui refuse, ce qui ferme la lecture de /api/session depuis
        n'importe quel site visite.
        """
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        # L'interface ne doit jamais rester en cache : sinon une mise a jour du
        # conteneur laisse tourner l'ancien code dans le navigateur.
        page = (self.path.split("?")[0].rstrip("/") in ("", "/index.html")
                or self.path.endswith(".html"))
        if page:
            self.send_header("Cache-Control", "no-store, must-revalidate")
            # « connect-src * » est necessaire : l'interface appelle les services
            # du reseau en direct. Le reste ferme le chargement de code externe.
            # Les deux origines de Google Fonts sont nommees explicitement :
            # l'interface y charge sa typographie. Tout le reste est ferme.
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                             "font-src 'self' https://fonts.gstatic.com; "
                             "img-src 'self' data: http: https:; connect-src *; "
                             "frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
        super().end_headers()

    # ---------- utilitaires ----------
    def reply(self, code, data, ctype="application/json; charset=utf-8", cookie=None,
              entetes=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        for k, v in (entetes or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json_recu(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length or length > CORPS_MAX:
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

    def https(self):
        """Vrai si le navigateur a parle en HTTPS. Atrium sert du HTTP en clair ;
        c'est le proxy en amont qui chiffre et l'annonce par cet en-tete. On ne
        s'en sert que pour ajouter une protection, jamais pour en retirer."""
        return (self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
                == "https")

    def cookie_session(self, jeton, longue):
        parts = [
            "%s=%s" % (auth.COOKIE, jeton),
            "Path=/",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if self.https():
            parts.append("Secure")
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
        elif route == "/api/securite":
            if self.exiger_session():
                self.securite_get()
        elif route == "/api/historique":
            if self.exiger_session():
                self.historique_get()
        elif route == "/api/diagnostic":
            if self.exiger_session():
                self.diagnostic_get()
        elif route == "/api/mesure":
            if self.exiger_session():
                self.mesure_get()
        elif route == "/api/journal":
            if self.exiger_session():
                self.journal_get()
        elif route == "/api/sauvegarde":
            if self.exiger_session():
                self.sauvegarde_get()
        elif route == "/api/capacites":
            # Ne change jamais en cours d'execution : l'interface la demande une
            # fois et s'en sert pour expliquer une tuile sans chiffre.
            if self.exiger_session():
                self.reply(200, json.dumps(widgets.profils(), ensure_ascii=False).encode())
        elif route == "/api/conteneurs":
            if self.exiger_session():
                self.reply(200, json.dumps({"docker": conteneurs.disponible(),
                                            "liste": conteneurs.noms()},
                                           ensure_ascii=False).encode())
        elif route == "/api/serveur":
            if self.exiger_session():
                liste, mesure_a = conteneurs_recents()
                self.reply(200, json.dumps({
                    "hote": _releve["hote"],
                    "historique": systeme.historique(),
                    "docker": conteneurs.disponible(),
                    "conteneurs": liste,
                    # 0 tant qu'aucune mesure n'a abouti : l'interface le dit
                    # au lieu d'afficher un tableau vide qui semblerait faux
                    "conteneurs_a": mesure_a,
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
        elif route == "/api/restauration":
            if self.exiger_session():
                self.restauration_post()
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
        vu = services.identifier(url, cle)
        # ce que cette integration saura lire : l'interface l'annonce sur la
        # fiche, avant meme qu'une mesure ait abouti
        vu["donnees"] = widgets.capacites(vu.get("type") or "")
        self.reply(200, json.dumps(vu, ensure_ascii=False).encode())

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

    def historique_get(self):
        """Disponibilite horaire des services surveilles.

        Le nombre d heures demande est borne : la retention est de trente
        jours, reclamer davantage ne rendrait que des seaux vides.
        """
        try:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            heures = int(q.get("h", ["24"])[0])
        except (ValueError, TypeError):
            heures = 24
        heures = max(1, min(heures, historique.RETENTION))
        cfg = self.lire_config()
        noms = [a.get("nom") for a in (cfg.get("apps") or [])
                if a.get("nom") and (a.get("url") or "").strip()]
        self.reply(200, json.dumps({
            "heures": heures,
            "services": {n: HISTO.resume(n, heures) for n in noms},
        }, ensure_ascii=False).encode())

    def mesure_get(self):
        """Serie d une mesure d un service, sur la plage demandee.

        Le service doit exister dans la configuration : sans cette verification,
        la route repondrait sur n importe quel nom present dans le fichier,
        y compris ceux qu une suppression aurait du faire disparaitre.
        """
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        nom = (q.get("service") or [""])[0]
        ident = (q.get("id") or [""])[0]
        try:
            heures = int((q.get("h") or ["24"])[0])
        except (ValueError, TypeError):
            heures = 24
        heures = max(1, min(heures, historique.RETENTION))
        if not self._app_nommee(nom):
            self.reply(404, json.dumps({"error": "service inconnu"},
                                       ensure_ascii=False).encode())
            return
        r = MESURES.resume(nom, ident, heures)
        r["heures_demandees"] = heures
        r["nature"] = metriques.nature(ident)
        r["agregats"] = list(metriques.agregats(ident))
        r["lab"] = metriques.libelle(ident)
        self.reply(200, json.dumps(r, ensure_ascii=False).encode())

    def journal_get(self):
        """Ce qu Atrium a constate, du plus recent au plus ancien.

        Sans « service », le journal est commun a toutes les applications : c est
        la meme matiere, regroupee, et chaque entree dit alors d ou elle vient.
        """
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        nom = (q.get("service") or [""])[0]
        if not nom:
            try:
                combien = int((q.get("n") or ["200"])[0])
            except (ValueError, TypeError):
                combien = 200
            self.reply(200, json.dumps(
                {"evenements": JOURNAL_SRV.tout(max(1, min(combien, 500)))},
                ensure_ascii=False).encode())
            return
        if not self._app_nommee(nom):
            self.reply(404, json.dumps({"error": "service inconnu"},
                                       ensure_ascii=False).encode())
            return
        self.reply(200, json.dumps({"evenements": JOURNAL_SRV.lister(nom, 20)},
                                   ensure_ascii=False).encode())

    # Ce qui part dans une archive, et ce qui n en part jamais. Les jetons de
    # session ouvriraient l acces a qui met la main sur le fichier ; le journal
    # des connexions refusees contient des adresses IP, qui ne regardent
    # personne d autre. Ni l un ni l autre ne se restaure utilement.
    SAUVEGARDE = ("atrium.json", "historique.json", "mesures.json", "evenements.json")
    JAMAIS = ("sessions.json", "journal.json")
    RESTAURE_MAX = 64 * 1024 * 1024

    def sauvegarde_get(self):
        """Archive la configuration et les series, telles quelles.

        Le fichier contient les cles d API des services et les empreintes des
        mots de passe : il vaut la configuration elle-meme, et se range comme
        tel. L interface le dit avant le telechargement.
        """
        tampon = _io.BytesIO()
        dedans = []
        with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
            for nom in self.SAUVEGARDE:
                chemin = os.path.join(CONFIG_DIR, nom)
                if not os.path.exists(chemin):
                    continue
                if nom == "atrium.json":
                    # Les comptes de secours ne partent pas dans une archive que
                    # n importe quelle session peut telecharger : leur nom et
                    # leur empreinte y seraient lisibles. Ils appartiennent a
                    # l installation (ATRIUM_ADMIN), pas a la sauvegarde.
                    try:
                        with open(chemin, "r", encoding="utf-8-sig") as f:
                            cfg = json.load(f)
                        cfg["users"] = visibles(cfg.get("users"))
                        z.writestr(nom, json.dumps(cfg, ensure_ascii=False))
                        dedans.append(nom)
                        continue
                    except (OSError, ValueError):
                        pass          # illisible : on archive le fichier tel quel
                z.write(chemin, nom)
                dedans.append(nom)
            z.writestr("LISEZ-MOI.txt", (
                "Sauvegarde Atrium du %s\r\n\r\n"
                "Contenu : %s\r\n\r\n"
                "atrium.json contient les cles d API de vos services et les\r\n"
                "empreintes des mots de passe de vos profils. Rangez ce fichier\r\n"
                "comme vous rangeriez ces cles.\r\n\r\n"
                "Les jetons de session et le journal des connexions refusees\r\n"
                "n y figurent pas : ils ne se restaurent pas et n ont rien a\r\n"
                "faire dans une archive. Un eventuel compte de secours non plus :\r\n"
                "il se redefinit sur la nouvelle installation par ATRIUM_ADMIN.\r\n"
            ) % (time.strftime("%Y-%m-%d %H:%M"), ", ".join(dedans) or "rien"))
        corps = tampon.getvalue()
        self.reply(200, corps, "application/zip", entetes={
            "Content-Disposition": 'attachment; filename="atrium-%s.zip"'
                                   % time.strftime("%Y%m%d-%H%M"),
        })

    def restauration_post(self):
        """Remplace la configuration par celle d une archive.

        Rien n est ecrit avant que tout ait ete verifie : une archive tronquee
        ou dont la configuration ne se lit pas laisse l installation intacte.
        L ancienne configuration est conservee a cote, sous « .avant ».
        """
        try:
            taille = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            taille = 0
        if taille <= 0 or taille > self.RESTAURE_MAX:
            self.reply(413, json.dumps({"error": "Archive absente ou trop volumineuse."},
                                       ensure_ascii=False).encode())
            return
        brut = self.rfile.read(taille)
        try:
            with zipfile.ZipFile(_io.BytesIO(brut)) as z:
                noms = [n for n in z.namelist() if n in self.SAUVEGARDE]
                if "atrium.json" not in noms:
                    raise ValueError("archive sans configuration")
                contenus = {}
                for n in noms:
                    if z.getinfo(n).file_size > self.RESTAURE_MAX:
                        raise ValueError("fichier trop volumineux")
                    contenus[n] = z.read(n)
                cfg = json.loads(contenus["atrium.json"].decode("utf-8"))
                if not isinstance(cfg, dict) or not isinstance(cfg.get("users"), list):
                    raise ValueError("configuration illisible")
                if not cfg["users"]:
                    raise ValueError("configuration sans profil")
                # Une archive ne pose pas de compte cache : ce serait le moyen
                # le plus simple d installer un acces invisible sur une machine
                # ou l on vient d obtenir une session ordinaire.
                for u in cfg["users"]:
                    if isinstance(u, dict):
                        u.pop("cache", None)
                # Ceux de l installation en cours, eux, survivent : ils ne
                # figurent dans aucune archive et seraient sinon effaces.
                gardes = [u for u in (self.lire_config().get("users") or [])
                          if profil_cache(u)]
                noms_gardes = {u.get("nom") for u in gardes}
                cfg["users"] = [u for u in cfg["users"]
                                if not (isinstance(u, dict) and u.get("nom") in noms_gardes)]
                cfg["users"] += gardes
                contenus["atrium.json"] = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
        except (zipfile.BadZipFile, ValueError, KeyError, UnicodeDecodeError) as e:
            self.reply(400, json.dumps({"error": "Archive invalide : %s" % e},
                                       ensure_ascii=False).encode())
            return

        try:
            ancien = os.path.join(CONFIG_DIR, "atrium.json")
            if os.path.exists(ancien):
                with open(ancien, "rb") as f:
                    garde = f.read()
                with open(ancien + ".avant", "wb") as f:
                    f.write(garde)
            for nom, donnees in contenus.items():
                tmp = os.path.join(CONFIG_DIR, nom + ".tmp")
                with open(tmp, "wb") as f:
                    f.write(donnees)
                os.replace(tmp, os.path.join(CONFIG_DIR, nom))
            try:
                os.chmod(os.path.join(CONFIG_DIR, "atrium.json"), 0o600)
            except OSError:
                pass
        except OSError as e:
            self.reply(500, json.dumps({"error": self.msg_ecriture(e)},
                                       ensure_ascii=False).encode())
            return
        # Les series relisent leur fichier : sans cela, la memoire du processus
        # ecraserait la restauration a la premiere ecriture.
        global HISTO, MESURES, JOURNAL_SRV
        HISTO = historique.Historique(os.path.join(CONFIG_DIR, "historique.json"))
        MESURES = historique.Mesures(os.path.join(CONFIG_DIR, "mesures.json"))
        JOURNAL_SRV = historique.Evenements(os.path.join(CONFIG_DIR, "evenements.json"))
        _vu.clear()
        self.reply(200, json.dumps({"ok": True, "fichiers": sorted(contenus)},
                                   ensure_ascii=False).encode())

    def securite_get(self):
        """Etat de securite reel, tel que le serveur peut le constater.

        Chaque point est verifie ici et non deduit cote navigateur : une page
        qui s auto-declarerait sure ne prouverait rien. Ce qui n est pas
        verifiable est rapporte comme inconnu, jamais comme bon.
        """
        cfg = self.lire_config()
        # Le compte de secours n est pas compte parmi les profils : le nombre
        # affiche revelerait son existence.
        users = visibles(cfg.get("users"))
        moi = self.session_user()
        u = next((x for x in (cfg.get("users") or []) if x.get("nom") == moi), None)

        ip = self.client_address[0]
        relaye = bool(self.headers.get("X-Forwarded-For"))
        # Un portail d authentification place devant Atrium annonce l utilisateur
        # qu il a valide. Ces en-tetes sont la convention d Authelia, d Authentik
        # et du proxy de Home Assistant.
        portail = next((h for h in ("Remote-User", "X-Forwarded-User",
                                    "X-Authentik-Username", "X-Remote-User")
                        if self.headers.get(h)), None)

        echecs = JOURNAL.echecs()
        points = [
            {"cle": "mdp", "ok": bool(u and u.get("pwd"))},
            {"cle": "auth", "ok": all(x.get("pwd") for x in users) if users else False},
            {"cle": "limite", "ok": True},
            {"cle": "relais", "ok": True},
            {"cle": "journal", "ok": not echecs},
            {"cle": "local", "ok": (not relaye) and reseau.prive(ip)},
            {"cle": "portail", "ok": bool(portail)},
            {"cle": "cookie", "ok": True},
            {"cle": "entetes", "ok": True},
        ]
        self.reply(200, json.dumps({
            "points": points,
            "score": sum(1 for p in points if p["ok"]),
            "total": len(points),
            "mdp": bool(u and u.get("pwd")),
            "profils": len(users),
            "sans_mdp": [x.get("nom") for x in users if not x.get("pwd")],
            "limiteur": {"essais": LIMITEUR.essais, "fenetre": LIMITEUR.fenetre},
            "reseaux": list(reseau.NOMS_AUTORISES) or None,
            "acces": {"ip": ip, "local": (not relaye) and reseau.prive(ip),
                      "relaye": relaye, "portail": portail or ""},
            "echecs": echecs[-20:],
            "iterations": auth.ITERATIONS,
        }, ensure_ascii=False).encode())

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
            # Description de l installation : tout est constate ici, rien n est
            # demande a l exterieur. Une version « a jour » supposerait
            # d interroger Internet, ce que le relais s interdit — la page
            # renvoie donc vers les publications plutot que de l affirmer.
            "installe": {
                "demarre": DEMARRE,
                "memoire": memoire_atrium(),
                "port": PORT,
                "config_dir": CONFIG_DIR,
                "fuseau": time.strftime("%Z"),
                "fuseau_nom": os.environ.get("TZ", ""),
                "reseaux": list(reseau.NOMS_AUTORISES),
                "image": os.environ.get("ATRIUM_IMAGE", ""),
                "conteneur": os.path.exists("/.dockerenv"),
                "python": "%d.%d.%d" % sys.version_info[:3],
                "apps": len(apps),
                "integrations": sum(1 for a in apps
                                    if (a.get("type") or services.deviner_type(a.get("nom") or ""))
                                    in widgets.REGISTRE),
            },
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
        # D ou vient cette connexion : la seule information que le navigateur ne
        # peut pas etablir lui-meme, et la plus utile a savoir avant de saisir un
        # mot de passe. Il s agit de sa propre adresse, rien n en fuit.
        #
        # Derriere un reverse proxy, l adresse vue est celle du proxy : elle est
        # privee quelle que soit la provenance reelle. Annoncer « acces local »
        # dans ce cas serait faux, et faux dans le sens rassurant. On dit alors
        # qu on ne sait pas, en rapportant ce que le relais pretend — sans le
        # croire : cet en-tete est fourni par le client autant que par le proxy.
        ip = self.client_address[0]
        relaye = bool(self.headers.get("X-Forwarded-For"))
        pretendue = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        self.reply(200, json.dumps({
            "installe": bool(visibles(users)),
            "utilisateur": self.session_user(),
            "acces": {"ip": pretendue if relaye else ip,
                      "local": (not relaye) and reseau.prive(ip),
                      "relaye": relaye},
            # Un compte de secours n apparait nulle part : il se rejoint en
            # tapant son nom, et son existence ne se lit pas depuis le dehors.
            "profils": [
                {"nom": u.get("nom"), "photo": u.get("photo", ""), "protege": bool(u.get("pwd"))}
                for u in visibles(users) if u.get("nom")
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

        # Meme cout que le compte existe ou non : sans cette empreinte de
        # comparaison, un compte inconnu repondrait instantanement quand un
        # compte protege prend pres d'une seconde, ce qui revelerait lesquels
        # existent.
        if u and u.get("pwd"):
            ok = auth.verifier(mdp, u.get("pwd"))
        elif u:
            auth.verifier(mdp, auth.EMPREINTE_LEURRE)
            ok = True  # profil sans mot de passe : acces libre, comme prevu
        else:
            auth.verifier(mdp, auth.EMPREINTE_LEURRE)
            ok = False
        if not ok:
            LIMITEUR.echec(cle)
            JOURNAL.noter(cle, nom, u is not None)
            self.reply(401, json.dumps({"error": "Nom d'utilisateur ou mot de passe incorrect."}, ensure_ascii=False).encode())
            return

        LIMITEUR.reussite(cle)

        # Une empreinte plus ancienne que la recommandation courante se refait
        # ici, seul moment ou le mot de passe en clair est disponible. Un echec
        # d'ecriture ne doit pas empecher la connexion : le compte reste
        # utilisable avec son ancienne empreinte, qui porte son propre nombre
        # d'iterations.
        if u and u.get("pwd") and auth.a_refaire(u.get("pwd")):
            try:
                frais = self.lire_config()
                cible = next((x for x in (frais.get("users") or [])
                              if x.get("nom") == nom), None)
                if cible and cible.get("pwd") == u.get("pwd"):
                    cible["pwd"] = auth.hacher(mdp)
                    self.ecrire_config(frais)
            except (OSError, ValueError):
                pass

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
        """Changement de son propre mot de passe, l'actuel a l'appui.

        On ne peut changer que le sien. Tant qu'Atrium n'a pas de roles, rien ne
        distingue un compte d'un autre : autoriser un profil a changer le mot de
        passe d'un autre reviendrait a laisser n'importe lequel prendre la main
        sur tous les autres, a plus forte raison depuis un profil sans mot de
        passe, qui n'a rien a prouver."""
        courant = self.session_user()
        if not courant:
            self.reply(401, json.dumps({"error": "authentification requise"}).encode())
            return
        d = self.json_recu() or {}
        ancien = str(d.get("ancien", ""))
        nouveau = str(d.get("nouveau", ""))
        cible = str(d.get("cible", "")).strip() or courant
        if cible != courant:
            self.reply(403, json.dumps(
                {"error": "On ne peut changer que son propre mot de passe."},
                ensure_ascii=False).encode())
            return

        cfg = self.lire_config()
        users = cfg.get("users") or []
        u = next((x for x in users if x.get("nom") == courant), None)
        if not u:
            self.reply(404, json.dumps({"error": "Profil introuvable."}, ensure_ascii=False).encode())
            return

        # l'actuel n'est pas exige tant qu'aucun n'a ete defini
        if u.get("pwd") and not auth.verifier(ancien, u.get("pwd")):
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
        SESSIONS.supprimer_utilisateur(courant)  # les autres appareils tombent
        jeton = SESSIONS.creer(courant, False, self.headers.get("User-Agent", ""),
                               self.client_address[0])
        self.reply(200, json.dumps({"ok": True}).encode(),
                   cookie=self.cookie_session(jeton, False))

    def installer(self):
        """Creation du premier compte : refusee des qu'un compte existe.
        (Ne pas nommer cette methode « setup » : BaseRequestHandler.setup est
        appelee a chaque connexion.)"""
        cfg = self.lire_config()
        # Un compte de secours cree par ATRIUM_ADMIN ne compte pas comme une
        # installation : il n a pas d ecran, pas de tableau de bord, et son
        # existence ne doit pas empecher le proprietaire de creer son profil.
        if visibles(cfg.get("users")):
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
        # Les comptes de secours survivent a l installation : ils ne sont pas
        # remplaces par le premier profil, seulement rejoints.
        caches = [u for u in (cfg.get("users") or []) if profil_cache(u)]
        if any(u.get("nom") == nom for u in caches):
            self.reply(409, json.dumps({"error": "Ce nom de profil n'est pas disponible."},
                                       ensure_ascii=False).encode())
            return
        cfg["users"] = [{"nom": nom, "pwd": auth.hacher(mdp) if mdp else "", "photo": ""}] + caches
        cfg.setdefault("apps", d.get("apps") or [])
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
        # Aucune origine tierce n'est autorisee : on repond sans en-tete CORS.
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
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
        # Le compte de secours est retire de la liste envoyee : il ne doit
        # apparaitre ni dans le panneau des profils, ni dans une sauvegarde
        # faite depuis le navigateur.
        cfg["users"] = visibles(cfg.get("users"))
        self.reply(200, json.dumps(cfg, ensure_ascii=False).encode())

    def cfg_post(self):
        recu = self.json_recu()
        if recu is None or not isinstance(recu, dict):
            self.reply(400, json.dumps({"error": "json invalide"}).encode())
            return

        actuel = self.lire_config()
        # Les comptes de secours ne sont jamais envoyes au navigateur : ils ne
        # peuvent donc ni etre modifies ni etre supprimes par cette route, et
        # sont remis en place tels quels a l enregistrement.
        caches = [u for u in (actuel.get("users") or []) if profil_cache(u)]
        users_actuels = visibles(actuel.get("users"))

        # Garde-fou : un navigateur qui repart d'un etat vide ne doit pas
        # effacer les comptes existants. Supprimer un profil passe par une
        # liste qui en contient encore au moins un.
        if users_actuels and not (recu.get("users") or []):
            self.reply(409, json.dumps(
                {"error": "Refus : cette requête supprimerait tous les profils."},
                ensure_ascii=False).encode())
            return

        # Un profil protege ne peut etre supprime que par lui-meme : sans cela,
        # n'importe quelle session — y compris celle d'un profil sans mot de
        # passe — pourrait effacer le compte des autres.
        courant = self.session_user()
        restants = {u.get("nom") for u in (recu.get("users") or [])}
        for u in users_actuels:
            if u.get("pwd") and u.get("nom") not in restants and u.get("nom") != courant:
                self.reply(403, json.dumps(
                    {"error": "Refus : « %s » est protégé par un mot de passe et ne "
                              "peut être supprimé que depuis son propre profil."
                              % u.get("nom")}, ensure_ascii=False).encode())
                return

        # Les empreintes de mots de passe restent l'affaire du serveur : le
        # navigateur ne peut ni les lire ni les remplacer via cette route.
        anciens = {u.get("nom"): u.get("pwd", "") for u in users_actuels}
        noms_caches = {u.get("nom") for u in caches}
        propres = []
        for u in (recu.get("users") or []):
            # Le drapeau « cache » ne se pose pas depuis le navigateur, et un
            # profil ordinaire ne peut pas usurper le nom d un compte de secours.
            u.pop("cache", None)
            if u.get("nom") in noms_caches:
                continue
            u["pwd"] = anciens.get(u.get("nom"), "")
            propres.append(u)
        recu["users"] = propres + caches

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
            if length > CORPS_MAX:
                self.reply(413, b"corps trop volumineux", "text/plain")
                return
            body = self.rfile.read(length) if length else b""

        # On vise l'adresse deja validee : entre la verification et la
        # connexion, une reponse DNS ne peut plus designer une autre machine.
        cible, hote = reseau.adresse_epinglee(target)
        if not cible:
            self.reply(502, b"cible non resolue", "text/plain")
            return

        req = urllib.request.Request(cible, data=body, method=self.command)
        if hote:
            req.add_header("Host", hote)
        for h in FORWARD_HEADERS:
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # equipements locaux : certificats auto-signes
        try:
            with OUVREUR.open(req, timeout=10) as r:
                data, code = r.read(CORPS_MAX), r.status
                ctype = r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            data, code = e.read(CORPS_MAX), e.code
            ctype = e.headers.get("Content-Type", "text/plain")
        except Exception as e:
            data, code, ctype = str(e).encode()[:500], 502, "text/plain"
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
    installer_admin_cache()
    systeme.demarrer_echantillonnage()
    demarrer_collecte()
    # Ecoute sur toutes les interfaces : dans un conteneur, seul le port
    # publie par Docker est joignable, et une ecoute sur 127.0.0.1 ne
    # sortirait pas du conteneur. L exposition est decidee par le -p, pas ici.
    Server(("0.0.0.0", PORT), Handler).serve_forever()  # nosec B104
