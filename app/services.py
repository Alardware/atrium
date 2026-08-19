"""Atrium — reconnaissance automatique des services.

Atrium interroge l URL fournie et identifie le service a sa signature : rien a
declarer, rien a choisir dans une liste. Si une cle est fournie, elle est
verifiee dans la foulee.

Ajouter un service = ajouter une entree dans CATALOGUE.
"""
import base64
import errno
import json
import socket
import threading
import time
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

import nut

import reseau

TIMEOUT = 4
# Pendant une detection, on frappe a des dizaines de portes sur une machine du
# reseau local : un service qui accepte la connexion sans repondre ne doit pas
# couter quatre secondes a chaque essai.
DELAI_DETECTION = 1.5
# Au-dela, on renonce : mieux vaut une fiche sans integration tout de suite
# qu une page qui tourne pendant une minute.
BUDGET_DETECTION = 8
_delai = TIMEOUT
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE     # equipements locaux : certificats auto-signes


class _SansSuite(urllib.request.HTTPRedirectHandler):
    """Un ouvreur qui laisse la redirection visible au lieu de la suivre.

    Certaines identifications se jouent la : Jackett protege par un mot de
    passe d administration renvoie tout vers sa page de connexion, et c est
    justement ce renvoi qui le nomme.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OUVREUR_DIRECT = urllib.request.build_opener(
    _SansSuite, urllib.request.HTTPSHandler(context=_CTX))


# Le dernier echec reseau, par fil d execution : la collecte sonde plusieurs
# services a la fois, et le motif de l un ne doit pas etre pris pour celui d un
# autre.
_dernier = threading.local()


def _motif(e):
    """Le code court qui dit ce qui a echoue, ou rien si l on ne sait pas."""
    if isinstance(e, socket.timeout) or isinstance(e, TimeoutError):
        return "delai"
    if isinstance(e, socket.gaierror):
        return "dns"
    if isinstance(e, ssl.SSLError) or "certificate" in str(e).lower():
        return "certificat"
    err = getattr(e, "reason", e)
    numero = getattr(err, "errno", None)
    if numero == errno.EHOSTUNREACH or numero == errno.ENETUNREACH:
        return "sans_route"
    if numero == errno.ECONNREFUSED:
        return "refus"
    if numero == errno.ETIMEDOUT or isinstance(err, (socket.timeout, TimeoutError)):
        return "delai"
    if isinstance(err, socket.gaierror):
        return "dns"
    return ""


def motif_dernier():
    """Ce qui a fait echouer le dernier appel de ce fil, puis on l oublie."""
    m = getattr(_dernier, "motif", "")
    _dernier.motif = ""
    return m


def _http(url, entetes=None, methode="GET", corps=None, suivre=True):
    # Les appelants verifient deja la destination ; on la reverifie ici parce
    # que ce lecteur sert a toutes les integrations et qu un appel ajoute plus
    # tard oublierait la garde. urlopen accepte « file:// » et « ftp:// » : une
    # URL de service mal formee lirait un fichier du conteneur.
    if not reseau.autorise(url):
        # Deux refus differents : un nom qui ne se resout pas, et une adresse
        # qui se resout hors du reseau prive. Le second n est pas une panne,
        # c est la regle du relais.
        hote = urllib.parse.urlparse(url).hostname or ""
        _dernier.motif = "dns" if hote and not reseau.resoudre(hote) else "prive"
        return 0, b"", {}
    # On se connecte a l adresse deja validee, pas au nom : entre la
    # verification et la connexion, une reponse DNS ne peut plus designer une
    # autre machine. L en-tete Host garde le nom, que les reverse proxies du
    # reseau local attendent.
    cible, hote = reseau.adresse_epinglee(url)
    if not cible:
        return 0, b"", {}
    req = urllib.request.Request(cible, data=corps, method=methode)
    if hote:
        req.add_header("Host", hote)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Atrium")
    for k, v in (entetes or {}).items():
        if v:
            req.add_header(k, v)
    try:
        # Destination et schema deja valides ci-dessus par reseau.autorise.
        if not suivre:
            with _OUVREUR_DIRECT.open(req, timeout=_delai) as r:  # nosec B310
                return r.status, r.read(200000), dict(r.headers)
        with urllib.request.urlopen(req, timeout=_delai, context=_CTX) as r:  # nosec B310
            return r.status, r.read(200000), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            corps_err = e.read(20000)
        except Exception:
            corps_err = b""
        return e.code, corps_err, dict(e.headers or {})
    except Exception as e:                       # reseau, DNS, delai, certificat
        # Le systeme sait pourquoi il a echoue ; l interface, elle, ne disait
        # que « aucune reponse ». Un hote sans route et un port ferme ne se
        # corrigent pourtant pas de la meme facon.
        _dernier.motif = _motif(e)
        return 0, b"", {}


def basic(secret, utilisateur="glances"):
    """En-tete d authentification Basic, ou rien si aucun secret.

    Le champ de la fiche accepte deux ecritures : « utilisateur:secret », et le
    secret seul — l utilisateur vaut alors celui que le service cree par
    defaut. Les modules de Home Assistant, eux, demandent un compte Home
    Assistant : c est la premiere forme qu il faut alors donner.
    """
    secret = (secret or "").strip()
    if not secret:
        return {}
    couple = secret if ":" in secret else ("%s:%s" % (utilisateur, secret))
    return {"Authorization": "Basic "
            + base64.b64encode(couple.encode("utf-8")).decode("ascii")}


def _json(donnees):
    try:
        return json.loads(donnees.decode("utf-8", "replace"))
    except Exception:
        return None


def _chemin(obj, *cles):
    for c in cles:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(c)
    return obj


# --- sondes : chacune renvoie (nom_affiche, version) ou None -----------------

def s_plex(base, cle):
    code, corps, _ = _http(base + "/identity", {"X-Plex-Token": cle})
    j = _json(corps)
    mc = _chemin(j, "MediaContainer") or {}
    if code == 200 and (mc.get("machineIdentifier") or mc.get("version")):
        return mc.get("friendlyName") or "Plex", mc.get("version")
    if code == 200 and b"machineIdentifier" in corps:
        return "Plex", None
    return None


def s_jellyfin(base, cle):
    code, corps, _ = _http(base + "/System/Info/Public")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and j.get("Id") and "Version" in j:
        produit = (j.get("ProductName") or "").lower()
        nom = j.get("ServerName") or j.get("ProductName") or "Jellyfin"
        return nom, j.get("Version"), ("emby" if "emby" in produit else "jellyfin")
    return None


def s_homeassistant(base, cle):
    # un 401 nu ne prouve rien : quantite de services repondent ainsi sur
    # /api/. On exige le message de l API, ou le manifeste de l application.
    code, corps, _ = _http(base + "/api/", {"Authorization": "Bearer " + cle} if cle else None)
    if code == 200 and b"API running" in corps:
        return "Home Assistant", None
    code, corps, _ = _http(base + "/manifest.json")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and "home assistant" in (j.get("name") or "").lower():
        return "Home Assistant", None
    return None


def s_unraid(base, cle):
    code, corps, _ = _http(
        base + "/graphql",
        {"Content-Type": "application/json", "x-api-key": cle},
        "POST",
        b'{"query":"{ metrics { cpu { percentTotal } } }"}',
    )
    # soit les mesures arrivent, soit l API annonce explicitement une cle
    # manquante ou invalide : dans les deux cas c est bien Unraid
    if code == 200 and b"percentTotal" in corps:
        return "Unraid", None
    if b"API key" in corps or b"UNAUTHENTICATED" in corps:
        return "Unraid", None
    return None


def s_unifi(base, cle):
    """Reconnait un controleur UniFi, et rien d autre.

    Un 401 ne prouve rien : n importe quel service qui demande une
    authentification repond 401 sur une adresse inconnue — l ingress de Home
    Assistant le premier, ce qui faisait passer un module Glances pour un
    controleur UniFi. On exige donc une reponse qui ressemble a UniFi : la
    liste des sites servie par la cle, ou la page d etat, que le controleur
    publie sans authentification.
    """
    for racine in ("/proxy/network/integration/v1", "/integration/v1"):
        code, corps, _ = _http(base + racine + "/sites", {"X-API-KEY": cle})
        if code == 200:
            j = _json(corps)
            sites = _chemin(j, "data")
            if isinstance(sites, list):
                return ("UniFi" + (" — %d site(s)" % len(sites) if sites else "")), None

    # « /status » : sans cle, un controleur annonce sa version et son etat.
    code, corps, _ = _http(base + "/status")
    j = _json(corps)
    if code == 200 and isinstance(j, dict):
        meta = j.get("meta") or {}
        if isinstance(meta, dict) and (meta.get("server_version") or meta.get("up")):
            return "UniFi", meta.get("server_version")
    return None


def s_arr(produit):
    """Sonarr, Radarr, Lidarr, Readarr, Prowlarr : meme API."""
    def sonde(base, cle):
        for v in ("v3", "v1"):
            code, corps, _ = _http(base + "/api/%s/system/status" % v, {"X-Api-Key": cle})
            j = _json(corps)
            if code == 200 and isinstance(j, dict) and j.get("appName"):
                if produit.lower() in (j.get("appName") or "").lower():
                    return j.get("appName"), j.get("version")
                return None
            if code == 401 and produit == "Sonarr":
                pass
        return None
    return sonde


def s_qbittorrent(base, cle):
    """La version en clair, ou le refus caracteristique de l API.

    Un 403 nu ne prouve rien : beaucoup de serveurs refusent ainsi une adresse
    inconnue. Celui de qBittorrent nomme sa raison.
    """
    code, corps, _ = _http(base + "/api/v2/app/version")
    if code == 200 and re.match(rb"^v?\d", corps.strip()):
        return "qBittorrent", corps.decode("utf-8", "replace").strip()
    if code == 403 and b"forbidden" in corps.lower():
        return "qBittorrent", None
    return None


def s_portainer(base, cle):
    code, corps, _ = _http(base + "/api/status", {"X-API-Key": cle})
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and ("Version" in j or "version" in j):
        return "Portainer", j.get("Version") or j.get("version")
    return None


def s_pihole(base, cle):
    # « status » seul est un mot bien trop commun : on exige la valeur que
    # Pi-hole y met, « enabled » ou « disabled ».
    code, corps, _ = _http(base + "/admin/api.php?status")
    j = _json(corps)
    if code == 200 and isinstance(j, dict)             and str(j.get("status", "")).lower() in ("enabled", "disabled"):
        return "Pi-hole", None
    # Pi-hole 6 rend un objet « version ». Chercher le mot dans la reponse
    # suffisait a reconnaitre n importe quelle page web qui le contient — une
    # application a page unique repond la meme page a toutes les adresses.
    code, corps, _ = _http(base + "/api/info/version")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and isinstance(j.get("version"), (dict, str)):
        return "Pi-hole", None
    return None


def s_adguard(base, cle):
    code, corps, _ = _http(base + "/control/status")
    j = _json(corps)
    if code in (200, 401) and (isinstance(j, dict) and ("version" in j or "dns_addresses" in j)):
        return "AdGuard Home", (j or {}).get("version")
    return None


def s_nextcloud(base, cle):
    code, corps, _ = _http(base + "/status.php")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and j.get("productname"):
        return j.get("productname") or "Nextcloud", j.get("versionstring")
    return None


def s_immich(base, cle):
    for c in ("/api/server/version", "/api/server-info/version"):
        code, corps, _ = _http(base + c)
        j = _json(corps)
        if code == 200 and isinstance(j, dict) and "major" in j:
            return "Immich", "%s.%s.%s" % (j.get("major"), j.get("minor"), j.get("patch"))
    return None


def s_grafana(base, cle):
    code, corps, _ = _http(base + "/api/health")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and j.get("database"):
        return "Grafana", j.get("version")
    return None


def s_uptimekuma(base, cle):
    # signature propre a l application : chercher son nom dans la page d accueil
    # provoquerait des faux positifs (toute page qui cite « Uptime Kuma »)
    code, corps, _ = _http(base + "/api/entry-page")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and ("entryPage" in j or "type" in j):
        return "Uptime Kuma", None
    return None


def s_proxmox(base, cle):
    code, corps, _ = _http(base + "/api2/json/version", {"Authorization": cle} if cle else None)
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and isinstance(j.get("data"), dict) \
            and "version" in j["data"]:
        return "Proxmox", j["data"].get("version")
    # non authentifie, Proxmox refuse avec un corps vide mais un en-tete typique
    if code == 401 and b"PVEAuthCookie" in corps:
        return "Proxmox", None
    return None


def s_vaultwarden(base, cle):
    code, corps, _ = _http(base + "/api/config")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and (j.get("server") or {}).get("name"):
        return (j.get("server") or {}).get("name") or "Vaultwarden", (j.get("version") or None)
    return None


def s_paperless(base, cle):
    # la racine d API liste ses collections : « documents » et « correspondents »
    # ensemble sont propres a Paperless
    code, corps, _ = _http(base + "/api/", {"Authorization": ("Token " + cle) if cle else None})
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and "documents" in j and "correspondents" in j:
        return "Paperless-ngx", None
    return None


def s_tautulli(base, cle):
    code, corps, _ = _http(base + "/api/v2?apikey=%s&cmd=status" % urllib.parse.quote(cle or ""))
    j = _json(corps)
    if code == 200 and _chemin(j, "response", "result"):
        return "Tautulli", None
    return None


def s_sabnzbd(base, cle):
    code, corps, _ = _http(base + "/api?mode=version&output=json")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and j.get("version"):
        return "SABnzbd", j.get("version")
    return None


def s_syncthing(base, cle):
    """L etat rendu par l API, pas les deux lettres « OK » d une page.

    « /rest/noauth/health » repond un objet JSON : l exiger evite de prendre
    pour Syncthing toute page qui contient ce mot.
    """
    code, corps, _ = _http(base + "/rest/noauth/health")
    j = _json(corps)
    # La reponse de Syncthing ne contient que cette clef. Accepter n importe
    # quel objet qui porte « status: ok » reconnaitrait la sonde de sante de
    # tout autre service.
    if code == 200 and isinstance(j, dict) and list(j) == ["status"]             and str(j.get("status", "")).upper() == "OK":
        return "Syncthing", None
    return None


def s_npm(base, cle):
    """Nginx Proxy Manager, reconnu a la forme de sa reponse.

    Les versions 2.x ne portent plus leur nom : « /api/ » rend un objet de
    trois champs — un etat, un drapeau d installation, une version en trois
    nombres. Chercher « Nginx Proxy Manager » dans le corps ne trouvait donc
    plus rien, et le service restait non reconnu malgre une API en parfait
    etat de marche.

    La forme suffit a l identifier, et reste exigeante : un service qui repond
    seulement « OK » n en fait pas partie.
    """
    code, corps, _ = _http(base + "/api/")
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    if "Nginx Proxy Manager" in json.dumps(j):
        return "Nginx Proxy Manager", None
    v = j.get("version")
    if (j.get("status") == "OK" and "setup" in j and isinstance(v, dict)
            and all(k in v for k in ("major", "minor", "revision"))):
        return "Nginx Proxy Manager", "%s.%s.%s" % (v["major"], v["minor"], v["revision"])
    return None


def s_gitea(base, cle):
    code, corps, _ = _http(base + "/api/v1/version")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and j.get("version"):
        return "Gitea", j.get("version")
    return None


def s_frigate(base, cle):
    code, corps, _ = _http(base + "/api/version")
    if code == 200 and re.match(rb"^\d", corps.strip()):
        return "Frigate", corps.decode("utf-8", "replace").strip()
    return None


# --- NAS et systemes hotes ---------------------------------------------------

def s_truenas(base, cle):
    """TrueNAS CORE / SCALE, et HexOS qui repose dessus."""
    code, corps, _ = _http(base + "/api/v2.0/system/info",
                           {"Authorization": ("Bearer " + cle) if cle else None})
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and "version" in j and "system_product" in j:
        v = j.get("version") or ""
        nom = "TrueNAS SCALE" if "SCALE" in v.upper() else "TrueNAS"
        return nom, v or None
    # sans jeton l API refuse, mais son message est reconnaissable
    if code in (401, 403) and b"Not authenticated" in corps:
        return "TrueNAS", None
    return None


def s_omv(base, cle):
    code, corps, entetes = _http(base + "/rpc.php", methode="POST",
                                 corps=b'{"service":"System","method":"noop"}')
    if code in (200, 400, 401, 405) and (b"openmediavault" in corps.lower() or b"response" in corps.lower()):
        return "OpenMediaVault", None
    code, corps, _ = _http(base + "/")
    if code == 200 and b"openmediavault" in corps.lower():
        return "OpenMediaVault", None
    return None


def s_casaos(base, cle):
    # CasaOS enveloppe ses reponses dans {success: <code>, message: ...}
    for c in ("/v1/sys/health", "/v1/users/status"):
        code, corps, _ = _http(base + c)
        j = _json(corps)
        if code == 200 and isinstance(j, dict) and isinstance(j.get("success"), int) \
                and "message" in j:
            return "CasaOS", None
    return None


def s_cosmos(base, cle):
    code, corps, _ = _http(base + "/")
    if code == 200 and b"cosmos-ui" in corps:
        return "Cosmos", None
    return None


# --- supervision -------------------------------------------------------------

def s_netdata(base, cle):
    code, corps, _ = _http(base + "/api/v1/info")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and j.get("uid") and "version" in j:
        return "Netdata", j.get("version")
    return None


def s_glances(base, cle):
    """L API de Glances repond en JSON ; une page web ne le fait pas.

    Se contenter d un 200 sur « /api/4/status » reconnaissait n importe quel
    serveur qui rend la meme page a toutes les adresses.
    """
    # Glances peut etre protege — le module de Home Assistant l est par un
    # compte Home Assistant. Sans cette cle, la detection ne verrait qu un 401.
    entetes = basic(cle)
    for v in ("4", "3"):
        code, corps, _ = _http(base + "/api/%s/status" % v, entetes)
        j = _json(corps)
        if code == 200 and isinstance(j, dict) and isinstance(j.get("version"), str):
            return "Glances", j["version"]
        code, corps, _ = _http(base + "/api/%s/cpu" % v, entetes)
        j = _json(corps)
        if code == 200 and isinstance(j, dict) and "total" in j:
            return "Glances", None
    return None


# --- domotique ---------------------------------------------------------------

def s_openhab(base, cle):
    code, corps, _ = _http(base + "/rest/", {"Authorization": ("Bearer " + cle) if cle else None})
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and (j.get("runtimeInfo") or j.get("version")):
        ri = j.get("runtimeInfo") or {}
        return "openHAB", ri.get("version")
    return None


def s_domoticz(base, cle):
    code, corps, _ = _http(base + "/json.htm?type=command&param=getversion")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and j.get("status") == "OK" and "version" in j:
        return "Domoticz", j.get("version")
    return None


def s_iobroker(base, cle):
    code, corps, _ = _http(base + "/")
    if code == 200 and re.search(rb"iobroker", corps, re.I):
        return "ioBroker", None
    return None


# --- media complementaires ---------------------------------------------------

def s_kodi(base, cle):
    code, corps, _ = _http(base + "/jsonrpc", {"Content-Type": "application/json"}, "POST",
                           b'{"jsonrpc":"2.0","method":"JSONRPC.Version","id":1}')
    j = _json(corps)
    if code in (200, 401) and isinstance(j, dict) and ("result" in j or "error" in j) and "jsonrpc" in corps.decode("utf-8", "replace"):
        v = _chemin(j, "result", "version", "major")
        return "Kodi", (str(v) if v else None)
    return None


def s_navidrome(base, cle):
    code, corps, _ = _http(base + "/rest/ping.view?f=json&v=1.16.1&c=atrium")
    j = _json(corps)
    sr = _chemin(j, "subsonic-response") or {}
    if code == 200 and sr:
        return ("Navidrome" if "navidrome" in json.dumps(sr).lower() else "Serveur Subsonic"), sr.get("serverVersion")
    return None


def s_audiobookshelf(base, cle):
    code, corps, _ = _http(base + "/status")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and ("isInit" in j or "serverVersion" in j):
        return "Audiobookshelf", j.get("serverVersion")
    return None


def s_bazarr(base, cle):
    code, corps, _ = _http(base + "/api/system/status", {"X-API-KEY": cle})
    j = _json(corps)
    d = _chemin(j, "data") or {}
    if code == 200 and isinstance(d, dict) and "bazarr_version" in d:
        return "Bazarr", d.get("bazarr_version")
    return None


def s_jackett(base, cle):
    """Jackett, reconnu a la facon dont il refuse une cle.

    Son adresse Torznab repond en XML, et nomme son grief : « Invalid API
    Key ». Un mot de passe d administration ne change rien a cette reponse,
    la ou la liste des indexeurs, elle, se voit renvoyee vers l ecran de
    connexion. C est donc elle qui sert de signature.
    """
    code, corps, _ = _http(_jackett_torznab(base, cle, "indexers"))
    if code == 200 and b"<" in corps:
        bas = corps.lower()
        if b"invalid api key" in bas or b"<indexer" in bas or b"<indexers" in bas:
            return "Jackett", None
    # Sans mot de passe d administration, la liste JSON repond aussi.
    code, corps, _ = _http(_jackett_url(base, "indexers?configured=true", cle))
    if code == 200 and isinstance(_json(corps), list):
        return "Jackett", None
    return None


def _jackett_url(base, chemin, cle):
    """Jackett attend sa cle dans l adresse : « ?apikey=… »."""
    joint = "&" if "?" in chemin else "?"
    return "%s/api/v2.0/%s%sapikey=%s" % (base, chemin, joint,
                                          urllib.parse.quote(cle or ""))


def _jackett_torznab(base, cle, demande):
    """L adresse Torznab agregee, celle que la cle ouvre en toutes circonstances."""
    return ("%s/api/v2.0/indexers/all/results/torznab/api"
            "?apikey=%s&t=%s&configured=true"
            % (base, urllib.parse.quote(cle or ""), demande))


def s_seerr(base, cle):
    """Seerr, et ses deux ancetres.

    Overseerr et Jellyseerr ont fusionne en fevrier 2026 pour donner Seerr,
    qui poursuit la numerotation de Jellyseerr : une version 3 ou plus est un
    Seerr, une version 2 un Jellyseerr, une version 1 un Overseerr. Les trois
    partagent la meme API, on ne peut donc les distinguer qu ainsi — et par
    les reglages, ou seul Jellyseerr et son successeur nomment Jellyfin.

    Le nom rendu est celui que l instance se donne quand il differe : c est
    celui que l utilisateur lit dans son navigateur.
    """
    code, corps, _ = _http(base + "/api/v1/status", {"X-Api-Key": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict) or not j.get("version"):
        return None
    version = str(j.get("version") or "")
    code2, corps2, _ = _http(base + "/api/v1/settings/public", {"X-Api-Key": cle})
    reglages = _json(corps2)
    txt = (corps2 or b"").decode("utf-8", "replace").lower()

    majeure = 0
    m = re.match(r"v?(\d+)", version)
    if m:
        majeure = int(m.group(1))
    if majeure >= 3:
        produit, ident = "Seerr", "seerr"
    elif "jellyfin" in txt:
        produit, ident = "Jellyseerr", "jellyseerr"
    else:
        produit, ident = "Overseerr", "overseerr"

    titre = (reglages or {}).get("applicationTitle") if isinstance(reglages, dict) else None
    titre = (titre or "").strip()
    if titre and titre.lower() != produit.lower():
        return "%s (%s)" % (titre, produit), version, ident
    return produit, version, ident


def s_mylar(base, cle):
    code, corps, _ = _http(base + "/api?apikey=%s&cmd=getVersion" % urllib.parse.quote(cle or ""))
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and ("data" in j or "success" in j):
        return "Mylar3", None
    return None


def s_kapowarr(base, cle):
    code, corps, _ = _http(base + "/api/system/about?api_key=%s" % urllib.parse.quote(cle or ""))
    j = _json(corps)
    # « error » seul ne prouve rien : beaucoup d API repondent ainsi
    if code == 200 and isinstance(j, dict) and isinstance(j.get("result"), dict) \
            and "version" in j["result"]:
        return "Kapowarr", j["result"].get("version")
    return None


# --- reseau, fichiers, securite ---------------------------------------------

def s_wgeasy(base, cle):
    # marqueurs propres a WG-Easy : liste de pairs WireGuard, ou session
    # annoncant « requiresPassword »
    code, corps, _ = _http(base + "/api/wireguard/client")
    j = _json(corps)
    if code == 200 and isinstance(j, list) and (not j or (isinstance(j[0], dict) and "publicKey" in j[0])):
        return "WG-Easy", None
    code, corps, _ = _http(base + "/api/session")
    j = _json(corps)
    if code in (200, 401) and isinstance(j, dict) and "requiresPassword" in j:
        return "WG-Easy", None
    return None


def s_filebrowser(base, cle):
    # /health repond « healthy » ; un 401 sur /api/renew ne prouve rien
    code, corps, _ = _http(base + "/health")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and str(j.get("status", "")).lower() == "healthy":
        return "FileBrowser", None
    return None


def s_authentik(base, cle):
    # « detail » est le format d erreur de Django REST : trop repandu pour
    # servir de preuve. On exige la liste des capacites propre a authentik.
    code, corps, _ = _http(base + "/api/v3/root/config/",
                           {"Authorization": ("Bearer " + cle) if cle else None})
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and isinstance(j.get("capabilities"), list):
        return "authentik", None
    return None


# id interne, sonde, cle attendue (libelle) ; l ordre compte : du plus
# discriminant au plus generique
CATALOGUE = [
    # media
    ("plex", s_plex, "Jeton Plex (X-Plex-Token)"),
    ("jellyfin", s_jellyfin, "Clé API"),
    ("kodi", s_kodi, ""),
    ("navidrome", s_navidrome, ""),
    ("audiobookshelf", s_audiobookshelf, "Jeton d'API"),
    ("tautulli", s_tautulli, "Clé API"),
    # Une seule signature pour Seerr et ses ancetres : c est la meme API.
    ("seerr", s_seerr, "Clé API"),
    ("jackett", s_jackett, "Clé API"),
    # suite *arr
    ("sonarr", s_arr("Sonarr"), "Clé API"),
    ("radarr", s_arr("Radarr"), "Clé API"),
    ("lidarr", s_arr("Lidarr"), "Clé API"),
    ("readarr", s_arr("Readarr"), "Clé API"),
    ("whisparr", s_arr("Whisparr"), "Clé API"),
    ("prowlarr", s_arr("Prowlarr"), "Clé API"),
    ("bazarr", s_bazarr, "Clé API"),
    ("mylar", s_mylar, "Clé API"),
    ("kapowarr", s_kapowarr, "Clé API"),
    # telechargement
    ("qbittorrent", s_qbittorrent, ""),
    ("sabnzbd", s_sabnzbd, "Clé API"),
    # NAS et hyperviseurs
    ("unraid", s_unraid, "Clé API Unraid"),
    ("truenas", s_truenas, "Clé API"),
    ("omv", s_omv, ""),
    ("proxmox", s_proxmox, "Jeton d'API"),
    ("casaos", s_casaos, ""),
    ("cosmos", s_cosmos, ""),
    # domotique
    ("ha", s_homeassistant, "Jeton longue durée"),
    ("openhab", s_openhab, "Jeton d'API"),
    ("domoticz", s_domoticz, ""),
    ("iobroker", s_iobroker, ""),
    ("frigate", s_frigate, ""),
    # reseau
    ("unifi", s_unifi, "Clé API UniFi"),
    ("adguard", s_adguard, ""),
    ("pihole", s_pihole, "Mot de passe / jeton"),
    # Le libelle vide laissait croire qu il n y avait rien a saisir, alors que
    # le lecteur reclame un compte : l API de NPM ne delivre un jeton que
    # contre les identifiants de l administrateur.
    ("npm", s_npm, "Courriel:mot de passe du compte"),
    ("wgeasy", s_wgeasy, ""),
    # fichiers
    ("nextcloud", s_nextcloud, ""),
    ("immich", s_immich, "Clé API"),
    ("paperless", s_paperless, "Jeton d'API"),
    ("filebrowser", s_filebrowser, ""),
    ("syncthing", s_syncthing, "Clé API"),
    # outils
    ("portainer", s_portainer, "Clé API"),
    ("netdata", s_netdata, ""),
    ("glances", s_glances, ""),
    ("grafana", s_grafana, "Clé API"),
    ("uptimekuma", s_uptimekuma, ""),
    ("gitea", s_gitea, ""),
    ("vaultwarden", s_vaultwarden, ""),
    ("authentik", s_authentik, "Jeton d'API"),
]


def joignable(base):
    """Une seule requete pour savoir si quelque chose repond : sans cela, une
    adresse morte declencherait les 28 sondes, chacune jusqu au delai."""
    for chemin in ("/", ""):
        code, _, _ = _http(base + chemin)
        if code > 0:
            return code
    return 0


# Repli quand une fiche n a pas de type enregistre (application ajoutee avant
# la detection automatique) : le nom donne une piste. Une devinette fausse ne
# coute rien, l appel echoue et la tuile reste simplement sans chiffres.
NOMS_CONNUS = (
    ("home assistant", "ha"), ("hassio", "ha"), ("hass", "ha"),
    ("unraid", "unraid"), ("unifi", "unifi"), ("plex", "plex"),
    ("jellyfin", "jellyfin"), ("emby", "jellyfin"), ("tautulli", "tautulli"),
    ("sonarr", "sonarr"), ("radarr", "radarr"), ("lidarr", "lidarr"),
    ("readarr", "readarr"), ("whisparr", "whisparr"), ("prowlarr", "prowlarr"),
    ("bazarr", "bazarr"), ("sabnzbd", "sabnzbd"), ("qbittorrent", "qbittorrent"),
    ("deluge", "deluge"), ("transmission", "transmission"), ("nzbget", "nzbget"),
    ("jellyseerr", "jellyseerr"), ("overseerr", "overseerr"), ("seerr", "seerr"),
    ("jackett", "jackett"),
    ("adguard", "adguard"), ("pi-hole", "pihole"), ("pihole", "pihole"),
    ("nginx proxy manager", "npm"), ("wg-easy", "wgeasy"),
    ("portainer", "portainer"), ("uptime kuma", "uptimekuma"),
    ("glances", "glances"), ("netdata", "netdata"), ("grafana", "grafana"),
    ("gitea", "gitea"), ("authentik", "authentik"), ("vaultwarden", "vaultwarden"),
    ("proxmox", "proxmox"), ("truenas", "truenas"), ("openmediavault", "omv"),
    ("casaos", "casaos"),
    ("immich", "immich"), ("paperless", "paperless"), ("nextcloud", "nextcloud"),
    ("filebrowser", "filebrowser"), ("syncthing", "syncthing"),
    ("frigate", "frigate"), ("zigbee2mqtt", "zigbee2mqtt"), ("esphome", "esphome"),
)


def deviner_type(nom):
    n = (nom or "").strip().lower()
    for motif, ident in NOMS_CONNUS:
        if motif in n:
            return ident
    return ""


# Le port dit souvent qui ecoute. Commencer par ce qu il designe evite les
# vingt sondes qui, sinon, le precedent — c est la difference entre une
# detection immediate et une detection qui se fait attendre.
PORTS = {
    32400: ("plex",), 8096: ("jellyfin",), 8920: ("jellyfin",), 8123: ("ha",),
    61208: ("glances",), 5055: ("seerr",), 5056: ("seerr",),
    8989: ("sonarr",), 7878: ("radarr",), 8686: ("lidarr",), 8787: ("readarr",),
    9696: ("prowlarr",), 6767: ("bazarr",), 8181: ("tautulli",),
    8080: ("sabnzbd", "qbittorrent"), 8081: ("qbittorrent",), 8112: ("deluge",),
    9091: ("transmission",), 6789: ("nzbget",), 9000: ("portainer",),
    3001: ("uptimekuma",), 2283: ("immich",), 8000: ("paperless",),
    3000: ("grafana", "gitea"), 19999: ("netdata",), 8384: ("syncthing",),
    3080: ("adguard",), 81: ("npm",), 8006: ("proxmox",), 5000: ("nextcloud",),
    9925: ("kapowarr",), 51413: ("transmission",),
    3493: ("nut",),
}


def _ordre(base, nom=""):
    """Le catalogue, reordonne pour ce qu on s attend a trouver ici.

    Le port d abord, le nom de la fiche ensuite : « Seerr » sur le port 5055
    se reconnait au premier essai plutot qu au vingtieme. L ordre complet est
    conserve derriere : rien n est ecarte, seulement retarde.
    """
    tete = []
    try:
        port = urllib.parse.urlparse(base).port
    except ValueError:
        port = None
    devine = deviner_type(nom) if nom else ""
    if devine:
        tete.append(devine)
    tete.extend(PORTS.get(port, ()))
    if not tete:
        return CATALOGUE
    rang = {ident: i for i, ident in enumerate(tete)}
    return sorted(CATALOGUE, key=lambda e: rang.get(e[0], len(tete)))


# Ce que le champ « cle » attend, service par service. Le libelle seul ne
# suffit pas : « Cle API » ne dit pas qu il faut ecrire « utilisateur:motdepasse »,
# et personne ne peut le deviner. L interface traduit ces formes.
#
#   cle      une chaine delivree par le service
#   jeton    un jeton porteur (Bearer)
#   couple   « utilisateur:motdepasse », faute de cle d API
#   mdp      un mot de passe seul
#   admin    le jeton d administration du service
FORMATS = {
    "ha": "jeton", "plex": "cle", "jellyfin": "cle", "tautulli": "cle",
    "sonarr": "cle", "radarr": "cle", "lidarr": "cle", "readarr": "cle",
    "whisparr": "cle", "prowlarr": "cle", "bazarr": "cle", "jackett": "cle",
    "seerr": "cle", "jellyseerr": "cle", "overseerr": "cle",
    "sabnzbd": "cle", "qbittorrent": "couple", "deluge": "mdp",
    "transmission": "couple", "nzbget": "couple",
    "adguard": "couple", "pihole": "cle", "unifi": "cle", "npm": "couple",
    "wgeasy": "mdp", "cosmos": "jeton",
    "portainer": "cle", "uptimekuma": "cle", "grafana": "cle", "gitea": "jeton",
    "authentik": "jeton", "vaultwarden": "admin", "netdata": "aucun",
    "glances": "couple", "unraid": "cle", "proxmox": "jeton", "truenas": "jeton",
    "omv": "couple", "casaos": "aucun", "syncthing": "cle",
    "immich": "cle", "paperless": "cle", "nextcloud": "couple",
    "filebrowser": "couple", "frigate": "aucun",
    "kodi": "aucun", "navidrome": "couple", "audiobookshelf": "jeton",
    # upsd n exige un compte que si son administrateur l a voulu.
    "nut": "couple",
    "mylar": "cle", "kapowarr": "cle",
    "openhab": "aucun", "domoticz": "aucun", "iobroker": "aucun",
    "zigbee2mqtt": "aucun", "esphome": "aucun",
}


def format_cle(type_service):
    """La forme attendue par ce service, ou « cle » a defaut."""
    return FORMATS.get(type_service or "", "cle")


def identifier(url, cle="", nom=""):
    """Sonde l URL et renvoie le service reconnu."""
    base = (url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "http://" + base

    # NUT ne parle pas HTTP : aucune des signatures ci-dessous ne le
    # trouverait, et la premiere requete finirait en « injoignable ». Le port
    # le designe, on lui demande donc directement.
    p = urllib.parse.urlparse(base)
    if (p.port or 0) == nut.PORT:
        return _identifier_nut(p.hostname or "", p.port, cle)

    global _delai
    _delai = DELAI_DETECTION
    depart = time.monotonic()
    try:
        code_racine = joignable(base)
        if code_racine == 0:
            # Pourquoi ca ne repond pas : sans route, port ferme, delai, nom
            # introuvable. L interface le dit au lieu d un « aucune reponse »
            # qui laisse chercher au mauvais endroit.
            return {"trouve": False, "type": "", "joignable": False, "code": 0,
                    "motif": motif_dernier()}
        return _parcourir(base, cle, nom, code_racine, depart)
    finally:
        _delai = TIMEOUT


def _identifier_nut(hote, port, cle):
    """Un onduleur publie par NUT, ou l explication de son refus."""
    try:
        trouve = nut.identifier(hote, port, cle)
    except nut.Refus as e:
        # upsd a repondu : il est bien la, mais il veut un compte.
        return {"trouve": False, "type": "nut", "joignable": True, "code": 0,
                "indice": "nut_refus", "message": str(e),
                "cle_libelle": "Utilisateur:mot de passe (si upsd en demande un)",
                "cle_requise": True}
    except OSError:
        trouve = None
    if not trouve:
        return {"trouve": False, "type": "", "joignable": False, "code": 0}
    return {"trouve": True, "type": "nut", "nom": trouve[0], "version": trouve[1],
            "cle_libelle": "", "cle_requise": False, "joignable": True, "code": 200}


def _parcourir(base, cle, nom, code_racine, depart):
    for ident, sonde, libelle_cle in _ordre(base, nom):
        # Une machine qui accepte les connexions sans jamais repondre ferait
        # durer la detection une minute et demie. Passe ce budget, on rend ce
        # qu on sait : la fiche fonctionnera en raccourci, et l utilisateur
        # peut toujours choisir l integration a la main.
        if time.monotonic() - depart > BUDGET_DETECTION:
            return {"trouve": False, "type": "", "joignable": True,
                    "code": code_racine, "indice": "trop_lent"}
        try:
            res = sonde(base, cle or "")
        except Exception:
            res = None
        if res:
            nom, version = res[0], res[1]
            type_reel = res[2] if len(res) > 2 else ident
            return {
                "trouve": True,
                "type": type_reel,
                "nom": nom,
                "version": version,
                "cle_libelle": libelle_cle,
                "cle_requise": bool(libelle_cle),
                "cle_format": format_cle(type_reel),
            }
    # joignable mais non reconnu : la tuile fonctionnera en simple raccourci.
    # Un cas revient assez souvent pour meriter d etre nomme : l adresse d un
    # module vue depuis l interface de Home Assistant. Elle rend la page de
    # Home Assistant, jamais l API du module — quelle que soit l adresse
    # demandee derriere. Le dire evite de chercher une cle qui n existe pas.
    return {"trouve": False, "type": "", "joignable": True, "code": code_racine,
            "indice": "ha_frontend" if _page_home_assistant(base) else ""}


def _page_home_assistant(base):
    """Cette adresse rend-elle l interface de Home Assistant ?"""
    code, corps, entetes = _http(base)
    if code != 200 or "html" not in str(entetes.get("Content-Type", "")).lower():
        return False
    bas = corps.lower()
    return b"<title>home assistant</title>" in bas or b"/frontend_latest/" in bas
