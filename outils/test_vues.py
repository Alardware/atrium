"""La vue d un profil n appartient qu a lui.

Les services sont declares une fois pour la maison : une adresse, une cle, une
sonde. Ce que chaque profil compose — ce qu il montre, dans quel ordre, ses
favoris, ses mesures masquees — lui appartient, et une session ne doit pouvoir
ecrire que la sienne.

Ce test monte un vrai Atrium, cree deux profils, et verifie qu une session ne
peut pas recomposer le tableau de l autre, meme en renvoyant une configuration
entiere qui pretend le contraire. Il verifie aussi la reprise : une
installation d avant les vues doit se retrouver telle quelle, pas videe.

Sortie : 0 si la vue tient, 1 sinon.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSSIER = os.path.join(tempfile.gettempdir(), "atrium-vues")
PROFIL, MDP = "Guillaume", "motdepasse-de-test"
AUTRE = "Clara"

ECHECS = []


def verifier(titre, obtenu, attendu):
    ok = obtenu == attendu
    print("    %-46s %-24s %s" % (titre, obtenu, "" if ok else "!!! attendu %s" % (attendu,)))
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


def appel(methode, chemin, corps=None, cookie=None):
    d = json.dumps(corps).encode() if corps is not None else None
    r = urllib.request.Request(BASE + chemin, data=d, method=methode)
    if d:
        r.add_header("Content-Type", "application/json")
    if cookie:
        r.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(r, timeout=25) as rep:
            return rep.status, rep.read(400000), rep.headers.get("Set-Cookie") or ""
    except urllib.error.HTTPError as e:
        return e.code, e.read(20000), ""
    except Exception as e:                        # noqa: BLE001
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
    except Exception:                             # noqa: BLE001
        pass
    sys.exit("serveur injoignable")


def arreter(p):
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:                             # noqa: BLE001
        p.kill()


def ecrire_config(contenu):
    with open(os.path.join(DOSSIER, "atrium.json"), "w", encoding="utf-8") as f:
        json.dump(contenu, f, ensure_ascii=False)


def config():
    with open(os.path.join(DOSSIER, "atrium.json"), encoding="utf-8") as f:
        return json.load(f)


def vue_de(cfg, nom):
    for u in cfg.get("users") or []:
        if u.get("nom") == nom:
            return u.get("vue")
    return None


def main():
    shutil.rmtree(DOSSIER, ignore_errors=True)
    os.makedirs(DOSSIER, exist_ok=True)

    # Une installation d avant les vues : favoris et masques sur les fiches,
    # partages par tout le monde.
    ecrire_config({
        "apps": [
            {"id": "plex", "nom": "Plex", "url": "http://127.0.0.1:1", "fav": True,
             "masque": ["st:debit"]},
            {"id": "unraid", "nom": "Unraid", "url": "http://127.0.0.1:2"},
        ],
        "users": [],
        "widgets": ["media", "maison"],
    })

    srv = demarrer()
    try:
        appel("POST", "/api/setup", {"nom": PROFIL, "motdepasse": MDP})
        _, _, ck = appel("POST", "/api/login", {"nom": PROFIL, "motdepasse": MDP})
        cookie = ck.split(";")[0]

        print("\n== reprise d une installation d avant les vues ==")
        code, brut, _ = appel("GET", "/api/config", cookie=cookie)
        cfg = json.loads(brut)
        vue = vue_de(cfg, PROFIL)
        verifier("le profil a une vue", isinstance(vue, dict), True)
        verifier("elle suit la maison", (vue or {}).get("montre"), None)
        verifier("les favoris d avant sont repris", (vue or {}).get("favoris"), ["plex"])
        verifier("les masques aussi", (vue or {}).get("masques"), {"plex": ["st:debit"]})

        print("\n== deux profils, deux tableaux ==")
        cfg["users"].append({"nom": AUTRE, "pwd": "", "photo": "",
                             "vue": {"montre": ["unraid"], "ordre": ["unraid"],
                                     "favoris": [], "masques": {}, "widgets": None}})
        code, brut, _ = appel("POST", "/api/config", cfg, cookie)
        verifier("le second profil est accepte", code, 200)
        disque = config()
        verifier("sa vue est enregistree", (vue_de(disque, AUTRE) or {}).get("montre"),
                 ["unraid"])
        verifier("celle du premier n a pas bouge",
                 (vue_de(disque, PROFIL) or {}).get("montre"), None)

        print("\n== une session ne recompose pas le tableau des autres ==")
        code, brut, _ = appel("GET", "/api/config", cookie=cookie)
        cfg = json.loads(brut)
        for u in cfg["users"]:
            if u["nom"] == AUTRE:
                u["vue"] = {"montre": [], "ordre": [], "favoris": ["plex"],
                            "masques": {"unraid": ["st:cpu"]}, "widgets": []}
            else:
                u["vue"]["favoris"] = ["unraid"]        # la sienne, elle, doit passer
        code, _, _ = appel("POST", "/api/config", cfg, cookie)
        verifier("la requete est acceptee", code, 200)
        disque = config()
        verifier("la vue de l autre est intacte",
                 (vue_de(disque, AUTRE) or {}).get("montre"), ["unraid"])
        verifier("ses favoris n ont pas ete inventes",
                 (vue_de(disque, AUTRE) or {}).get("favoris"), [])
        verifier("la sienne, en revanche, est bien ecrite",
                 (vue_de(disque, PROFIL) or {}).get("favoris"), ["unraid"])

        print("\n== un profil cree sans vue en recoit une ==")
        cfg = json.loads(appel("GET", "/api/config", cookie=cookie)[1])
        cfg["users"].append({"nom": "Jules", "pwd": "", "photo": ""})
        appel("POST", "/api/config", cfg, cookie)
        verifier("elle existe", isinstance(vue_de(config(), "Jules"), dict), True)

        print("\n== les services restent communs ==")
        verifier("la maison declare toujours ses deux fiches",
                 [a["nom"] for a in config().get("apps") or []], ["Plex", "Unraid"])
    finally:
        arreter(srv)

    print()
    if ECHECS:
        print("RESUME : %d point(s) a corriger" % len(ECHECS))
        for e in ECHECS:
            print("  - " + e)
        return 1
    print("RESUME : chaque profil compose son tableau, et lui seul")
    return 0


if __name__ == "__main__":
    sys.exit(main())
