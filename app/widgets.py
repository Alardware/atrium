"""Atrium — ce que chaque service sait dire de lui-meme.

Une integration lit tout ce que son API expose d interessant et en fait des
metriques typees, construites par metriques.M : identifiant, valeur numerique,
libelle et rendu. Le moteur d alertes raisonne sur le nombre, l interface
affiche le texte, et aucun des deux n a besoin de connaitre l autre.

CAPACITES declare, pour chaque type, ce qu il sait lire — de quoi l annoncer
avant meme la premiere mesure.
"""
import inspect
import json
import re
import time
import urllib.parse

import services
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


def w_seerr(base, cle):
    """Les demandes de media : ce qu on attend, ce qui a ete accorde.

    Seerr, Jellyseerr et Overseerr partagent cette API. Le compteur donne
    l etat de la file en un appel — c est ce qu on regarde en passant : y a-t-il
    quelque chose a valider.
    """
    code, corps, _ = _http(base + "/api/v1/request/count", {"X-Api-Key": cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    stats = []
    for ident, champ in (("en_attente", "pending"), ("approuvees", "approved"),
                         ("disponibles", "available")):
        v = j.get(champ)
        if isinstance(v, (int, float)):
            stats.append(M(ident, int(v)))
    return stats or None


def _jackett_torznab_liste(base, cle, diag):
    """Les indexeurs listes par l adresse Torznab, que la cle ouvre toujours.

    Meme protege par un mot de passe d administration, Jackett repond ici : ce
    chemin s authentifie par la cle et non par le cookie de l interface.
    """
    code, corps, _ = _http(services._jackett_torznab(base, cle, "indexers"))
    if code != 200 or b"<" not in corps:
        return None
    texte = corps.decode("utf-8", "replace")
    if "Invalid API Key" in texte:
        if diag is not None:
            diag.setdefault("refus", []).append("cle API refusee par Jackett")
        return None
    # « <indexer id="…" configured="true"> » : on compte les balises plutot que
    # d embarquer un analyseur XML pour trois attributs.
    total = len(re.findall(r"<indexer\b", texte))
    if not total:
        return None
    return [M("indexeurs", total)]


def w_jackett(base, cle, diag=None):
    """Les indexeurs configures, et ceux qui ne repondent plus.

    Jackett ne compte pas ses indexeurs : il les liste. On les compte donc ici,
    et l on distingue ceux qu il declare en erreur — un indexeur casse fait
    echouer les recherches sans rien dire.

    Sa cle voyage dans l adresse. Si un mot de passe d administration est pose,
    cette liste passe derriere l ecran de connexion : on la redemande alors par
    l adresse Torznab, qui s authentifie par la cle en toutes circonstances —
    au prix du detail des erreurs, que celle-ci ne donne pas.
    """
    code, corps, _ = _http(services._jackett_url(
        base, "indexers?configured=true", cle))
    j = _json(corps)
    if code != 200 or not isinstance(j, list):
        # Liste refusee : c est le cas quand un mot de passe d administration
        # protege l interface. L adresse Torznab, elle, ne demande que la cle.
        return _jackett_torznab_liste(base, cle, diag)
    casses = sum(1 for i in j if isinstance(i, dict)
                 and (i.get("last_error") or "").strip())
    stats = [M("indexeurs", len(j))]
    if casses:
        stats.append(M("erreurs", casses))
    return stats


# --- video-surveillance, supervision, synchronisation, virtualisation --------

def w_frigate(base, cle):
    """Les cameras suivies et ce qu elles ont vu depuis hier.

    « /api/stats » donne l etat du service ; les evenements se comptent a part,
    sur vingt-quatre heures — c est la question qu on se pose en passant : est-ce
    que quelque chose a bouge cette nuit.
    """
    entetes = {"X-Api-Key": cle} if cle else None
    code, corps, _ = _http(base + "/api/stats", entetes)
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    stats = []
    cameras = j.get("cameras")
    if isinstance(cameras, dict):
        stats.append(M("cameras", len(cameras)))
    marche = _chemin(j, "service", "uptime")
    if isinstance(marche, (int, float)) and marche > 0:
        jours = int(marche // 86400)
        stats.append(M("uptime", marche,
                       ("%d j" % jours) if jours else ("%d h" % int(marche // 3600))))

    depuis = int(time.time()) - 86400
    code, corps, _ = _http(base + "/api/events?limit=500&after=%d" % depuis, entetes)
    evs = _json(corps)
    if code == 200 and isinstance(evs, list):
        stats.append(M("detections", len(evs)))
    return stats or None


def _netdata_serie(base, chart, cle):
    """La derniere valeur d un graphe, dimension par dimension."""
    code, corps, _ = _http(
        base + "/api/v1/data?chart=%s&after=-1&points=1&format=json" % chart,
        {"X-Api-Key": cle} if cle else None)
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return {}
    etiquettes = j.get("labels") or []
    lignes = j.get("data") or []
    if not etiquettes or not lignes or not isinstance(lignes[0], list):
        return {}
    # La premiere colonne est l horodatage ; les suivantes portent leur nom.
    return {etiquettes[i]: lignes[0][i]
            for i in range(1, min(len(etiquettes), len(lignes[0])))
            if isinstance(lignes[0][i], (int, float))}


def w_netdata(base, cle):
    """Charge de la machine et alarmes en cours, telles que Netdata les voit."""
    stats = []
    cpu = _netdata_serie(base, "system.cpu", cle)
    if cpu:
        # Netdata detaille l occupation par mode ; l inverse du repos est ce
        # qu on lit ailleurs sous le nom de « CPU ».
        repos = cpu.get("idle")
        if isinstance(repos, (int, float)):
            stats.append(M("cpu", max(0.0, 100.0 - float(repos))))
        else:
            stats.append(M("cpu", float(sum(cpu.values()))))
    ram = _netdata_serie(base, "system.ram", cle)
    total = sum(v for v in ram.values() if isinstance(v, (int, float)))
    if total > 0 and "used" in ram:
        stats.append(M("ram", float(ram["used"]) * 100 / total))

    code, corps, _ = _http(base + "/api/v1/info", {"X-Api-Key": cle} if cle else None)
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and isinstance(j.get("alarms"), dict):
        al = j["alarms"]
        stats.append(M("alarmes", sum(int(al.get(k) or 0)
                                      for k in ("warning", "critical"))))
    return stats or None


def w_syncthing(base, cle):
    """Dossiers partages, appareils joignables, et depuis quand ca tourne."""
    if not cle:
        return None
    entetes = {"X-API-Key": cle}
    stats = []
    code, corps, _ = _http(base + "/rest/config/folders", entetes)
    j = _json(corps)
    if code == 200 and isinstance(j, list):
        stats.append(M("dossiers", len(j)))

    code, corps, _ = _http(base + "/rest/system/connections", entetes)
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and isinstance(j.get("connections"), dict):
        liens = j["connections"]
        joints = sum(1 for c in liens.values()
                     if isinstance(c, dict) and c.get("connected"))
        # « 2 / 3 » plutot que « 2 » : un appareil absent est ce qu on cherche.
        stats.append(M("appareils", joints, "%d / %d" % (joints, len(liens))))

    code, corps, _ = _http(base + "/rest/system/status", entetes)
    j = _json(corps)
    if code == 200 and isinstance(j, dict) and isinstance(j.get("uptime"), (int, float)):
        marche = j["uptime"]
        jours = int(marche // 86400)
        stats.append(M("uptime", marche,
                       ("%d j" % jours) if jours else ("%d h" % int(marche // 3600))))
    return stats or None


def w_proxmox(base, cle):
    """Charge du noeud et machines en marche.

    La cle attendue est un jeton d API complet — « utilisateur@pam!nom=secret ».
    Proxmox le veut prefixe : on ajoute le prefixe si l utilisateur ne l a pas
    recopie, ce que personne ne fait de memoire.
    """
    if not cle:
        return None
    jeton = cle if cle.lower().startswith("pveapitoken=") else ("PVEAPIToken=" + cle)
    entetes = {"Authorization": jeton}
    code, corps, _ = _http(base + "/api2/json/nodes", entetes)
    j = _json(corps)
    noeuds = j.get("data") if isinstance(j, dict) else None
    if code != 200 or not isinstance(noeuds, list) or not noeuds:
        return None
    stats = []
    # Un serveur Proxmox n a le plus souvent qu un noeud ; s il en a plusieurs,
    # on montre le plus charge — c est celui qui posera probleme.
    charges = [n.get("cpu") for n in noeuds if isinstance(n.get("cpu"), (int, float))]
    if charges:
        stats.append(M("cpu", max(charges) * 100))
    memoires = [(n["mem"], n["maxmem"]) for n in noeuds
                if isinstance(n.get("mem"), (int, float))
                and isinstance(n.get("maxmem"), (int, float)) and n.get("maxmem")]
    if memoires:
        stats.append(M("ram", max(m * 100.0 / t for m, t in memoires)))
    marches = [n.get("uptime") for n in noeuds if isinstance(n.get("uptime"), (int, float))]
    if marches:
        marche = max(marches)
        jours = int(marche // 86400)
        stats.append(M("uptime", marche,
                       ("%d j" % jours) if jours else ("%d h" % int(marche // 3600))))

    code, corps, _ = _http(base + "/api2/json/cluster/resources?type=vm", entetes)
    j = _json(corps)
    vms = j.get("data") if isinstance(j, dict) else None
    if code == 200 and isinstance(vms, list) and vms:
        actives = sum(1 for v in vms
                      if str((v or {}).get("status", "")).lower() == "running")
        stats.append(M("machines", actives, "%d / %d" % (actives, len(vms))))
    return stats or None


# --- services restes longtemps muets ----------------------------------------
#
# Trois familles d authentification se partagent ce qui suit : la cle dans un
# en-tete, la cle dans l adresse, et le couple « utilisateur:motdepasse » qu il
# faut echanger contre un jeton. La derniere passe par _session_jeton, ecrit une
# fois pour toutes.


def _couple(cle):
    """« utilisateur:motdepasse » -> (utilisateur, motdepasse)."""
    cle = (cle or "").strip()
    if ":" not in cle:
        return "", cle
    u, _, m = cle.partition(":")
    return u.strip(), m


def _poster(url, corps, entetes=None):
    """Un POST JSON, et sa reponse decodee."""
    tetes = {"Content-Type": "application/json"}
    tetes.update(entetes or {})
    code, brut, ent = _http(url, tetes, "POST", json.dumps(corps).encode())
    return code, _json(brut), ent


def w_kodi(base, cle):
    """Lectures en cours, films et series de la mediatheque."""
    def rpc(methode, params=None):
        code, j, _ = _poster(base + "/jsonrpc",
                             {"jsonrpc": "2.0", "id": 1, "method": methode,
                              "params": params or {}},
                             services.basic(cle, "kodi") if cle else None)
        return j.get("result") if code == 200 and isinstance(j, dict) else None

    joueurs = rpc("Player.GetActivePlayers")
    if joueurs is None:
        return None
    stats = [M("lectures", len(joueurs) if isinstance(joueurs, list) else 0)]
    for methode, ident in (("VideoLibrary.GetMovies", "films"),
                           ("VideoLibrary.GetTVShows", "series")):
        r = rpc(methode, {"limits": {"start": 0, "end": 1}})
        total = _chemin(r or {}, "limits", "total")
        if isinstance(total, int):
            stats.append(M(ident, total))
    return stats


def w_navidrome(base, cle):
    """Titres de la bibliotheque, par l API Subsonic.

    La cle s ecrit « utilisateur:motdepasse » : Subsonic n a pas de jeton, il
    signe chaque appel avec le mot de passe.
    """
    utilisateur, motdepasse = _couple(cle)
    if not utilisateur or not motdepasse:
        return None
    url = (base + "/rest/getScanStatus?u=%s&p=%s&v=1.16.1&c=atrium&f=json"
           % (urllib.parse.quote(utilisateur), urllib.parse.quote(motdepasse)))
    code, corps, _ = _http(url)
    j = _json(corps)
    compte = _chemin(j or {}, "subsonic-response", "scanStatus", "count")
    if code != 200 or not isinstance(compte, (int, float)):
        return None
    return [M("titres", int(compte))]


def w_audiobookshelf(base, cle):
    """Bibliotheques declarees et livres qu elles contiennent."""
    if not cle:
        return None
    entetes = {"Authorization": "Bearer " + cle}
    code, corps, _ = _http(base + "/api/libraries", entetes)
    j = _json(corps)
    biblios = (j or {}).get("libraries") if isinstance(j, dict) else None
    if code != 200 or not isinstance(biblios, list):
        return None
    stats = [M("bibliotheques", len(biblios))]
    total = 0
    lus = False
    for b in biblios[:6]:      # au-dela, le compte coute plus qu il ne dit
        ident = (b or {}).get("id")
        if not ident:
            continue
        code, corps, _ = _http(base + "/api/libraries/%s/items?limit=1" % ident, entetes)
        j = _json(corps)
        n = (j or {}).get("total") if isinstance(j, dict) else None
        if isinstance(n, int):
            total += n
            lus = True
    if lus:
        stats.append(M("livres", total))
    return stats


def w_mylar(base, cle):
    """Series suivies par Mylar."""
    code, corps, _ = _http(base + "/api?apikey=%s&cmd=getIndex"
                           % urllib.parse.quote(cle or ""))
    j = _json(corps)
    liste = (j or {}).get("data") if isinstance(j, dict) else j
    if code != 200 or not isinstance(liste, list):
        return None
    return [M("series", len(liste))]


def w_kapowarr(base, cle):
    """Volumes suivis par Kapowarr."""
    code, corps, _ = _http(base + "/api/volumes?api_key=%s"
                           % urllib.parse.quote(cle or ""))
    j = _json(corps)
    liste = (j or {}).get("result") if isinstance(j, dict) else None
    if code != 200 or not isinstance(liste, list):
        return None
    return [M("series", len(liste))]


def w_truenas(base, cle):
    """Grappes, remplissage et duree de marche."""
    if not cle:
        return None
    entetes = {"Authorization": "Bearer " + cle}
    stats = []
    code, corps, _ = _http(base + "/api/v2.0/pool", entetes)
    grappes = _json(corps)
    if code == 200 and isinstance(grappes, list) and grappes:
        stats.append(M("grappes", len(grappes)))
        pleins = []
        for g in grappes:
            libre = _chemin(g or {}, "free")
            taille = _chemin(g or {}, "size")
            if isinstance(libre, (int, float)) and isinstance(taille, (int, float)) and taille:
                pleins.append((taille - libre) * 100.0 / taille)
        if pleins:
            stats.append(M("disque", max(pleins)))
    code, corps, _ = _http(base + "/api/v2.0/system/info", entetes)
    j = _json(corps)
    marche = (j or {}).get("uptime_seconds") if isinstance(j, dict) else None
    if isinstance(marche, (int, float)) and marche > 0:
        jours = int(marche // 86400)
        stats.append(M("uptime", marche,
                       ("%d j" % jours) if jours else ("%d h" % int(marche // 3600))))
    return stats or None


def w_openhab(base, cle):
    """Objets et equipements declares dans openHAB."""
    entetes = {"Authorization": "Bearer " + cle} if cle else None
    stats = []
    for chemin, ident in (("/rest/items?fields=name", "objets"),
                          ("/rest/things?summary=true", "equipements")):
        code, corps, _ = _http(base + chemin, entetes)
        j = _json(corps)
        if code == 200 and isinstance(j, list):
            stats.append(M(ident, len(j)))
    return stats or None


def w_domoticz(base, cle):
    """Appareils utilises, et ceux qui sont allumes."""
    entetes = services.basic(cle, "admin") if cle else None
    code, corps, _ = _http(base + "/json.htm?type=devices&used=true&filter=all",
                           entetes)
    j = _json(corps)
    liste = (j or {}).get("result") if isinstance(j, dict) else None
    if code != 200 or not isinstance(liste, list):
        return None
    stats = [M("appareils", len(liste))]
    allumes = sum(1 for d in liste if isinstance(d, dict)
                  and str(d.get("Status", "")).lower().startswith("on"))
    if allumes:
        stats.append(M("actifs", allumes))
    return stats


def w_iobroker(base, cle):
    """Instances d adaptateurs declarees, par l adaptateur « simple-api »."""
    code, corps, _ = _http(base + "/objects?pattern=system.adapter.*&type=instance"
                           + ("&user=%s" % urllib.parse.quote(cle) if cle else ""))
    j = _json(corps)
    if code != 200 or not isinstance(j, dict) or not j:
        return None
    return [M("actifs", len(j))]


def w_grafana(base, cle):
    """Tableaux de bord publies et alertes en cours."""
    if not cle:
        return None
    entetes = {"Authorization": "Bearer " + cle}
    stats = []
    code, corps, _ = _http(base + "/api/search?type=dash-db&limit=1000", entetes)
    j = _json(corps)
    if code != 200 or not isinstance(j, list):
        return None
    stats.append(M("tableaux", len(j)))
    code, corps, _ = _http(base + "/api/alertmanager/grafana/api/v2/alerts", entetes)
    j = _json(corps)
    if code == 200 and isinstance(j, list):
        stats.append(M("alarmes", len(j)))
    return stats


def w_gitea(base, cle):
    """Depots visibles par cette cle.

    Le compte total voyage dans un en-tete : le corps ne rend qu une page.
    """
    if not cle:
        return None
    code, corps, entetes = _http(base + "/api/v1/repos/search?limit=1",
                                 {"Authorization": "token " + cle})
    j = _json(corps)
    if code != 200 or not isinstance(j, dict):
        return None
    total = entetes.get("X-Total-Count") or entetes.get("x-total-count")
    try:
        total = int(total)
    except (TypeError, ValueError):
        donnees = j.get("data")
        total = len(donnees) if isinstance(donnees, list) else None
    if total is None:
        return None
    return [M("depots", total)]


def w_authentik(base, cle):
    """Comptes et evenements, tels que l API les denombre."""
    if not cle:
        return None
    entetes = {"Authorization": "Bearer " + cle}
    stats = []
    for chemin, ident in (("/api/v3/core/users/?page_size=1", "utilisateurs"),
                          ("/api/v3/events/events/?page_size=1", "evenements")):
        code, corps, _ = _http(base + chemin, entetes)
        j = _json(corps)
        compte = _chemin(j or {}, "pagination", "count")
        if code == 200 and isinstance(compte, int):
            stats.append(M(ident, compte))
    return stats or None


def _session_jeton(base, chemin, corps, ou):
    """Echange un couple contre un jeton, et le rend.

    « ou » nomme le champ ou le jeton se trouve dans la reponse — chaque
    service a le sien, et aucun ne l appelle pareil.
    """
    code, j, _ = _poster(base + chemin, corps)
    if code not in (200, 201) or not isinstance(j, dict):
        return None
    for champ in ou:
        v = _chemin(j, *champ) if isinstance(champ, tuple) else j.get(champ)
        if isinstance(v, str) and v:
            return v
    return None


def w_npm(base, cle):
    """Hotes servis par Nginx Proxy Manager, et ceux qui sont eteints.

    La cle s ecrit « courriel:motdepasse » : ce service n a pas de cle d API,
    il delivre un jeton contre les identifiants.
    """
    utilisateur, motdepasse = _couple(cle)
    if not utilisateur or not motdepasse:
        return None
    jeton = _session_jeton(base, "/api/tokens",
                           {"identity": utilisateur, "secret": motdepasse},
                           ["token"])
    if not jeton:
        return None
    code, corps, _ = _http(base + "/api/nginx/proxy-hosts",
                           {"Authorization": "Bearer " + jeton})
    j = _json(corps)
    if code != 200 or not isinstance(j, list):
        return None
    stats = [M("hotes", len(j))]
    eteints = sum(1 for h in j if isinstance(h, dict) and not h.get("enabled", True))
    if eteints:
        stats.append(M("arretes", eteints))
    return stats


def w_wgeasy(base, cle):
    """Clients WireGuard declares, et ceux qui ont parle recemment.

    Selon la version, l interface demande un mot de passe ou rien du tout : on
    tente la session, puis la lecture directe.
    """
    entetes = {}
    if cle:
        code, j, ent = _poster(base + "/api/session", {"password": cle})
        biscuit = ent.get("Set-Cookie") if code in (200, 204) else None
        if biscuit:
            entetes["Cookie"] = biscuit.split(";")[0]
    code, corps, _ = _http(base + "/api/wireguard/client", entetes or None)
    j = _json(corps)
    if code != 200 or not isinstance(j, list):
        return None
    stats = [M("clients", len(j))]
    recents = 0
    for c in j:
        vu = (c or {}).get("latestHandshakeAt") if isinstance(c, dict) else None
        if isinstance(vu, str) and vu:
            recents += 1
    if recents:
        stats.append(M("actifs", recents))
    return stats


def w_filebrowser(base, cle):
    """Elements a la racine du navigateur de fichiers.

    La cle s ecrit « utilisateur:motdepasse » ; Filebrowser rend un jeton en
    texte brut, que l on repasse en en-tete.
    """
    utilisateur, motdepasse = _couple(cle)
    if not utilisateur or not motdepasse:
        return None
    code, brut, _ = _http(base + "/api/login", {"Content-Type": "application/json"},
                          "POST", json.dumps({"username": utilisateur,
                                              "password": motdepasse,
                                              "recaptcha": ""}).encode())
    jeton = brut.decode("utf-8", "replace").strip() if code == 200 else ""
    if not jeton or len(jeton) > 4096 or "<" in jeton:
        return None
    code, corps, _ = _http(base + "/api/resources/?auth=" + urllib.parse.quote(jeton),
                           {"X-Auth": jeton})
    j = _json(corps)
    items = (j or {}).get("items") if isinstance(j, dict) else None
    if code != 200 or not isinstance(items, list):
        return None
    return [M("fichiers", len(items))]


def w_omv(base, cle):
    """Systemes de fichiers montes et leur remplissage.

    OpenMediaVault n a pas de cle d API : on ouvre une session avec
    « utilisateur:motdepasse », puis on interroge son RPC.
    """
    utilisateur, motdepasse = _couple(cle)
    if not utilisateur or not motdepasse:
        return None
    code, j, ent = _poster(base + "/rpc.php",
                           {"service": "Session", "method": "login",
                            "params": {"username": utilisateur, "password": motdepasse}})
    if code != 200 or not isinstance(j, dict) or _chemin(j, "response", "authenticated") is not True:
        return None
    biscuit = (ent.get("Set-Cookie") or "").split(";")[0]
    entetes = {"Cookie": biscuit} if biscuit else None
    code, j, _ = _poster(base + "/rpc.php",
                         {"service": "FileSystemMgmt",
                          "method": "enumerateMountedFilesystems",
                          "params": {"includeroot": False}}, entetes)
    liste = (j or {}).get("response") if isinstance(j, dict) else None
    if code != 200 or not isinstance(liste, list) or not liste:
        return None
    stats = [M("grappes", len(liste))]
    pleins = []
    for f in liste:
        pc = (f or {}).get("percentage") if isinstance(f, dict) else None
        try:
            pleins.append(float(pc))
        except (TypeError, ValueError):
            continue
    if pleins:
        stats.append(M("disque", max(pleins)))
    return stats


def w_casaos(base, cle):
    """Charge de la machine, telle que CasaOS la publie."""
    entetes = {"Authorization": cle} if cle else None
    for chemin in ("/v1/sys/hardware/usage", "/v1/sys/utilization"):
        code, corps, _ = _http(base + chemin, entetes)
        j = _json(corps)
        donnees = (j or {}).get("data") if isinstance(j, dict) else None
        if code != 200 or not isinstance(donnees, dict):
            continue
        stats = []
        cpu = _chemin(donnees, "cpu", "percent")
        if isinstance(cpu, (int, float)):
            stats.append(M("cpu", float(cpu)))
        mem = _chemin(donnees, "mem", "usedPercent")
        if isinstance(mem, (int, float)):
            stats.append(M("ram", float(mem)))
        if stats:
            return stats
    return None


def w_cosmos(base, cle):
    """Routes servies par Cosmos, et celles qui sont eteintes."""
    entetes = {"Authorization": "Bearer " + cle} if cle else None
    code, corps, _ = _http(base + "/cosmos/api/config", entetes)
    j = _json(corps)
    routes = _chemin(j or {}, "data", "HTTPConfig", "ProxyConfig", "Routes")
    if code != 200 or not isinstance(routes, list):
        return None
    stats = [M("hotes", len(routes))]
    eteintes = sum(1 for r in routes if isinstance(r, dict) and r.get("Disabled"))
    if eteintes:
        stats.append(M("arretes", eteintes))
    return stats


def w_vaultwarden(base, cle):
    """Comptes du coffre, par la console d administration.

    La cle attendue est le jeton d administration (ADMIN_TOKEN) : Vaultwarden
    n expose aucun compte sans lui.
    """
    if not cle:
        return None
    code, _, ent = _http(base + "/admin", {"Content-Type": "application/x-www-form-urlencoded"},
                         "POST", urllib.parse.urlencode({"token": cle}).encode())
    biscuit = (ent.get("Set-Cookie") or "").split(";")[0]
    if code not in (200, 302) or not biscuit:
        return None
    code, corps, _ = _http(base + "/admin/users", {"Cookie": biscuit,
                                                   "Accept": "application/json"})
    j = _json(corps)
    if code != 200 or not isinstance(j, list):
        return None
    stats = [M("utilisateurs", len(j))]
    actifs = sum(1 for u in j if isinstance(u, dict) and not u.get("userEnabled") is False)
    if actifs and actifs != len(j):
        stats.append(M("actifs", actifs))
    return stats


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


def _unraid_graphql(base, cle, requete, diag=None, quoi=""):
    """Une requete GraphQL Unraid. Rend « data », ou None en disant pourquoi.

    GraphQL repond 200 meme lorsqu il refuse : le motif est dans « errors », et
    c est celui-la qu il faut lire pour savoir si la cle manque de portee ou si
    le schema a change.
    """
    corps = json.dumps({"query": requete}).encode()
    code, rep, _ = _http(base + "/graphql",
                         {"x-api-key": cle, "Content-Type": "application/json"},
                         "POST", corps)
    j = _json(rep)
    d = _chemin(j, "data")
    if code == 200 and isinstance(d, dict):
        return d
    if diag is not None:
        erreurs = _chemin(j, "errors") or []
        motif = ""
        if isinstance(erreurs, list) and erreurs:
            motif = str((erreurs[0] or {}).get("message") or "")[:120]
        diag.setdefault("refus", []).append(
            "%s : %s" % (quoi, motif or ("HTTP %s" % code)))
    return None


def w_unraid(base, cle, diag=None):
    """Charge du NAS, lue par l API GraphQL d Unraid.

    Les quatre familles sont demandees separement : une branche refusee — Docker
    hors de la portee de la cle, grappe absente d une version — ne doit pas
    emporter les trois autres. Une requete de plus par cycle sur le reseau local
    coute quelques millisecondes ; perdre toutes les mesures coute la tuile.
    """
    if not cle:
        return None
    charge = _unraid_graphql(
        base, cle, "{ metrics { cpu { percentTotal } memory { percentTotal } } }",
        diag, "charge")
    grappe = _unraid_graphql(
        base, cle, "{ array { capacity { kilobytes { used total } } disks { temp } } }",
        diag, "grappe")
    marche = _unraid_graphql(base, cle, "{ info { time uptime } }", diag, "uptime")
    conteneurs = _unraid_graphql(
        base, cle, "{ docker { containers { state } } }", diag, "docker")
    if charge is None and grappe is None and marche is None and conteneurs is None:
        return None
    d = {}
    for bloc in (charge, grappe, marche, conteneurs):
        if isinstance(bloc, dict):
            d.update(bloc)
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


# --- supervision de machine --------------------------------------------------

# La version de l API se decouvre une fois par hote : Glances 4 et Glances 3
# n exposent pas les memes chemins, et refaire la decouverte a chaque cycle
# doublerait les appels pour une reponse qui ne change pas.
_GLANCES = {}

# Etiquettes de seuils, a ne pas confondre avec une mesure.
_SEUIL = re.compile(r"crit|alarm|alert|max|min|high|low|limit|warn|target|trip", re.I)


def _glances_auth(mot):
    """L en-tete d authentification, ecrit une seule fois — voir services.basic."""
    return services.basic(mot)


def _glances_lire(base, version, greffon, entetes):
    code, corps, _ = _http("%s/api/%s/%s" % (base, version, greffon), entetes)
    if code != 200:
        return None
    return _json(corps)


def _glances_version(base, entetes):
    """4 ou 3, selon ce que l hote repond — retenu pour les cycles suivants."""
    connue = _GLANCES.get(base)
    if connue:
        return connue
    for v in ("4", "3"):
        if _glances_lire(base, v, "cpu", entetes) is not None:
            _GLANCES[base] = v
            return v
    return None


def _glances_secondes(texte_duree):
    """« 6 days, 22:53:29 » en secondes ; Glances ne donne que cette forme."""
    m = re.search(r"(?:(\d+)\s*day[s]?,\s*)?(\d+):(\d+):(\d+)", str(texte_duree or ""))
    if not m:
        return None
    jours = int(m.group(1) or 0)
    return ((jours * 24 + int(m.group(2))) * 60 + int(m.group(3))) * 60 + int(m.group(4))


def w_glances(base, mot_de_passe=""):
    """Charge de la machine, lue par l API REST de Glances.

    Un greffon par mesure plutot qu un « /all » : la reponse complete embarque
    la liste des processus, plusieurs centaines de kilo-octets dont on ne lit
    rien. Chaque greffon absent — pas de sonde thermique, pas de Docker — laisse
    simplement sa mesure de cote.
    """
    entetes = _glances_auth(mot_de_passe)
    version = _glances_version(base, entetes)
    if not version:
        return None
    def lire(greffon):
        return _glances_lire(base, version, greffon, entetes)

    stats = []

    cpu = lire("cpu")
    if isinstance(cpu, dict) and cpu.get("total") is not None:
        stats.append(M("cpu", float(cpu["total"])))

    mem = lire("mem")
    if isinstance(mem, dict) and mem.get("percent") is not None:
        stats.append(M("ram", float(mem["percent"])))

    # Plusieurs points de montage : celui qui se remplit decide, c est lui qui
    # declenchera l alerte.
    fs = lire("fs")
    if isinstance(fs, dict):
        fs = fs.get("fs") or []
    plein = [(float(d["percent"]), d.get("mnt_point") or "")
             for d in (fs or []) if isinstance(d, dict) and d.get("percent") is not None]
    if plein:
        pire = max(plein)
        stats.append(M("disque", pire[0]))

    # Meme regle pour les sondes thermiques, en ecartant les valeurs qui ne
    # sont pas des temperatures (ventilateurs, batteries).
    sondes = lire("sensors")
    if isinstance(sondes, dict):
        sondes = sondes.get("sensors") or []
    degres = []
    for d in (sondes or []):
        if not isinstance(d, dict):
            continue
        unite = str(d.get("unit") or "")
        if unite not in ("C", "°C"):
            continue
        # Les cartes annoncent aussi leurs seuils : « Composite Critical » a
        # beau etre en degres, 108 °C est la limite du disque, pas sa
        # temperature. Un seuil pris pour une mesure declenche une alerte qui
        # ne retombera jamais.
        etiquette = str(d.get("label") or "").lower()
        if _SEUIL.search(etiquette):
            continue
        genre = str(d.get("type") or "")
        if genre and "temp" not in genre.lower():
            continue
        try:
            v = float(d.get("value"))
        except (TypeError, ValueError):
            continue
        if 0 < v < 120:
            degres.append(v)
    if degres:
        stats.append(M("temp", max(degres)))

    marche = lire("uptime")
    if isinstance(marche, dict):
        marche = marche.get("uptime")
    secondes = _glances_secondes(marche)
    if secondes:
        jours = secondes // 86400
        stats.append(M("uptime", secondes,
                       ("%d j" % jours) if jours else ("%d h" % (secondes // 3600))))

    # Glances 4 nomme le greffon « containers », Glances 3 « docker ».
    boites = lire("containers" if version == "4" else "docker")
    if isinstance(boites, dict):
        boites = boites.get("containers") or []
    if isinstance(boites, list) and boites:
        actifs = sum(1 for c in boites if isinstance(c, dict)
                     and str(c.get("status") or c.get("Status") or "").lower().startswith("running"))
        stats.append(M("docker", actifs, "%d / %d" % (actifs, len(boites))))

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
    "glances": w_glances,
    "seerr": w_seerr,
    "jellyseerr": w_seerr,
    "overseerr": w_seerr,
    "jackett": w_jackett,
    "frigate": w_frigate,
    "netdata": w_netdata,
    "syncthing": w_syncthing,
    "proxmox": w_proxmox,
    "kodi": w_kodi,
    "navidrome": w_navidrome,
    "audiobookshelf": w_audiobookshelf,
    "mylar": w_mylar,
    "kapowarr": w_kapowarr,
    "truenas": w_truenas,
    "omv": w_omv,
    "casaos": w_casaos,
    "cosmos": w_cosmos,
    "openhab": w_openhab,
    "domoticz": w_domoticz,
    "iobroker": w_iobroker,
    "npm": w_npm,
    "wgeasy": w_wgeasy,
    "filebrowser": w_filebrowser,
    "grafana": w_grafana,
    "gitea": w_gitea,
    "vaultwarden": w_vaultwarden,
    "authentik": w_authentik,
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
    "glances": ["cpu", "ram", "disque", "temp", "uptime", "docker"],
    "seerr": ["en_attente", "approuvees", "disponibles"],
    "jellyseerr": ["en_attente", "approuvees", "disponibles"],
    "overseerr": ["en_attente", "approuvees", "disponibles"],
    "jackett": ["indexeurs", "erreurs"],
    "frigate": ["cameras", "detections", "uptime"],
    "netdata": ["cpu", "ram", "alarmes"],
    "syncthing": ["dossiers", "appareils", "uptime"],
    "proxmox": ["cpu", "ram", "uptime", "machines"],
    "kodi": ["lectures", "films", "series"],
    "navidrome": ["titres"],
    "audiobookshelf": ["bibliotheques", "livres"],
    "mylar": ["series"],
    "kapowarr": ["series"],
    "truenas": ["grappes", "disque", "uptime"],
    "omv": ["grappes", "disque"],
    "casaos": ["cpu", "ram"],
    "cosmos": ["hotes", "arretes"],
    "openhab": ["objets", "equipements"],
    "domoticz": ["appareils", "actifs"],
    "iobroker": ["actifs"],
    "npm": ["hotes", "arretes"],
    "wgeasy": ["clients", "actifs"],
    "filebrowser": ["fichiers"],
    "grafana": ["tableaux", "alarmes"],
    "gitea": ["depots"],
    "vaultwarden": ["utilisateurs"],
    "authentik": ["utilisateurs", "evenements"],
}


def capacites(type_service):
    """Ce qu une integration sait lire, en clair pour l interface."""
    return [{"id": i, "lab": libelle(i)} for i in CAPACITES.get(type_service, [])]


def _demande_une_cle(fn):
    """Ce lecteur se sert-il d une cle ?

    La reponse se lit dans son code plutot que dans une table tenue a la main :
    une liste ecrite a cote finirait par mentir le jour ou un lecteur change.
    On ecarte la ligne de signature, qui nomme « cle » dans tous les cas.
    """
    try:
        corps = inspect.getsource(fn).split("\n", 1)[1]
    except (OSError, TypeError, IndexError):
        return False
    return bool(re.search(r"\bcle\b", corps))


def profils():
    """Pour chaque type : ce qu il sait lire, et s il lui faut une cle.

    L interface s en sert pour expliquer une tuile sans chiffre : rien a
    configurer quand aucune integration n existe, une cle a fournir quand elle
    existe et qu il en faut une, une panne a chercher sinon.
    """
    sortie = {}
    for type_service, ids in CAPACITES.items():
        fn = REGISTRE.get(type_service)
        sortie[type_service] = {
            "donnees": [{"id": i, "lab": libelle(i)} for i in ids],
            "cle": bool(fn) and _demande_une_cle(fn),
        }
    return sortie


def mesurer(type_service, url, cle, diag=None):
    """Les mesures d un service, et pourquoi elles manquent quand elles manquent.

    « diag » est rempli par les lecteurs qui savent nommer un refus : sans lui,
    une tuile vide ne dit que « indisponible », ce qui n aide personne a
    comprendre si c est la cle, la portee de la cle ou le service.
    """
    fn = REGISTRE.get(type_service)
    if not fn:
        return None
    base = (url or "").strip().rstrip("/")
    if not base:
        return None
    try:
        # Seuls certains lecteurs savent detailler : les autres gardent leur
        # signature a deux arguments.
        import inspect as _i
        if diag is not None and "diag" in _i.signature(fn).parameters:
            stats = fn(base, cle or "", diag)
        else:
            stats = fn(base, cle or "")
    except Exception:
        return None
    # Plus de plafond ici : on remonte tout ce que le service donne, la fiche
    # de l application decide de ce qui apparait sur la tuile.
    return stats[:8] if stats else None
