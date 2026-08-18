"""Les dix-neuf integrations branchees en une fois, chacune face a son serveur.

Aucune de ces applications ne tourne ici : les reponses imitent la forme
documentee de chaque API — en-tete ou adresse pour la cle, jeton echange contre
un couple « utilisateur:motdepasse » pour les autres. Ce test prouve que le
lecteur lit ce que l API rend, pas que la documentation dit vrai : le jour ou
une reponse reelle dementira l imitation, c est ici qu il faudra corriger.

Il verifie aussi la regle qui vaut pour toutes : sans identifiants valables, on
ne rend aucun chiffre plutot qu un zero.

Sortie : 0 si les dix-neuf lisent, 1 sinon.
"""
import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import widgets  # noqa: E402

CLE = "cle-de-test"
COUPLE = "guillaume:motdepasse"
ECHECS = []


def verifier(titre, obtenu, attendu):
    ok = obtenu == attendu
    print("    %-34s %-26s %s" % (titre, obtenu, "" if ok else "!!! attendu %s" % (attendu,)))
    if not ok:
        ECHECS.append(titre)


def servir(router):
    """Un serveur dont le routeur recoit chemin, en-tetes, methode et corps."""
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _rep(self):
            taille = int(self.headers.get("Content-Length") or 0)
            corps = self.rfile.read(taille) if taille else b""
            r = router(self.path, self.headers, self.command, corps)
            if r is None:
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code, contenu, entetes = r if isinstance(r, tuple) else (200, r, {})
            brut = contenu if isinstance(contenu, bytes) else json.dumps(contenu).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(brut)))
            for k, v in (entetes or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(brut)

        do_GET = _rep
        do_POST = _rep

    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.server_address[1]


def val(stats):
    return {m["id"]: m["val"] for m in (stats or [])}


def porteur(entetes, valeur):
    return entetes.get("Authorization") == valeur


# --- un routeur par service --------------------------------------------------

def kodi(chemin, entetes, methode, corps):
    if chemin != "/jsonrpc" or methode != "POST":
        return None
    d = json.loads(corps or b"{}")
    m = d.get("method")
    if m == "Player.GetActivePlayers":
        return {"result": [{"playerid": 1, "type": "video"}]}
    if m == "VideoLibrary.GetMovies":
        return {"result": {"limits": {"total": 412}}}
    if m == "VideoLibrary.GetTVShows":
        return {"result": {"limits": {"total": 37}}}
    return {"result": {}}


def navidrome(chemin, entetes, methode, corps):
    if not chemin.startswith("/rest/getScanStatus"):
        return None
    q = urllib.parse.parse_qs(chemin.split("?", 1)[1])
    if q.get("u", [""])[0] != "guillaume" or q.get("p", [""])[0] != "motdepasse":
        return {"subsonic-response": {"status": "failed"}}
    return {"subsonic-response": {"status": "ok", "scanStatus": {"scanning": False,
                                                                 "count": 18452}}}


def audiobookshelf(chemin, entetes, methode, corps):
    if not porteur(entetes, "Bearer " + CLE):
        return None
    if chemin == "/api/libraries":
        return {"libraries": [{"id": "l1", "name": "Romans"}, {"id": "l2", "name": "Podcasts"}]}
    if chemin.startswith("/api/libraries/l1/items"):
        return {"total": 240, "results": []}
    if chemin.startswith("/api/libraries/l2/items"):
        return {"total": 60, "results": []}
    return None


def mylar(chemin, entetes, methode, corps):
    if "apikey=" + CLE not in chemin:
        return {"data": None}
    return {"success": True, "data": [{"ComicName": "A"}, {"ComicName": "B"}]}


def kapowarr(chemin, entetes, methode, corps):
    if "api_key=" + CLE not in chemin:
        return None
    return {"error": None, "result": [{"id": 1}, {"id": 2}, {"id": 3}]}


def truenas(chemin, entetes, methode, corps):
    if not porteur(entetes, "Bearer " + CLE):
        return None
    if chemin == "/api/v2.0/pool":
        return [{"name": "tank", "size": 20000000000000, "free": 4000000000000},
                {"name": "ssd", "size": 1000000000000, "free": 800000000000}]
    if chemin == "/api/v2.0/system/info":
        return {"version": "TrueNAS-SCALE-24.04", "uptime_seconds": 604800}
    return None


def openhab(chemin, entetes, methode, corps):
    if chemin.startswith("/rest/items"):
        return [{"name": "x%d" % i} for i in range(146)]
    if chemin.startswith("/rest/things"):
        return [{"UID": "t%d" % i} for i in range(23)]
    return None


def domoticz(chemin, entetes, methode, corps):
    if not chemin.startswith("/json.htm"):
        return None
    return {"status": "OK", "result": [
        {"idx": "1", "Status": "On"}, {"idx": "2", "Status": "Off"},
        {"idx": "3", "Status": "On"}, {"idx": "4", "Status": "Closed"}]}


def iobroker(chemin, entetes, methode, corps):
    if not chemin.startswith("/objects"):
        return None
    return {"system.adapter.admin.0": {}, "system.adapter.zigbee.0": {},
            "system.adapter.web.0": {}}


def grafana(chemin, entetes, methode, corps):
    if not porteur(entetes, "Bearer " + CLE):
        return None
    if chemin.startswith("/api/search"):
        return [{"uid": "d%d" % i, "type": "dash-db"} for i in range(12)]
    if "alertmanager" in chemin:
        return [{"labels": {"alertname": "disque"}}]
    return None


def gitea(chemin, entetes, methode, corps):
    if not porteur(entetes, "token " + CLE):
        return None
    if chemin.startswith("/api/v1/repos/search"):
        return 200, {"ok": True, "data": [{"id": 1}]}, {"X-Total-Count": "34"}
    return None


def authentik(chemin, entetes, methode, corps):
    if not porteur(entetes, "Bearer " + CLE):
        return None
    if chemin.startswith("/api/v3/core/users/"):
        return {"pagination": {"count": 9}, "results": []}
    if chemin.startswith("/api/v3/events/events/"):
        return {"pagination": {"count": 1204}, "results": []}
    return None


def npm(chemin, entetes, methode, corps):
    if chemin == "/api/tokens" and methode == "POST":
        d = json.loads(corps or b"{}")
        if d.get("identity") == "guillaume" and d.get("secret") == "motdepasse":
            return {"token": "jwt-npm", "expires": "2030-01-01"}
        return None
    if chemin == "/api/nginx/proxy-hosts":
        if not porteur(entetes, "Bearer jwt-npm"):
            return None
        return [{"id": 1, "enabled": True}, {"id": 2, "enabled": True},
                {"id": 3, "enabled": False}]
    return None


def wgeasy(chemin, entetes, methode, corps):
    if chemin == "/api/session" and methode == "POST":
        d = json.loads(corps or b"{}")
        if d.get("password") != CLE:
            return None
        return 200, {"success": True}, {"Set-Cookie": "connect.sid=abc; Path=/"}
    if chemin == "/api/wireguard/client":
        if "connect.sid=abc" not in (entetes.get("Cookie") or ""):
            return None
        return [{"name": "portable", "latestHandshakeAt": "2026-08-18T07:00:00Z"},
                {"name": "telephone", "latestHandshakeAt": None},
                {"name": "tablette", "latestHandshakeAt": "2026-08-18T06:40:00Z"}]
    return None


def filebrowser(chemin, entetes, methode, corps):
    if chemin == "/api/login" and methode == "POST":
        d = json.loads(corps or b"{}")
        if d.get("username") == "guillaume" and d.get("password") == "motdepasse":
            return 200, b"jeton.filebrowser.abc", {}
        return None
    if chemin.startswith("/api/resources"):
        if entetes.get("X-Auth") != "jeton.filebrowser.abc":
            return None
        return {"items": [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]}
    return None


def omv(chemin, entetes, methode, corps):
    if chemin != "/rpc.php" or methode != "POST":
        return None
    d = json.loads(corps or b"{}")
    if d.get("service") == "Session" and d.get("method") == "login":
        p = d.get("params") or {}
        if p.get("username") == "guillaume" and p.get("password") == "motdepasse":
            return 200, {"response": {"authenticated": True}, "error": None}, \
                   {"Set-Cookie": "X-OPENMEDIAVAULT-SESSIONID=xyz; Path=/"}
        return {"response": {"authenticated": False}, "error": None}
    if d.get("method") == "enumerateMountedFilesystems":
        if "X-OPENMEDIAVAULT-SESSIONID=xyz" not in (entetes.get("Cookie") or ""):
            return None
        return {"response": [{"devicename": "sda1", "percentage": 41},
                             {"devicename": "sdb1", "percentage": 88}], "error": None}
    return None


def casaos(chemin, entetes, methode, corps):
    if chemin == "/v1/sys/hardware/usage":
        return {"success": 200, "data": {"cpu": {"percent": 7.5},
                                         "mem": {"usedPercent": 43.2}}}
    return None


def cosmos(chemin, entetes, methode, corps):
    if chemin != "/cosmos/api/config":
        return None
    return {"data": {"HTTPConfig": {"ProxyConfig": {"Routes": [
        {"Name": "plex"}, {"Name": "vieux", "Disabled": True}, {"Name": "photos"}]}}}}


def vaultwarden(chemin, entetes, methode, corps):
    if chemin == "/admin" and methode == "POST":
        if b"token=" + CLE.encode() not in (corps or b""):
            return None
        return 200, {"ok": True}, {"Set-Cookie": "VW_ADMIN=abc; Path=/"}
    if chemin == "/admin/users":
        if "VW_ADMIN=abc" not in (entetes.get("Cookie") or ""):
            return None
        return [{"email": "a@x", "userEnabled": True},
                {"email": "b@x", "userEnabled": True},
                {"email": "c@x", "userEnabled": False}]
    return None


CAS = [
    ("kodi", kodi, "", {"lectures": "1", "films": "412", "series": "37"}),
    ("navidrome", navidrome, COUPLE, {"titres": "18 452"}),
    ("audiobookshelf", audiobookshelf, CLE, {"bibliotheques": "2", "livres": "300"}),
    ("mylar", mylar, CLE, {"series": "2"}),
    ("kapowarr", kapowarr, CLE, {"series": "3"}),
    ("truenas", truenas, CLE, {"grappes": "2", "disque": "80 %", "uptime": "7 j"}),
    ("openhab", openhab, "", {"objets": "146", "equipements": "23"}),
    ("domoticz", domoticz, "", {"appareils": "4", "actifs": "2"}),
    ("iobroker", iobroker, "", {"actifs": "3"}),
    ("grafana", grafana, CLE, {"tableaux": "12", "alarmes": "1"}),
    ("gitea", gitea, CLE, {"depots": "34"}),
    ("authentik", authentik, CLE, {"utilisateurs": "9", "evenements": "1 204"}),
    ("npm", npm, COUPLE, {"hotes": "3", "arretes": "1"}),
    ("wgeasy", wgeasy, CLE, {"clients": "3", "actifs": "2"}),
    ("filebrowser", filebrowser, COUPLE, {"fichiers": "4"}),
    ("omv", omv, COUPLE, {"grappes": "2", "disque": "88 %"}),
    ("casaos", casaos, "", {"cpu": "8 %", "ram": "43 %"}),
    ("cosmos", cosmos, "", {"hotes": "3", "arretes": "1"}),
    ("vaultwarden", vaultwarden, CLE, {"utilisateurs": "3", "actifs": "2"}),
]


def main():
    print("1. chacun lit ce que son API rend")
    for nom, router, cle, attendu in CAS:
        s, u = servir(router)
        try:
            mes = val(widgets.mesurer(nom, u, cle))
            manquantes = {k: v for k, v in attendu.items() if mes.get(k) != v}
            print("    %-15s %s" % (nom, " · ".join(
                "%s %s" % (k, mes.get(k)) for k in attendu) or "(rien)"))
            if manquantes:
                ECHECS.append("%s : %s" % (nom, manquantes))
                print("        !!! attendu %s" % attendu)
        finally:
            s.shutdown()

    print("2. sans identifiants valables, aucun chiffre invente")
    for nom, router, cle, _ in CAS:
        if not cle:
            continue          # ces services-la ne demandent rien
        s, u = servir(router)
        try:
            mes = widgets.mesurer(nom, u, "mauvaise-cle")
            if mes:
                ECHECS.append("%s rend des chiffres avec une mauvaise cle" % nom)
                print("    %-15s !!! %s" % (nom, val(mes)))
        finally:
            s.shutdown()
    print("    %d services a identifiants, aucun ne cede" % sum(1 for c in CAS if c[2]))

    print()
    if ECHECS:
        print("RESUME : %d lecteur(s) a corriger" % len(ECHECS))
        for e in ECHECS:
            print("  - %s" % (e,))
        return 1
    print("RESUME : les dix-neuf lisent, et se taisent sans identifiants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
