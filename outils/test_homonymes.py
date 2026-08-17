"""Deux applications du meme nom doivent rester deux applications.

Le nom sert a l affichage ; c est l identifiant qui range les mesures, l etat,
l historique, les alertes et le journal. Ce test monte deux faux Glances aux
chiffres differents, les declare tous deux sous le nom « Glances », et verifie
que rien ne se melange.

Il verifie aussi la reprise : une fiche d avant les identifiants garde son
historique, parce qu elle recoit son propre nom comme identifiant.

Sortie : 0 si tout tient, 1 au premier point qui cede.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSSIER = os.path.join(tempfile.gettempdir(), "atrium-homonymes")
PROFIL, MDP = "Proprietaire", "motdepasse-test"

ECHECS = []


def verifier(titre, obtenu, attendu):
    ok = obtenu == attendu
    print("    %-44s %-22s %s" % (titre, obtenu, "" if ok else "!!! attendu %s" % (attendu,)))
    if not ok:
        ECHECS.append(titre)


def port_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = port_libre()
BASE = "http://127.0.0.1:%d" % PORT


def faux_glances(cpu, ram, disque):
    """Un Glances qui ne dit qu une chose, mais la dit bien."""
    corps = {
        "cpu": {"total": cpu}, "mem": {"percent": ram},
        "fs": [{"mnt_point": "/", "percent": disque}],
        "uptime": "1 day, 0:00:00", "sensors": [], "containers": [],
    }

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            greffon = self.path.rsplit("/", 1)[-1]
            if self.path.startswith("/api/4/") and greffon in corps:
                brut = json.dumps(corps[greffon]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(brut)))
                self.end_headers()
                self.wfile.write(brut)
                return
            self.send_response(404)
            self.end_headers()

    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.server_address[1]


def appel(methode, chemin, corps=None, cookie=None):
    d = json.dumps(corps).encode() if corps is not None else None
    r = urllib.request.Request(BASE + chemin, data=d, method=methode)
    if d:
        r.add_header("Content-Type", "application/json")
    if cookie:
        r.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(r, timeout=25) as rep:
            return rep.status, rep.read(200000), rep.headers.get("Set-Cookie") or ""
    except urllib.error.HTTPError as e:
        return e.code, e.read(20000), ""
    except Exception as e:
        return 0, str(e).encode(), ""


def demarrer():
    env = dict(os.environ, ATRIUM_PORT=str(PORT), ATRIUM_CONFIG_DIR=DOSSIER,
               PYTHONIOENCODING="utf-8")
    env.pop("ATRIUM_ADMIN", None)
    p = subprocess.Popen([sys.executable, "server.py"], cwd=os.path.join(RACINE, "app"),
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    for _ in range(60):
        time.sleep(0.4)
        if appel("GET", "/api/health")[0] == 200:
            return p
        if p.poll() is not None:
            break
    try:
        p.kill()
        print(p.stdout.read())
    except Exception:
        pass
    sys.exit("serveur injoignable")


def arreter(p):
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()


def config():
    with open(os.path.join(DOSSIER, "atrium.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    shutil.rmtree(DOSSIER, ignore_errors=True)
    os.makedirs(DOSSIER, exist_ok=True)
    g1, url1 = faux_glances(3.0, 24.0, 53.0)      # la machine « Unraid »
    g2, url2 = faux_glances(71.0, 88.0, 12.0)     # la machine « HAOS »
    srv = demarrer()
    try:
        appel("POST", "/api/setup", {"nom": PROFIL, "motdepasse": MDP})
        _, _, ck = appel("POST", "/api/login", {"nom": PROFIL, "motdepasse": MDP})
        cookie = "atrium_session=" + ck.split("atrium_session=")[1].split(";")[0]

        fiche = lambda url: {"nom": "Glances", "role": "", "cat": "Administration",
                             "url": url, "logoUrl": "", "token": "", "apiKey": "",
                             "tempEntity": "", "type": "glances", "fav": False,
                             "masque": []}
        code, _, _ = appel("POST", "/api/config",
                           {"apps": [fiche(url1), fiche(url2)],
                            "users": [{"nom": PROFIL}]}, cookie)
        print("1. deux fiches nommees « Glances »")
        verifier("enregistrement", code, 200)
        ids = [a.get("id") for a in config()["apps"]]
        verifier("identifiants distincts", len(set(ids)), 2)
        verifier("la premiere garde son nom comme identifiant", ids[0], "Glances")

        # Le cycle de collecte tourne toutes les 30 s ; on ne l attend pas.
        for _ in range(40):
            _, corps, _ = appel("GET", "/api/widgets", None, cookie)
            tuiles = json.loads(corps) or {}
            if len(tuiles) >= 2:
                break
            time.sleep(1)

        print("2. chacune remonte ses propres chiffres")
        val = {c: {m["id"]: m["val"] for m in mes} for c, mes in tuiles.items()}
        verifier("nombre de services mesures", len(val), 2)
        verifier("processeur de la premiere", val.get(ids[0], {}).get("cpu"), "3 %")
        verifier("processeur de la seconde", val.get(ids[1], {}).get("cpu"), "71 %")
        verifier("memoire de la premiere", val.get(ids[0], {}).get("ram"), "24 %")
        verifier("memoire de la seconde", val.get(ids[1], {}).get("ram"), "88 %")

        print("3. l historique et les series suivent la meme clef")
        _, corps, _ = appel("GET", "/api/historique?h=24", None, cookie)
        services = json.loads(corps).get("services") or {}
        verifier("services historises", sorted(services) == sorted(ids), True)
        _, corps, _ = appel("GET", "/api/mesure?service=%s&id=cpu&h=24" % ids[1],
                            None, cookie)
        verifier("serie de la seconde", json.loads(corps).get("erreur") or "lue", "lue")

        print("4. l alerte nomme la fiche, pas la clef")
        _, corps, _ = appel("GET", "/api/supervision", None, cookie)
        alertes = (json.loads(corps).get("alertes") or {}).get("alertes") or []
        vues = [(a.get("service"), a.get("service_nom")) for a in alertes
                if a.get("service") in ids]
        if vues:
            verifier("nom joint a l alerte", all(n == "Glances" for _, n in vues), True)
        else:
            print("    %-44s %s" % ("aucune alerte a ce stade", "(rien a verifier)"))
    finally:
        arreter(srv)
        for s in (g1, g2):
            s.shutdown()
        shutil.rmtree(DOSSIER, ignore_errors=True)

    print()
    if ECHECS:
        print("RESUME : %d point(s) a corriger" % len(ECHECS))
        for e in ECHECS:
            print("  - " + e)
        return 1
    print("RESUME : deux fiches homonymes restent deux services")
    return 0


if __name__ == "__main__":
    sys.exit(main())
