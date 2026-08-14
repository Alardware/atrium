"""Matrice de securite d Atrium : chaque route, avec et sans session.

Le principe : ne rien croire sur parole. On demarre un Atrium, on cree un
profil, puis on frappe chaque route deux fois — sans cookie, puis avec — et on
verifie que seules les routes publiques repondent aux inconnus. Suivent
quelques abus caracterises (relais vers Internet, lecture de fichier, second
installateur) et une relecture des reponses a la recherche de secrets.

Les routes ne sont pas recopiees ici : elles sont relues dans le source du
serveur. Une route ajoutee sans session obligatoire apparait donc dans cet
audit le jour meme, et non le jour ou quelqu un pense a l y inscrire.

Usage :
    python outils/audit_securite.py [http://127.0.0.1:8420]

Sortie : 0 si tout est en ordre, 1 des qu un point cloche — de quoi arreter
une chaine d integration.
"""
import http.cookies
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else
        os.environ.get("ATRIUM_URL") or "http://127.0.0.1:8420").rstrip("/")
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(RACINE, "app", "server.py")

PROFIL = os.environ.get("ATRIUM_AUDIT_USER", "auditeur")
SECRET = os.environ.get("ATRIUM_AUDIT_PASS", "mot-de-passe-audit-2026")

# Ce qu un visiteur sans session a le droit d obtenir : l ecran de connexion en
# depend. Toute autre route doit repondre 401 ou 403.
PUBLIQUES = {"/api/health", "/api/session", "/api/login", "/api/logout",
             "/api/setup", "/api/password"}

# Corps et parametres que le source ne peut pas deviner.
CORPS = {
    "/api/config": {}, "/api/detect": {"url": "http://127.0.0.1:9112"},
    "/api/alertes/lues": {}, "/api/sonder": {"nom": "Inconnu"},
    "/api/conteneur/redemarrer": {"nom": "x"}, "/api/sessions/revoquer": {},
    "/api/setup": {"nom": "pirate"}, "/api/password": {"ancien": "x", "nouveau": "y"},
    "/api/login": {"nom": "pirate", "motdepasse": "x"}, "/api/logout": {},
    "/api/restauration": {},
}
QUERY = {
    "/api/mesure": "?service=Inconnu&id=disque&h=24",
    "/api/historique": "?h=24",
}
DEPART = [
    ("GET", "/px?u=http://127.0.0.1:9112/", None),
]


def routes_du_serveur():
    """(methode, route) pour tout ce que le serveur route effectivement."""
    src = open(SOURCE, encoding="utf-8").read()
    trouve, methode = set(), None
    for ligne in src.splitlines():
        m = re.match(r"\s*def do_(GET|POST)", ligne)
        if m:
            methode = m.group(1)
        # « route == "/x" » comme « route in ("/x", "/y") » : on prend toutes
        # les chaines de la comparaison, sans quoi une route jumelle passerait
        # sous le radar — c est arrive a /api/config, cachee derriere /cfg.
        comparaison = re.search(r"route (?:==|in) (.+)", ligne)
        if comparaison and methode:
            for r in re.findall(r'"(/[^"]*)"', comparaison.group(1)):
                trouve.add((methode, r))
    return trouve


def appel(methode, chemin, corps, cookie=None):
    d = json.dumps(corps).encode() if corps is not None else None
    r = urllib.request.Request(BASE + chemin, data=d, method=methode)
    if d:
        r.add_header("Content-Type", "application/json")
    if cookie:
        r.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(r, timeout=20) as rep:
            return rep.status, rep.read(8000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(8000)
    except Exception as e:                                 # hote injoignable
        return 0, str(e).encode()


def session():
    """Un profil et son cookie. Le premier appel installe Atrium si besoin."""
    etat = json.loads(appel("GET", "/api/session", None)[1] or b"{}")
    if not etat.get("installe"):
        code, corps = appel("POST", "/api/setup", {"nom": PROFIL, "motdepasse": SECRET})
        if code != 200:
            print("installation impossible : %s %s" % (code, corps[:200]))
            sys.exit(2)
    d = json.dumps({"nom": PROFIL, "motdepasse": SECRET}).encode()
    r = urllib.request.Request(BASE + "/api/login", data=d, method="POST")
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=20) as rep:
            c = http.cookies.SimpleCookie(rep.headers.get("Set-Cookie"))
    except urllib.error.HTTPError as e:
        print("connexion refusee : %s" % e.code)
        sys.exit(2)
    return "atrium_session=" + c["atrium_session"].value


