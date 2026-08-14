"""Le compte de secours (ATRIUM_ADMIN) : invisible, mais joignable.

Un vrai serveur est demarre dans un dossier jetable, avec la variable, puis
interroge comme le ferait un navigateur. Ce qui est verifie :

  1. le compte est cree au demarrage, en empreinte, jamais en clair ;
  2. il n apparait ni a l ecran de connexion, ni dans la configuration, ni dans
     l etat de securite, ni dans une sauvegarde faite depuis le navigateur ;
  3. une session ordinaire ne peut ni l effacer, ni prendre son nom, ni se
     declarer cachee a son tour — que ce soit par la configuration ou par une
     archive de restauration ;
  4. il se connecte en tapant son nom, et refuse un mauvais mot de passe ;
  5. il ne prend pas le nom d un profil deja visible, qui disparaitrait ;
  6. un secret trop court est refuse au demarrage ;
  7. il survit au retrait de la variable.

Sortie : 0 si tout tient, 1 au premier point qui cede.
"""
import io
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
import zipfile

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSSIER = os.path.join(tempfile.gettempdir(), "atrium-secours")
NOM = "secours"
SECRET = "un-mot-de-passe-solide"
PROFIL, MDP = "Proprietaire", "motdepasse-test"

ECHECS = []


def port_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = port_libre()
BASE = "http://127.0.0.1:%d" % PORT


def verifier(titre, obtenu, attendu):
    ok = obtenu == attendu
    print("    %-42s %s%s" % (titre, obtenu, "" if ok else "   !!! attendu %s" % (attendu,)))
    if not ok:
        ECHECS.append("%s : %s au lieu de %s" % (titre, obtenu, attendu))


def appel(methode, chemin, corps=None, cookie=None, brut=None):
    d = brut if brut is not None else (json.dumps(corps).encode() if corps is not None else None)
    r = urllib.request.Request(BASE + chemin, data=d, method=methode)
    if corps is not None:
        r.add_header("Content-Type", "application/json")
    if cookie:
        r.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(r, timeout=20) as rep:
            return rep.status, rep.read(200000), rep.headers.get("Set-Cookie") or ""
    except urllib.error.HTTPError as e:
        return e.code, e.read(20000), ""
    except Exception as e:
        return 0, str(e).encode(), ""


def connexion(nom, mdp):
    code, _, cookie = appel("POST", "/api/login", {"nom": nom, "motdepasse": mdp})
    if code != 200 or "atrium_session=" not in cookie:
        return None, code
    return "atrium_session=" + cookie.split("atrium_session=")[1].split(";")[0], code


