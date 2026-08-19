# -*- coding: utf-8 -*-
"""Les telechargeurs, les suites *arr, et les sondes qui mentent.

Trois services etaient reconnus a la detection sans que rien ne sache les
lire : Deluge, Transmission et NZBGet. La fiche demandait un mot de passe, la
tuile disait « aucune metrique », et l utilisateur n avait aucun moyen de
comprendre pourquoi. Ce test monte les trois, avec leurs travers : Deluge
s identifie par un cookie, Transmission refuse le premier appel pour donner son
jeton de session, NZBGet parle un JSON-RPC en Basic.

Deux mesures fausses sont verifiees ici aussi : Lidarr et Readarr, restes a
l API « v1 » quand Sonarr et Radarr sont en « v3 » ; et les sondes thermiques
d une puce Super I/O, dont les entrees non connectees annoncent 113 degres en
permanence.

Sortie : 0 si tout se lit comme il faut, 1 sinon.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app'))
import widgets   # noqa: E402

ECHECS = []


def verifier(titre, obtenu, attendu):
    ok = obtenu == attendu
    print("    %-42s %-28s %s" % (titre, obtenu, "" if ok else "!!! attendu %s" % (attendu,)))
    if not ok:
        ECHECS.append(titre)


def servir(routeur):
    """Un serveur qui repond ce que « routeur » decide, en GET comme en POST."""
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _repondre(self, corps):
            code, contenu, entetes = corps
            brut = contenu if isinstance(contenu, bytes) else json.dumps(contenu).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(brut)))
            for k, v in (entetes or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(brut)

        def do_GET(self):
            self._repondre(routeur(self.path, self.headers, None))

        def do_POST(self):
            taille = int(self.headers.get("Content-Length") or 0)
            brut = self.rfile.read(taille)
            try:
                envoye = json.loads(brut or b"{}")
            except ValueError:
                envoye = {}
            self._repondre(routeur(self.path, self.headers, envoye))

    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.server_address[1]


# --- Deluge ------------------------------------------------------------------

def deluge(mdp_attendu="secret", relie=True):
    etat = {"identifie": False}

    def routeur(chemin, entetes, envoye):
        if chemin != "/json":
            return 404, b"", None
        methode = (envoye or {}).get("method")
        if methode == "auth.login":
            bon = ((envoye.get("params") or [""])[0] == mdp_attendu)
            etat["identifie"] = bon
            return 200, {"result": bon, "error": None, "id": 1}, \
                ({"Set-Cookie": "_session_id=abc123; Path=/; HttpOnly"} if bon else None)
        if methode == "web.update_ui":
            if "_session_id=abc123" not in (entetes.get("Cookie") or ""):
                return 200, {"result": None, "error": {"message": "Not authenticated"}}, None
            return 200, {"result": {
                "connected": relie,
                "stats": {"download_rate": 1536000.0, "upload_rate": 262144.0,
                          "num_connections": 84},
                "filters": {"state": [["All", 23], ["Downloading", 3], ["Seeding", 18],
                                      ["Paused", 2]]},
                "torrents": {},
            }, "error": None}, None
        return 200, {"result": None}, None
    return routeur


# --- Transmission ------------------------------------------------------------

def transmission(identifiants=None):
    def routeur(chemin, entetes, envoye):
        if chemin != "/transmission/rpc":
            return 404, b"", None
        if identifiants and not (entetes.get("Authorization") or "").startswith("Basic "):
            return 401, {"erreur": "auth"}, None
        # Le protocole veut ce refus : le jeton voyage dans l en-tete du 409.
        if entetes.get("X-Transmission-Session-Id") != "jeton-de-session":
            return 409, b"<h1>409: Conflict</h1>", {"X-Transmission-Session-Id": "jeton-de-session"}
        return 200, {"result": "success", "arguments": {
            "activeTorrentCount": 4, "torrentCount": 31, "pausedTorrentCount": 2,
            "downloadSpeed": 4718592, "uploadSpeed": 131072,
        }}, None
    return routeur


# --- NZBGet ------------------------------------------------------------------

def nzbget(chemin, entetes, envoye):
    if chemin != "/jsonrpc":
        return 404, b"", None
    if not (entetes.get("Authorization") or "").startswith("Basic "):
        return 401, {"erreur": "auth"}, None
    methode = (envoye or {}).get("method")
    if methode == "status":
        return 200, {"result": {"DownloadRate": 8912896, "RemainingSizeMB": 4210,
                                "DownloadPaused": False, "ArticleCacheMB": 12}}, None
    if methode == "listgroups":
        return 200, {"result": [{"NZBName": "a", "ActiveDownloads": 1},
                                {"NZBName": "b", "ActiveDownloads": 0},
                                {"NZBName": "c", "ActiveDownloads": 0}]}, None
    return 200, {"result": None}, None


# --- une suite *arr qui ne connait qu une version d API -----------------------

def arr(version):
    def routeur(chemin, entetes, envoye):
        if not chemin.startswith("/api/%s/" % version):
            return 404, {"erreur": "version"}, None
        if entetes.get("X-Api-Key") != "cle-arr":
            return 401, {"erreur": "cle"}, None
        if "wanted/missing" in chemin:
            return 200, {"totalRecords": 12}, None
        if "queue" in chemin:
            return 200, {"totalRecords": 2}, None
        return 200, [{"id": 1}, {"id": 2}, {"id": 3}], None
    return routeur


def rendu(stats):
    return {m["id"]: m["val"] for m in (stats or [])}


def main():
    print("\n== Deluge ==")
    srv, url = servir(deluge())
    try:
        diag = {}
        verifier("sans mot de passe, rien n est invente",
                 widgets.w_deluge(url, "", diag), None)
        verifier("et le refus est nomme", diag.get("refus"),
                 ["mot de passe de la WebUI requis"])
        diag = {}
        widgets.w_deluge(url, "faux", diag)
        verifier("un mauvais mot de passe se dit aussi", diag.get("refus"),
                 ["mot de passe de la WebUI refuse"])
        r = rendu(widgets.w_deluge(url, "secret"))
        print("    " + " · ".join("%s %s" % (k, v) for k, v in r.items()))
        verifier("torrents en cours", r.get("actifs"), "3")
        verifier("torrents au total", r.get("file"), "23")
        verifier("reception", r.get("reception"), "1.5 Mo/s")
        verifier("envoi", r.get("envoi"), "256.0 Ko/s")
    finally:
        srv.shutdown()

    print("\n== Deluge, WebUI non reliee au demon ==")
    srv, url = servir(deluge(relie=False))
    try:
        diag = {}
        verifier("aucune mesure", widgets.w_deluge(url, "secret", diag), None)
        verifier("la cause est dite", diag.get("refus"), ["WebUI non reliee au demon"])
    finally:
        srv.shutdown()

    print("\n== Transmission ==")
    srv, url = servir(transmission())
    try:
        r = rendu(widgets.w_transmission(url, ""))
        print("    " + " · ".join("%s %s" % (k, v) for k, v in r.items()))
        verifier("le jeton du 409 est repris", r.get("actifs"), "4")
        verifier("torrents au total", r.get("file"), "31")
        verifier("reception", r.get("reception"), "4.5 Mo/s")
    finally:
        srv.shutdown()

    print("\n== Transmission protege par un compte ==")
    srv, url = servir(transmission(identifiants=True))
    try:
        diag = {}
        verifier("sans compte, rien", widgets.w_transmission(url, "", diag), None)
        verifier("le refus est nomme", diag.get("refus"), ["identifiants refuses"])
        verifier("avec le couple, la lecture passe",
                 bool(widgets.w_transmission(url, "moi:secret")), True)
    finally:
        srv.shutdown()

    print("\n== NZBGet ==")
    srv, url = servir(nzbget)
    try:
        diag = {}
        verifier("sans compte, rien", widgets.w_nzbget(url, "", diag), None)
        r = rendu(widgets.w_nzbget(url, "nzbget:secret"))
        print("    " + " · ".join("%s %s" % (k, v) for k, v in r.items()))
        verifier("reception", r.get("reception"), "8.5 Mo/s")
        verifier("file d attente", r.get("file"), "3")
        verifier("telechargements actifs", r.get("actifs"), "1")
    finally:
        srv.shutdown()

    print("\n== Lidarr et Readarr parlent « v1 », pas « v3 » ==")
    srv, url = servir(arr("v1"))
    try:
        r = rendu(widgets.REGISTRE["lidarr"](url, "cle-arr"))
        verifier("Lidarr est lu", r.get("manque"), "12")
        verifier("et ses artistes comptes", r.get("artistes"), "3")
        r = rendu(widgets.REGISTRE["readarr"](url, "cle-arr"))
        verifier("Readarr aussi", r.get("manque"), "12")
        verifier("Sonarr, lui, ne s y trompe pas",
                 widgets.REGISTRE["sonarr"](url, "cle-arr"), None)
    finally:
        srv.shutdown()

    print("\n== Sonarr et Radarr restent en « v3 » ==")
    srv, url = servir(arr("v3"))
    try:
        r = rendu(widgets.REGISTRE["sonarr"](url, "cle-arr"))
        verifier("Sonarr est lu", r.get("manque"), "12")
        verifier("Lidarr ne lit pas du v3", widgets.REGISTRE["lidarr"](url, "cle-arr"), None)
    finally:
        srv.shutdown()

    print("\n== les sondes d une puce Super I/O ==")
    # Releve reel d une carte a puce nct6798 : les entrees non connectees
    # annoncent 110 a 113 degres en permanence.
    sondes = [("AUXTIN0", 110), ("AUXTIN1", 111), ("AUXTIN2", 112), ("AUXTIN3", -1),
              ("CPUTIN", 45), ("Composite", 46), ("Core 0", 29), ("Core 1", 30),
              ("PCH_CHIP_TEMP", 0), ("Package id 0", 33), ("SYSTIN", 113),
              ("Sensor 1", 45)]
    plausibles = [(l.lower(), v) for l, v in sondes if 0 < v < widgets.PLAFOND_SONDE]
    connues = [v for l, v in plausibles if widgets._SONDE_UTILE.search(l)]
    verifier("la temperature retenue", max(connues) if connues else None, 46)
    verifier("les entrees en l air sont ecartees",
             [l for l, v in sondes if v >= widgets.PLAFOND_SONDE],
             ["AUXTIN0", "AUXTIN1", "AUXTIN2", "SYSTIN"])
    verifier("une sonde inconnue reste un dernier recours",
             bool(widgets._SONDE_UTILE.search("sensor 1")), False)

    print()
    if ECHECS:
        print("RESUME : %d point(s) a corriger" % len(ECHECS))
        for e in ECHECS:
            print("  - " + e)
        return 1
    print("RESUME : les telechargeurs se lisent, et les sondes ne mentent plus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
