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

TIMEOUT = 4
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE     # equipements locaux : certificats auto-signes


def _http(url, entetes=None, methode="GET", corps=None):
    req = urllib.request.Request(url, data=corps, method=methode)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Atrium")
    for k, v in (entetes or {}).items():
        if v:
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
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
    code, corps, _ = _http(base + "/api/", {"Authorization": "Bearer " + cle} if cle else None)
    if code == 200 and b"API running" in corps:
        return "Home Assistant", None
    if code == 401:
        return "Home Assistant", None       # present, mais jeton requis
    code, corps, _ = _http(base + "/manifest.json")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and "Home Assistant" in (j.get("name") or ""):
        return "Home Assistant", None
    return None


def s_unraid(base, cle):
    code, corps, _ = _http(
        base + "/graphql",
        {"Content-Type": "application/json", "x-api-key": cle},
        "POST",
        b'{"query":"{ metrics { cpu { percentTotal } } }"}',
    )
    if code in (200, 401, 403) and (b"metrics" in corps or b"API key" in corps or b"errors" in corps):
        return "Unraid", None
    return None


def s_unifi(base, cle):
    for racine in ("/proxy/network/integration/v1", "/integration/v1"):
        code, corps, _ = _http(base + racine + "/sites", {"X-API-KEY": cle})
        if code in (200, 401, 403):
            j = _json(corps)
            n = len(_chemin(j, "data") or []) if code == 200 else 0
            return ("UniFi" + (" — %d site(s)" % n if n else "")), None
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
    code, corps, _ = _http(base + "/api/v2/app/version")
    if code == 200 and re.match(rb"^v?\d", corps.strip()):
        return "qBittorrent", corps.decode("utf-8", "replace").strip()
    if code == 403:
        return "qBittorrent", None
    return None


def s_portainer(base, cle):
    code, corps, _ = _http(base + "/api/status", {"X-API-Key": cle})
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and ("Version" in j or "version" in j):
        return "Portainer", j.get("Version") or j.get("version")
    return None


def s_pihole(base, cle):
    code, corps, _ = _http(base + "/admin/api.php?status")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and "status" in j:
        return "Pi-hole", None
    code, corps, _ = _http(base + "/api/info/version")
    if code in (200, 401) and b"version" in corps.lower():
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
    if code in (200, 401) and (b"version" in corps or b"authentication" in corps.lower()):
        return "Proxmox", None
    return None


def s_vaultwarden(base, cle):
    code, corps, _ = _http(base + "/api/config")
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and (j.get("server") or {}).get("name"):
        return (j.get("server") or {}).get("name") or "Vaultwarden", (j.get("version") or None)
    return None


def s_paperless(base, cle):
    code, corps, _ = _http(base + "/api/", {"Authorization": ("Token " + cle) if cle else None})
    if code in (200, 401, 403) and (b"documents" in corps or b"detail" in corps):
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
    code, corps, _ = _http(base + "/rest/noauth/health")
    if code == 200 and b"OK" in corps:
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


# id interne, sonde, cle attendue (libelle) ; l ordre compte : du plus
# discriminant au plus generique
CATALOGUE = [
    ("plex", s_plex, "Jeton Plex (X-Plex-Token)"),
    ("unraid", s_unraid, "Clé API Unraid"),
    ("unifi", s_unifi, "Clé API UniFi"),
    ("ha", s_homeassistant, "Jeton longue durée"),
    ("jellyfin", s_jellyfin, "Clé API"),
    ("sonarr", s_arr("Sonarr"), "Clé API"),
    ("radarr", s_arr("Radarr"), "Clé API"),
    ("lidarr", s_arr("Lidarr"), "Clé API"),
    ("readarr", s_arr("Readarr"), "Clé API"),
    ("prowlarr", s_arr("Prowlarr"), "Clé API"),
    ("overseerr", s_overseerr, "Clé API"),
    ("tautulli", s_tautulli, "Clé API"),
    ("immich", s_immich, "Clé API"),
    ("nextcloud", s_nextcloud, ""),
    ("paperless", s_paperless, "Jeton d'API"),
    ("adguard", s_adguard, ""),
    ("pihole", s_pihole, "Mot de passe / jeton"),
    ("portainer", s_portainer, "Clé API"),
    ("proxmox", s_proxmox, "Jeton d'API"),
    ("grafana", s_grafana, "Clé API"),
    ("gitea", s_gitea, ""),
    ("npm", s_npm, ""),
    ("vaultwarden", s_vaultwarden, ""),
    ("qbittorrent", s_qbittorrent, ""),
    ("sabnzbd", s_sabnzbd, "Clé API"),
    ("syncthing", s_syncthing, "Clé API"),
    ("frigate", s_frigate, ""),
    ("uptimekuma", s_uptimekuma, ""),
]


def joignable(base):
    """Une seule requete pour savoir si quelque chose repond : sans cela, une
    adresse morte declencherait les 28 sondes, chacune jusqu au delai."""
    for chemin in ("/", ""):
        code, _, _ = _http(base + chemin)
        if code > 0:
            return code
    return 0


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