def main():
    cookie = session()
    souci = []

    # /api/logout fermerait la session au milieu de la matrice : sa protection
    # se verifie a part, en dernier.
    routes = [(m, r + QUERY.get(r, ""), CORPS.get(r) if m == "POST" else None)
              for m, r in sorted(routes_du_serveur())
              if (m, r) != ("POST", "/api/logout")]
    routes = DEPART + routes

    print("%-7s %-34s %5s %5s  %s" % ("METHODE", "ROUTE", "SANS", "AVEC", "VERDICT"))
    for m, ch, corps in routes:
        sans, _ = appel(m, ch, corps)
        avec, _ = appel(m, ch, corps, cookie)
        publique = ch.split("?")[0] in PUBLIQUES
        ok = publique or sans in (401, 403)
        if not ok:
            souci.append("%s %s accessible sans session (%d)" % (m, ch, sans))
        print("%-7s %-34s %5s %5s  %s"
              % (m, ch, sans, avec,
                 "publique" if publique else ("protegee" if ok else "!!! OUVERTE")))

    print()
    print("--- abus ---")
    cas = [
        ("relais vers Internet", "GET", "/px?u=http://example.com/", None, (403,)),
        ("relais vers un fichier", "GET", "/px?u=file:///etc/passwd", None, (400, 403)),
        ("detection hors reseau prive", "POST", "/api/detect",
         {"url": "http://example.com"}, (403,)),
        ("second installateur", "POST", "/api/setup",
         {"nom": "pirate", "motdepasse": "abcdef"}, (409,)),
        ("config sans profils", "POST", "/api/config", {"apps": [], "users": []}, (409,)),
    ]
    for nom, m, ch, corps, attendus in cas:
        code, _ = appel(m, ch, corps, cookie)
        ok = code in attendus
        print("%-30s -> %-4s %s" % (nom, code, "ok" if ok else "!!! ATTENDU %s" % (attendus,)))
        if not ok:
            souci.append("%s : %s" % (nom, code))

    print()
    print("--- ce que les reponses laissent voir ---")
    controles = [
        ("/api/sessions", ("jeton",)),
        ("/api/widgets", ("apiKey", "token", "X-Plex")),
        ("/api/supervision", ("apiKey", "token", "pwd")),
        ("/api/config", ("pbkdf2",)),          # empreintes : masquees, meme avec session
    ]
    for chemin, motifs in controles:
        _, corps = appel("GET", chemin, None, cookie)
        texte = corps.decode("utf-8", "replace")
        fuite = [x for x in motifs if x in texte]
        print("%-20s %s" % (chemin, fuite or "rien de sensible"))
        if fuite:
            souci.append("%s laisse voir %s" % (chemin, fuite))

    # En dernier : la deconnexion ferme la session utilisee ci-dessus.
    code, _ = appel("POST", "/api/password", {"nouveau": "aaaaaa"})
    print()
    print("changement de mot de passe sans session : %s %s"
          % (code, "ok" if code == 401 else "!!! OUVERT"))
    if code != 401:
        souci.append("/api/password ouvert sans session")

    print()
    if souci:
        print("RESUME : %d point(s) a corriger" % len(souci))
        for s in souci:
            print("  - " + s)
        return 1
    print("RESUME : aucun probleme")
    return 0


if __name__ == "__main__":
    sys.exit(main())