def demarrer(admin=None):
    env = dict(os.environ, ATRIUM_PORT=str(PORT), ATRIUM_CONFIG_DIR=DOSSIER,
               PYTHONIOENCODING="utf-8")
    env.pop("ATRIUM_ADMIN", None)
    if admin:
        env["ATRIUM_ADMIN"] = admin
    p = subprocess.Popen([sys.executable, "server.py"], cwd=os.path.join(RACINE, "app"),
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    for _ in range(60):
        time.sleep(0.4)
        if appel("GET", "/api/health")[0] == 200:
            return p
        if p.poll() is not None:
            break
    sortie = ""
    try:
        p.kill()
        sortie = p.stdout.read()
    except Exception:
        pass
    print("serveur injoignable\n" + sortie)
    sys.exit(2)


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
    srv = demarrer("%s:%s" % (NOM, SECRET))
    try:
        print("1. cree au demarrage")
        cfg = config()
        u = next((x for x in cfg["users"] if x.get("nom") == NOM), None)
        verifier("present dans la configuration", bool(u), True)
        verifier("marque comme cache", bool(u and u.get("cache")), True)
        verifier("mot de passe en clair dans le fichier", SECRET in json.dumps(cfg), False)
        verifier("empreinte PBKDF2", (u or {}).get("pwd", "").startswith("pbkdf2$"), True)

        print("2. invisible avant toute connexion")
        _, corps, _ = appel("GET", "/api/session")
        etat = json.loads(corps)
        verifier("profils annonces", [p["nom"] for p in etat["profils"]], [])
        verifier("installation encore possible", etat["installe"], False)

        print("3. invisible depuis une session ordinaire")
        verifier("creation du profil ordinaire",
                 appel("POST", "/api/setup", {"nom": PROFIL, "motdepasse": MDP})[0], 200)
        cookie, code = connexion(PROFIL, MDP)
        verifier("connexion du profil ordinaire", code, 200)
        _, corps, _ = appel("GET", "/api/session")
        verifier("ecran de connexion", [p["nom"] for p in json.loads(corps)["profils"]], [PROFIL])
        _, corps, _ = appel("GET", "/api/config", None, cookie)
        vue = json.loads(corps)
        verifier("/api/config", [x["nom"] for x in vue.get("users", [])], [PROFIL])
        _, corps, _ = appel("GET", "/api/securite", None, cookie)
        verifier("/api/securite : nombre de profils", json.loads(corps)["profils"], 1)
        _, archive, _ = appel("GET", "/api/sauvegarde", None, cookie)
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            dedans = json.loads(z.read("atrium.json").decode("utf-8"))
        verifier("profils dans l archive", [x["nom"] for x in dedans["users"]], [PROFIL])

        print("4. une session ordinaire ne peut rien contre lui")
        verifier("enregistrement de la configuration",
                 appel("POST", "/api/config", vue, cookie)[0], 200)
        verifier("compte toujours present",
                 any(x["nom"] == NOM for x in config()["users"]), True)
        usurpe = dict(vue, users=vue["users"] + [{"nom": NOM, "pwd": "", "photo": "",
                                                  "cache": True}])
        appel("POST", "/api/config", usurpe, cookie)
        doublons = [x for x in config()["users"] if x["nom"] == NOM]
        verifier("profils portant le nom du compte", len(doublons), 1)
        verifier("compte toujours protege", bool(doublons[0].get("pwd")), True)
        verifier("drapeau cache pose par le navigateur",
                 any(x.get("cache") for x in config()["users"] if x["nom"] != NOM), False)

        print("5. les archives non plus")
        code, _, _ = appel("POST", "/api/restauration", None, cookie, brut=archive)
        verifier("restauration d une archive normale", code, 200)
        verifier("compte de secours conserve",
                 any(x["nom"] == NOM and x.get("cache") for x in config()["users"]), True)
        piege = dict(dedans, users=dedans["users"] + [{"nom": "porte", "pwd": "",
                                                      "photo": "", "cache": True}])
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w") as z:
            z.writestr("atrium.json", json.dumps(piege))
        appel("POST", "/api/restauration", None, cookie, brut=tampon.getvalue())
        porte = next((x for x in config()["users"] if x["nom"] == "porte"), None)
        verifier("compte cache installe par une archive",
                 bool(porte and porte.get("cache")), False)

        print("6. il se connecte en tapant son nom")
        ck, code = connexion(NOM, SECRET)
        verifier("connexion", code, 200)
        verifier("acces a la configuration",
                 appel("GET", "/api/config", None, ck)[0], 200)
        verifier("mauvais mot de passe", connexion(NOM, "faux")[1], 401)
    finally:
        arreter(srv)

    print("7. il ne prend pas le nom d un profil visible")
    env = dict(os.environ, ATRIUM_PORT=str(PORT), ATRIUM_CONFIG_DIR=DOSSIER,
               ATRIUM_ADMIN="%s:un-autre-mot-de-passe" % PROFIL, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "server.py"], cwd=os.path.join(RACINE, "app"),
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    time.sleep(3)
    arreter(p)
    sortie = p.stdout.read()
    verifier("refus annonce", any("deja un profil visible" in l for l in sortie.splitlines()), True)
    proprio = next((x for x in config()["users"] if x["nom"] == PROFIL), None)
    verifier("le profil visible reste visible", bool(proprio) and not proprio.get("cache"), True)

    print("8. un secret trop court est refuse")
    env = dict(os.environ, ATRIUM_PORT=str(PORT), ATRIUM_CONFIG_DIR=DOSSIER,
               ATRIUM_ADMIN="court:1234", PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "server.py"], cwd=os.path.join(RACINE, "app"),
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    time.sleep(3)
    arreter(p)
    sortie = p.stdout.read()
    verifier("refus annonce au demarrage",
             any("ATRIUM_ADMIN" in l for l in sortie.splitlines()), True)
    verifier("compte trop court cree",
             any(x["nom"] == "court" for x in config()["users"]), False)

    print("9. il survit au retrait de la variable")
    srv = demarrer()
    try:
        verifier("connexion apres redemarrage", connexion(NOM, SECRET)[1], 200)
        _, corps, _ = appel("GET", "/api/session")
        verifier("toujours absent de l ecran",
                 NOM in [p["nom"] for p in json.loads(corps)["profils"]], False)
    finally:
        arreter(srv)
        shutil.rmtree(DOSSIER, ignore_errors=True)

    print()
    if ECHECS:
        print("RESUME : %d point(s) a corriger" % len(ECHECS))
        for e in ECHECS:
            print("  - " + e)
        return 1
    print("RESUME : le compte de secours tient ses promesses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
