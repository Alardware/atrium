"""Le point d entree du conteneur decide-t-il correctement ?

Abandonner les privileges est irreversible : on ne peut pas l essayer pour voir.
La decision est donc prise a part, dans une fonction qui ne fait rien — et
c est elle que ce test interroge, situation par situation.

Le reste (chown, setgid, setuid) demande d etre root sur un vrai Linux : la
chaine d integration s en charge en lancant l image et en demandant au serveur
sous quel compte il tourne.

Sortie : 0 si les decisions sont justes, 1 sinon.
"""
import os
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RACINE, "app"))

import entree                       # noqa: E402

ECHECS = []


def verifier(condition, titre, detail=""):
    print(("  ok    " if condition else "  ECHEC ") + titre + (" — " + detail if detail else ""))
    if not condition:
        ECHECS.append(titre)


class Faux:
    """Le systeme, tel qu on le lui fait croire."""

    def __init__(self, uid=0, setuid=True):
        self.uid, self.setuid = uid, setuid

    def __enter__(self):
        self._geteuid = os.geteuid if hasattr(os, "geteuid") else None
        self._setuid = getattr(os, "setuid", None)
        os.geteuid = lambda: self.uid
        if self.setuid:
            os.setuid = self._setuid or (lambda n: None)
        elif hasattr(os, "setuid"):
            del os.setuid
        return self

    def __exit__(self, *a):
        if self._geteuid:
            os.geteuid = self._geteuid
        elif hasattr(os, "geteuid"):
            del os.geteuid
        if self._setuid:
            os.setuid = self._setuid
        elif hasattr(os, "setuid"):
            del os.setuid


def avec(env, uid=0, setuid=True):
    for cle in ("PUID", "PGID"):
        os.environ.pop(cle, None)
    os.environ.update(env)
    with Faux(uid=uid, setuid=setuid):
        return entree.projet()


def main():
    print("\n== sans rien demander, rien ne change ==")
    quoi, uid, gid, _ = avec({})
    verifier(quoi == "racine", "aucun PUID/PGID : le serveur demarre comme avant", quoi)

    print("\n== les deux variables posees ==")
    quoi, uid, gid, mot = avec({"PUID": "99", "PGID": "100"})
    verifier((quoi, uid, gid) == ("descendre", 99, 100),
             "le serveur descendra sous 99:100", "%s %s:%s" % (quoi, uid, gid))

    print("\n== une seule des deux ==")
    quoi, uid, gid, _ = avec({"PUID": "1000"})
    verifier((quoi, uid, gid) == ("descendre", 1000, 1000),
             "le groupe suit le compte", "%s:%s" % (uid, gid))
    quoi, uid, gid, _ = avec({"PGID": "1000"})
    verifier((quoi, uid, gid) == ("descendre", 1000, 1000),
             "et reciproquement", "%s:%s" % (uid, gid))

    print("\n== ce qui n est pas un identifiant est ignore, pas devine ==")
    for valeur in ("abc", "-5", "12,5", " "):
        quoi, uid, gid, _ = avec({"PUID": valeur})
        verifier(quoi == "racine", "PUID=%r refuse" % valeur, quoi)

    print("\n== deja descendu par Docker (--user) ==")
    quoi, uid, gid, mot = avec({"PUID": "99", "PGID": "100"}, uid=1000)
    verifier(quoi == "deja", "on ne tente pas de redescendre", quoi)
    verifier("1000" in mot, "et l on dit sous quel compte on tourne", mot)

    print("\n== un systeme qui ne sait pas changer d identite ==")
    quoi, uid, gid, _ = avec({"PUID": "99", "PGID": "100"}, setuid=False)
    verifier(quoi == "racine", "PUID/PGID ignores plutot que fatals", quoi)

    print("\n== le serveur lance est bien celui du dossier ==")
    verifier(entree.SERVEUR.endswith("server.py") and os.path.exists(entree.SERVEUR),
             "server.py trouve a cote", entree.SERVEUR)

    print()
    if ECHECS:
        print("%d point(s) en echec : %s" % (len(ECHECS), " ; ".join(ECHECS)))
        return 1
    print("les decisions du point d entree sont justes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
