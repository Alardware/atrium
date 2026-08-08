"""Atrium — donnees affichees sur les tuiles.

Trois chiffres au maximum par application : ce qu on veut savoir d un coup
d oeil, pas un tableau de bord miniature. Chaque fonction renvoie une liste
[{"lab": "MOVIES", "val": "634"}] ou None.
"""
import json
import urllib.parse

from services import _chemin, _http, _json


def _n(v):
    """Formate un nombre : 56055 -> 56 055."""
    try:
        return "{:,}".format(int(v)).replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


def _octets(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    for unite in ("o/s", "Ko/s", "Mo/s", "Go/s"):
        if v < 1024:
            return "%.0f %s" % (v, unite) if unite == "o/s" else "%.1f %s" % (v, unite)
        v /= 1024
    return "%.1f To/s" % v


# --- media -------------------------------------------------------------------

def w_plex(base, cle):
    code, corps, _ = _http(base + "/status/sessions", {"X-Plex-Token": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    mc = j.get("MediaContainer") or {}
    n = mc.get("size")
    if n is None:
        n = len(mc.get("Metadata") or [])
    return [{"lab": "LECTURES", "val": _n(n)}]


def w_jellyfin(base, cle):
    code, corps, _ = _http(base + "/Sessions", {"X-Emby-Token": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, list):
        return None
    actives = [s for s in j if s.get("NowPlayingItem")]
    return [{"lab": "LECTURES", "val": _n(len(actives))}]


def w_tautulli(base, cle):
    code, corps, _ = _http(base + "/api/v2?apikey=%s&cmd=get_activity" % urllib.parse.quote(cle or ""))
    d = _chemin(_json(corps), "response", "data")
    if code != 200 or not isinstance(d, dict):
        return None
    return [
        {"lab": "LECTURES", "val": _n(d.get("stream_count", 0))},
        {"lab": "DÉBIT", "val": "%.1f Mb/s" % (float(d.get("total_bandwidth") or 0) / 1000)},
    ]


# --- suite *arr --------------------------------------------------------------

def _arr_compte(base, cle, chemin):
    code, corps, _ = _http(base + chemin, {"X-Api-Key": cle})
    j = _json(corps)
    if code != 200:
        return None
    if isinstance(j, dict) and "totalRecords" in j:
        return j["totalRecords"]
    if isinstance(j, list):
        return len(j)
    return None


def _w_arr(nom_collection, libelle):
    def widget(base, cle):
        total = _arr_compte(base, cle, "/api/v3/%s" % nom_collection)
        manquants = _arr_compte(base, cle, "/api/v3/wanted/missing?pageSize=1")
        file = _arr_compte(base, cle, "/api/v3/queue?pageSize=1")
        stats = []
        if manquants is not None:
            stats.append({"lab": "MANQUE", "val": _n(manquants)})
        if file is not None:
            stats.append({"lab": "FILE", "val": _n(file)})
        if total is not None:
            stats.append({"lab": libelle, "val": _n(total)})
        return stats or None
    return widget


def w_prowlarr(base, cle):
    code, corps, _ = _http(base + "/api/v1/indexer", {"X-Api-Key": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, list):
        return None
    actifs = [i for i in j if i.get("enable")]
    return [{"lab": "INDEXEURS", "val": _n(len(actifs))}]


def w_bazarr(base, cle):
    code, corps, _ = _http(base + "/api/badges", {"X-API-KEY": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    return [
        {"lab": "FILMS", "val": _n(j.get("movies", 0))},
        {"lab": "ÉPISODES", "val": _n(j.get("episodes", 0))},
    ]


# --- telechargement ----------------------------------------------------------

def w_sabnzbd(base, cle):
    code, corps, _ = _http(base + "/api?mode=queue&output=json&apikey=%s" % urllib.parse.quote(cle or ""))
    q = _chemin(_json(corps), "queue")
    if code != 200 or not isinstance(q, dict):
        return None
    return [
        {"lab": "DÉBIT", "val": "%.1f Mo/s" % (float(q.get("kbpersec") or 0) / 1024)},
        {"lab": "FILE", "val": _n(q.get("noofslots", 0))},
    ]


def w_qbittorrent(base, cle):
    code, corps, _ = _http(base + "/api/v2/transfer/info")
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    return [
        {"lab": "RÉCEPTION", "val": _octets(j.get("dl_info_speed", 0))},
        {"lab": "ENVOI", "val": _octets(j.get("up_info_speed", 0))},
    ]


# --- reseau ------------------------------------------------------------------

def w_adguard(base, cle):
    code, corps, _ = _http(base + "/control/stats")
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    return [
        {"lab": "REQUÊTES", "val": _n(j.get("num_dns_queries", 0))},
        {"lab": "BLOQUÉES", "val": _n(j.get("num_blocked_filtering", 0))},
    ]


def w_pihole(base, cle):
    code, corps, _ = _http(base + "/admin/api.php?summaryRaw&auth=%s" % urllib.parse.quote(cle or ""))
    j = _json(corps)
    if code != 200 or not isinstance(j, dict) or "dns_queries_today" not in j:
        return None
    return [
        {"lab": "REQUÊTES", "val": _n(j.get("dns_queries_today", 0))},
        {"lab": "BLOQUÉES", "val": _n(j.get("ads_blocked_today", 0))},
    ]


# --- outils ------------------------------------------------------------------

def w_portainer(base, cle):
    code, corps, _ = _http(base + "/api/endpoints", {"X-API-Key": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, list) or not j:
        return None
    actifs = arretes = 0
    for e in j:
        s = _chemin(e, "Snapshots") or []
        if s:
            actifs += s[0].get("RunningContainerCount", 0) or 0
            arretes += s[0].get("StoppedContainerCount", 0) or 0
    if actifs == arretes == 0:
        return None
    return [
        {"lab": "ACTIFS", "val": _n(actifs)},
        {"lab": "ARRÊTÉS", "val": _n(arretes)},
    ]


def w_uptimekuma(base, cle):
    code, corps, _ = _http(base + "/metrics", {"Authorization": ("Basic " + cle) if cle else None})
    if code != 200:
        return None
    up = corps.count(b'monitor_status{') and corps.count(b'} 1')
    down = corps.count(b'} 0')
    if not up and not down:
        return None
    return [{"lab": "EN LIGNE", "val": _n(up)}, {"lab": "HORS LIGNE", "val": _n(down)}]


def w_immich(base, cle):
    code, corps, _ = _http(base + "/api/server/statistics", {"x-api-key": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    return [
        {"lab": "PHOTOS", "val": _n(j.get("photos", 0))},
        {"lab": "VIDÉOS", "val": _n(j.get("videos", 0))},
    ]


def w_paperless(base, cle):
    code, corps, _ = _http(base + "/api/documents/?page_size=1",
                           {"Authorization": ("Token " + cle) if cle else None})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict) or "count" not in j:
        return None
    return [{"lab": "DOCUMENTS", "val": _n(j.get("count", 0))}]


def w_nextcloud(base, cle):
    code, corps, _ = _http(base + "/ocs/v2.php/apps/serverinfo/api/v1/info?format=json",
                           {"NC-Token": cle} if cle else None)
    d = _chemin(_json(corps), "ocs", "data", "nextcloud")
    if code != 200 or not isinstance(d, dict):
        return None
    n = _chemin(d, "storage", "num_users")
    f = _chemin(d, "storage", "num_files")
    stats = []
    if n is not None:
        stats.append({"lab": "UTILISATEURS", "val": _n(n)})
    if f is not None:
        stats.append({"lab": "FICHIERS", "val": _n(f)})
    return stats or None


REGISTRE = {
    "plex": w_plex,
    "jellyfin": w_jellyfin,
    "tautulli": w_tautulli,
    "sonarr": _w_arr("series", "SÉRIES"),
    "radarr": _w_arr("movie", "FILMS"),
    "lidarr": _w_arr("artist", "ARTISTES"),
    "readarr": _w_arr("author", "AUTEURS"),
    "whisparr": _w_arr("movie", "FILMS"),
    "prowlarr": w_prowlarr,
    "bazarr": w_bazarr,
    "sabnzbd": w_sabnzbd,
    "qbittorrent": w_qbittorrent,
    "adguard": w_adguard,
    "pihole": w_pihole,
    "portainer": w_portainer,
    "uptimekuma": w_uptimekuma,
    "immich": w_immich,
    "paperless": w_paperless,
    "nextcloud": w_nextcloud,
}


def mesurer(type_service, url, cle):
    fn = REGISTRE.get(type_service)
    if not fn:
        return None
    base = (url or "").strip().rstrip("/")
    if not base:
        return None
    try:
        stats = fn(base, cle or "")
    except Exception:
        return None
    return stats[:3] if stats else None
