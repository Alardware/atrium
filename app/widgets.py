"""Atrium — ce que chaque service sait dire de lui-meme.

Une integration lit tout ce que son API expose d interessant et en fait des
metriques typees, construites par metriques.M : identifiant, valeur numerique,
libelle et rendu. Le moteur d alertes raisonne sur le nombre, l interface
affiche le texte, et aucun des deux n a besoin de connaitre l autre.

CAPACITES declare, pour chaque type, ce qu il sait lire — de quoi l annoncer
avant meme la premiere mesure.
"""
import json
import re
import urllib.parse

from metriques import M, libelle
from services import _chemin, _http, _json

_PASSERELLE = re.compile(r"udm|uxg|ucg|usg|gateway|dream", re.I)
_BORNE = re.compile(r"\buap|u6|u7|nanohd|flexhd|lite\b|lr\b|ac-?pro|ac-?lite", re.I)


# --- media -------------------------------------------------------------------

def w_plex(base, cle):
    """Lectures, spectateurs, transcodages, debit et bibliotheques.

    Le debit et le transcodage viennent des sessions elles-memes : Plex les
    decrit dans chaque flux, inutile d interroger un service tiers."""
    code, corps, _ = _http(base + "/status/sessions", {"X-Plex-Token": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    mc = j.get("MediaContainer") or {}
    flux = mc.get("Metadata") or []
    n = mc.get("size")
    if n is None:
        n = len(flux)

    spectateurs, transcodages, kbps = set(), 0, 0
    for f in flux:
        u = _chemin(f, "User", "title")
        if u:
            spectateurs.add(u)
        for s in (f.get("Media") or []):
            for part in (s.get("Part") or []):
                deb = part.get("decision") or ""
                if deb == "transcode":
                    transcodages += 1
            try:
                kbps += int(s.get("bitrate") or 0)
            except (TypeError, ValueError):
                pass

    stats = [M("lectures", n)]
    if spectateurs:
        stats.append(M("spectateurs", len(spectateurs)))
    if transcodages:
        stats.append(M("transcodages", transcodages))
    if kbps:
        stats.append(M("debit", kbps / 1000.0))

    # Les bibliotheques ne bougent pas d une minute a l autre, mais elles
    # disent la taille de la mediatheque : on les compte a part.
    code, corps, _ = _http(base + "/library/sections", {"X-Plex-Token": cle})
    d = _chemin(_json(corps), "MediaContainer", "Directory")
    if code == 200 and isinstance(d, list) and d:
        stats.append(M("bibliotheques", len(d)))
    return stats


def w_jellyfin(base, cle):
    code, corps, _ = _http(base + "/Sessions", {"X-Emby-Token": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, list):
        return None
    actives = [s for s in j if s.get("NowPlayingItem")]
    return [M("lectures", len(actives))]


def w_tautulli(base, cle):
    code, corps, _ = _http(base + "/api/v2?apikey=%s&cmd=get_activity" % urllib.parse.quote(cle or ""))
    d = _chemin(_json(corps), "response", "data")
    if code != 200 or not isinstance(d, dict):
        return None
    return [
        M("lectures", d.get("stream_count", 0)),
        M("debit", float(d.get("total_bandwidth") or 0) / 1000),
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


def _w_arr(nom_collection, ident):
    def widget(base, cle):
        total = _arr_compte(base, cle, "/api/v3/%s" % nom_collection)
        manquants = _arr_compte(base, cle, "/api/v3/wanted/missing?pageSize=1")
        file = _arr_compte(base, cle, "/api/v3/queue?pageSize=1")
        stats = []
        if manquants is not None:
            stats.append(M("manque", manquants))
        if file is not None:
            stats.append(M("file", file))
        if total is not None:
            stats.append(M(ident, total))
        return stats or None
    return widget


def w_prowlarr(base, cle):
    code, corps, _ = _http(base + "/api/v1/indexer", {"X-Api-Key": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, list):
        return None
    actifs = [i for i in j if i.get("enable")]
    return [M("indexeurs", len(actifs))]


def w_bazarr(base, cle):
    code, corps, _ = _http(base + "/api/badges", {"X-API-KEY": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    return [
        M("films", j.get("movies", 0)),
        M("episodes", j.get("episodes", 0)),
    ]


# --- telechargement ----------------------------------------------------------

def w_sabnzbd(base, cle):
    code, corps, _ = _http(base + "/api?mode=queue&output=json&apikey=%s" % urllib.parse.quote(cle or ""))
    q = _chemin(_json(corps), "queue")
    if code != 200 or not isinstance(q, dict):
        return None
    return [
        M("debit_o", float(q.get("kbpersec") or 0) * 1024),
        M("file", q.get("noofslots", 0)),
    ]


def w_qbittorrent(base, cle):
    code, corps, _ = _http(base + "/api/v2/transfer/info")
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    return [
        M("reception", j.get("dl_info_speed", 0)),
        M("envoi", j.get("up_info_speed", 0)),
    ]


# --- reseau ------------------------------------------------------------------

def w_adguard(base, cle):
    code, corps, _ = _http(base + "/control/stats")
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    return [
        M("requetes", j.get("num_dns_queries", 0)),
        M("bloquees", j.get("num_blocked_filtering", 0)),
    ]


def w_pihole(base, cle):
    code, corps, _ = _http(base + "/admin/api.php?summaryRaw&auth=%s" % urllib.parse.quote(cle or ""))
    j = _json(corps)
    if code != 200 or not isinstance(j, dict) or "dns_queries_today" not in j:
        return None
    return [
        M("requetes", j.get("dns_queries_today", 0)),
        M("bloquees", j.get("ads_blocked_today", 0)),
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
    return [
        M("actifs", actifs),
        M("arretes", arretes),
    ]


def w_uptimekuma(base, cle):
    code, corps, _ = _http(base + "/metrics", {"Authorization": ("Basic " + cle) if cle else None})
    if code != 200:
        return None
    up = corps.count(b'monitor_status{') and corps.count(b'} 1')
    down = corps.count(b'} 0')
    if not up and not down:
        return None
    return [M("en_ligne", up), M("hors_ligne", down)]


def w_immich(base, cle):
    code, corps, _ = _http(base + "/api/server/statistics", {"x-api-key": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    return [
        M("photos", j.get("photos", 0)),
        M("videos", j.get("videos", 0)),
    ]


def w_paperless(base, cle):
    code, corps, _ = _http(base + "/api/documents/?page_size=1",
                           {"Authorization": ("Token " + cle) if cle else None})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict) or "count" not in j:
        return None
    return [M("documents", j.get("count", 0))]


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
        stats.append(M("utilisateurs", n))
    if f is not None:
        stats.append(M("fichiers", f))
    return stats or None


def _gabarit_ha(base, cle, gabarit):
    """Interroge Home Assistant par gabarit : la reponse tient en quelques
    octets, la ou /api/states renverrait l integralite des entites."""
    corps = json.dumps({"template": gabarit}).encode()
    code, rep, _ = _http(base.rstrip("/") + "/api/template",
                         {"Authorization": "Bearer " + cle,
                          "Content-Type": "application/json"}, "POST", corps)
    if code != 200:
        return None
    return rep.decode("utf-8", "replace").strip()


def w_ha(base, cle):
    """Etat de la maison : appareils presents, entites muettes, automatisations
    desactivees. Les deux dernieres ne s affichent que si elles ne sont pas
    nulles — une maison qui va bien n a pas besoin de deux zeros."""
    if not cle:
        return None
    rep = _gabarit_ha(base, cle,
                      "{{ states.device_tracker | selectattr('state','eq','home')"
                      " | list | count }}|"
                      "{{ states | selectattr('state','in',['unavailable','unknown'])"
                      " | list | count }}|"
                      "{{ states.automation | selectattr('state','eq','off')"
                      " | list | count }}|"
                      "{{ states.light | selectattr('state','eq','on') | list | count }}|"
                      "{{ states.binary_sensor"
                      " | selectattr('attributes.device_class','defined')"
                      " | selectattr('attributes.device_class','in',['door','garage_door','opening'])"
                      " | selectattr('state','eq','on') | list | count }}")
    if not rep or rep.count("|") != 4:
        return None
    try:
        presents, muettes, arretees, lumieres, portes = (int(x) for x in rep.split("|"))
    except ValueError:
        return None
    stats = []
    if muettes:
        stats.append(M("indispo", muettes))
    if arretees:
        stats.append(M("autom_off", arretees))
    if lumieres:
        stats.append(M("lumieres", lumieres))
    if portes:
        stats.append(M("ouvertures", portes))
    if presents:
        stats.append(M("presents", presents))
    return stats or None


def w_unraid(base, cle):
    """Charge du NAS, lue par l API GraphQL d Unraid."""
    if not cle:
        return None
    corps = json.dumps({"query": "{ metrics { cpu { percentTotal } "
                                 "memory { percentTotal } } "
                                 "array { capacity { kilobytes { used total } } "
                                 "disks { temp } } "
                                 "info { time uptime } "
                                 "docker { containers { state } } }"}).encode()
    code, rep, _ = _http(base + "/graphql",
                         {"x-api-key": cle, "Content-Type": "application/json"},
                         "POST", corps)
    d = _chemin(_json(rep), "data")
    if code != 200 or not isinstance(d, dict):
        return None
    stats = []
    cpu = _chemin(d, "metrics", "cpu", "percentTotal")
    if cpu is not None:
        stats.append(M("cpu", float(cpu)))
    mem = _chemin(d, "metrics", "memory", "percentTotal")
    if mem is not None:
        stats.append(M("ram", float(mem)))
    cap = _chemin(d, "array", "capacity", "kilobytes") or {}
    try:
        total = float(cap.get("total") or 0)
        if total > 0:
            stats.append(M("disque", float(cap.get("used") or 0) * 100 / total))
    except (TypeError, ValueError):
        pass

    # La grappe compte plusieurs disques : la temperature qui compte est la
    # plus elevee, c est elle qui declenchera une alerte.
    temps = [t for t in ((x or {}).get("temp") for x in (_chemin(d, "array", "disks") or []))
             if isinstance(t, (int, float)) and 0 < t < 120]
    if temps:
        stats.append(M("temp", max(temps)))

    up = _chemin(d, "info", "uptime")
    if isinstance(up, (int, float)) and up > 0:
        jours = int(up // 86400)
        stats.append(M("uptime", up,
                        ("%d j" % jours) if jours else ("%d h" % int(up // 3600))))

    conteneurs = _chemin(d, "docker", "containers")
    if isinstance(conteneurs, list) and conteneurs:
        actifs = sum(1 for c in conteneurs
                     if str((c or {}).get("state", "")).upper().startswith("RUNNING"))
        stats.append(M("docker", actifs, "%d / %d" % (actifs, len(conteneurs))))
    return stats or None


# La racine de l API et l identifiant du site ne changent pas : les retenir
# evite deux appels par cycle.
_UNIFI = {}


def _unifi_api(base, entetes):
    memo = _UNIFI.get(base)
    if memo:
        return memo
    for racine in (base + "/proxy/network/integration/v1", base + "/integration/v1"):
        code, rep, _ = _http(racine + "/sites", entetes)
        if code != 200:
            continue
        sites = (_json(rep) or {}).get("data") or []
        if sites and sites[0].get("id"):
            _UNIFI[base] = (racine, sites[0]["id"])
            return _UNIFI[base]
    return None


def w_unifi(base, cle):
    """Reseau : clients connectes et charge de la passerelle."""
    if not cle:
        return None
    entetes = {"X-API-KEY": cle, "Accept": "application/json"}
    api = _unifi_api(base, entetes)
    if not api:
        return None
    racine, site = api
    stats = []
    code, rep, _ = _http("%s/sites/%s/clients?limit=1" % (racine, site), entetes)
    if code == 401 or code == 403:
        _UNIFI.pop(base, None)     # cle changee : on refera la decouverte
        return None
    n = (_json(rep) or {}).get("totalCount") if code == 200 else None
    if n is not None:
        stats.append(M("clients", n))

    code, rep, _ = _http("%s/sites/%s/devices?limit=200" % (racine, site), entetes)
    liste = (_json(rep) or {}).get("data") or [] if code == 200 else []
    if liste:
        stats.append(M("equipements", len(liste)))
        bornes = [d for d in liste if _BORNE.search((d.get("model") or "")
                                                    + " " + (d.get("name") or ""))]
        if bornes:
            stats.append(M("bornes", len(bornes)))
    passerelle = next((d for d in liste if _PASSERELLE.search(
        (d.get("model") or "") + " " + (d.get("name") or ""))), None) or (liste[0] if liste else None)
    if passerelle and passerelle.get("id"):
        code, rep, _ = _http("%s/sites/%s/devices/%s/statistics/latest"
                             % (racine, site, passerelle["id"]), entetes)
        st = _json(rep) if code == 200 else None
        if isinstance(st, dict):
            if st.get("cpuUtilizationPct") is not None:
                stats.append(M("cpu", float(st["cpuUtilizationPct"])))
            if st.get("memoryUtilizationPct") is not None:
                stats.append(M("ram", float(st["memoryUtilizationPct"])))
    return stats or None


def maj_ha(base, cle):
    """Nombre de mises a jour en attente selon Home Assistant."""
    rep = _gabarit_ha(base, cle,
                      "{{ states.update | selectattr('state','eq','on') | list | count }}")
    try:
        return int(rep)
    except (TypeError, ValueError):
        return None


REGISTRE = {
    "plex": w_plex,
    "jellyfin": w_jellyfin,
    "tautulli": w_tautulli,
    "sonarr": _w_arr("series", "series"),
    "radarr": _w_arr("movie", "films"),
    "lidarr": _w_arr("artist", "artistes"),
    "readarr": _w_arr("author", "auteurs"),
    "whisparr": _w_arr("movie", "films"),
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
    "ha": w_ha,
    "unraid": w_unraid,
    "unifi": w_unifi,
}


# Ce que chaque integration sait lire. Sert a l annoncer sur la fiche de
# l application, avant meme la premiere mesure : l utilisateur voit ce qu il
# gagne a fournir une cle. Les libelles correspondent aux pastilles produites.
CAPACITES = {
    "plex": ["lectures", "spectateurs", "transcodages", "debit", "bibliotheques"],
    "jellyfin": ["lectures"],
    "tautulli": ["lectures", "debit"],
    "sonarr": ["manque", "file", "series"],
    "radarr": ["manque", "file", "films"],
    "lidarr": ["manque", "file", "artistes"],
    "readarr": ["manque", "file", "auteurs"],
    "whisparr": ["manque", "file", "films"],
    "prowlarr": ["indexeurs"],
    "bazarr": ["films", "episodes"],
    "sabnzbd": ["debit_o", "file"],
    "qbittorrent": ["reception", "envoi"],
    "adguard": ["requetes", "bloquees"],
    "pihole": ["requetes", "bloquees"],
    "portainer": ["actifs", "arretes"],
    "uptimekuma": ["en_ligne", "hors_ligne"],
    "immich": ["photos", "videos"],
    "paperless": ["documents"],
    "nextcloud": ["utilisateurs", "fichiers"],
    "ha": ["indispo", "autom_off", "lumieres", "ouvertures", "presents"],
    "unraid": ["cpu", "ram", "disque", "temp", "uptime", "docker"],
    "unifi": ["clients", "equipements", "bornes", "cpu", "ram"],
}


def capacites(type_service):
    """Ce qu une integration sait lire, en clair pour l interface."""
    return [{"id": i, "lab": libelle(i)} for i in CAPACITES.get(type_service, [])]


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
    # Plus de plafond ici : on remonte tout ce que le service donne, la fiche
    # de l application decide de ce qui apparait sur la tuile.
    return stats[:8] if stats else None
