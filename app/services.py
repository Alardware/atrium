"""Atrium — reconnaissance automatique des services.

Atrium interroge l URL fournie et identifie le service a sa signature : rien a
declarer, rien a choisir dans une liste. Si une cle est fournie, elle est
verifiee dans la foulee.

Ajouter un service = ajouter une entree dans CATALOGUE.
"""
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

import reseau

TIMEOUT = 4
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE     # equipements locaux : certificats auto-signes


def _http(url, entetes=None, methode="GET", corps=None):
    # Les appelants verifient deja la destination ; on la reverifie ici parce
    # que ce lecteur sert a toutes les integrations et qu un appel ajoute plus
    # tard oublierait la garde. urlopen accepte « file:// » et « ftp:// » : une
    # URL de service mal formee lirait un fichier du conteneur.
    if not reseau.autorise(url):
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
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:  # nosec B310
            return r.status, r.read(200000), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            corps_err = e.read(20000)
        except Exception:
            corps_err = b""
        return e.code, corps_err, dict(e.headers or {})
    except Exception:
        return 0, b"", {}


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


def s_overseerr(base, cle):
    code, corps, _ = _http(base + "/api/v1/status", {"X-Api-Key": cle})
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and j.get("version"):
        return "Overseerr", j.get("version")
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
    code, corps, _ = _http(base + "/api/")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and "Nginx Proxy Manager" in json.dumps(j):
        return "Nginx Proxy Manager", None
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
    for v in ("4", "3"):
        code, corps, _ = _http(base + "/api/%s/status" % v)
        j = _json(corps)
        if code == 200 and isinstance(j, dict) and isinstance(j.get("version"), str):
            return "Glances", j["version"]
        code, corps, _ = _http(base + "/api/%s/cpu" % v)
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


def s_jellyseerr(base, cle):
    """Jellyseerr et Overseerr partagent l API ; on les distingue par les
    reglages exposes."""
    code, corps, _ = _http(base + "/api/v1/status", {"X-Api-Key": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict) or not j.get("version"):
        return None
    code2, corps2, _ = _http(base + "/api/v1/settings/public", {"X-Api-Key": cle})
    txt = (corps2 or b"").decode("utf-8", "replace").lower()
    nom = "Jellyseerr" if "jellyfin" in txt else "Overseerr"
    return nom, j.get("version")


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
    ("jellyseerr", s_jellyseerr, "Clé API"),
    ("overseerr", s_overseerr, "Clé API"),
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
    ("npm", s_npm, ""),
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


def identifier(url, cle=""):
    """Sonde l URL et renvoie le service reconnu."""
    base = (url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "http://" + base

    code_racine = joignable(base)
    if code_racine == 0:
        return {"trouve": False, "type": "", "joignable": False, "code": 0}

    for ident, sonde, libelle_cle in CATALOGUE:
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
            }
    # joignable mais non reconnu : la tuile fonctionnera en simple raccourci
    return {"trouve": False, "type": "", "joignable": True, "code": code_racine}
