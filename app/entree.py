"""Atrium — point d entree du conteneur.

Le serveur n a besoin d aucun privilege : il ecoute sur 8420, lit son dossier
de configuration, et rien d autre. Il peut donc tourner sous un compte
ordinaire — encore faut il savoir lequel.

C est tout le probleme des volumes montes depuis un NAS : /config appartient a
99:100 sur Unraid, a 1000:1000 ailleurs, parfois a root. Un compte fixe grave
dans l image rendrait ce dossier illisible sur la moitie des machines, et
Atrium ne pourrait plus rien enregistrer.

D ou ce point d entree. Il demarre en root, le temps de trois gestes :

  1. donner /config au compte demande, sans quoi le serveur ne pourrait plus y
     ecrire une fois descendu ;
  2. abandonner tous les groupes secondaires, puis prendre le groupe et le
     compte demandes — dans cet ordre, l inverse ne fonctionnerait pas ;
  3. remplacer le processus par le serveur.

Sans PUID ni PGID, rien de tout cela : le serveur demarre comme avant. Une
installation existante ne change pas de comportement en se mettant a jour,
et le jour ou l on veut sortir de root, deux variables suffisent.
"""
import os
import sys

CONFIG_DIR = os.environ.get("ATRIUM_CONFIG_DIR", "/config")
SERVEUR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")


def dire(message):
    print("Atrium : " + message, flush=True)


def _entier(nom):
    """La valeur demandee, ou None si elle n est pas un nombre utilisable."""
    brut = (os.environ.get(nom) or "").strip()
    if not brut:
        return None
    try:
        n = int(brut)
    except ValueError:
        dire("%s=%r ignore : un nombre est attendu." % (nom, brut))
        return None
    if n < 0:
        dire("%s=%d ignore : un identifiant ne peut pas etre negatif." % (nom, n))
        return None
    return n


def projet():
    """Ce qu il convient de faire, avant de le faire.

    Rendu a part pour etre verifiable sans avoir a devenir root : la decision
    ne depend que de trois choses — qui l on est, ce qui est demande, et si le
    systeme sait changer d identite.
    """
    puid, pgid = _entier("PUID"), _entier("PGID")
    if puid is None and pgid is None:
        return ("racine", None, None,
                "aucun PUID/PGID : le serveur garde les droits du conteneur")
    if not hasattr(os, "setuid"):
        return ("racine", None, None,
                "ce systeme ne sait pas changer d identite : PUID/PGID ignores")
    if os.geteuid() != 0:
        return ("deja", None, None,
                "le conteneur ne tourne deja pas en root (uid %d) : PUID/PGID "
                "ignores" % os.geteuid())
    # L un sans l autre est courant : Unraid ne propose parfois que PUID. Le
    # groupe suit alors le compte, ce qui est le comportement attendu.
    uid = puid if puid is not None else pgid
    gid = pgid if pgid is not None else puid
    return ("descendre", uid, gid,
            "le serveur tournera sous %d:%d" % (uid, gid))


def donner(chemin, uid, gid):
    """Le dossier de configuration passe au compte qui va s en servir.

    Recursif, mais sur un dossier qui tient en quelques fichiers. Un echec ne
    doit pas empecher le demarrage : l utilisateur a peut etre deja pose les
    bons droits, et un volume en lecture seule est son choix.
    """
    if not os.path.isdir(chemin):
        try:
            os.makedirs(chemin, exist_ok=True)
        except OSError as e:
            dire("impossible de creer %s (%s)" % (chemin, e))
            return
    faits, rates = 0, 0
    for racine, dossiers, fichiers in os.walk(chemin):
        for nom in [racine] + [os.path.join(racine, x) for x in dossiers + fichiers]:
            try:
                infos = os.stat(nom)
                if infos.st_uid != uid or infos.st_gid != gid:
                    os.chown(nom, uid, gid)
                    faits += 1
            except OSError:
                rates += 1
    if faits:
        dire("%d fichier(s) de %s donnes a %d:%d" % (faits, chemin, uid, gid))
    if rates:
        dire("%d fichier(s) de %s n ont pas pu changer de proprietaire "
             "(volume en lecture seule ?)" % (rates, chemin))


def descendre(uid, gid):
    """Abandonner les privileges, sans retour possible.

    L ordre compte : les groupes secondaires d abord, le groupe ensuite, le
    compte en dernier. Un processus qui a deja quitte root ne peut plus rien
    reprendre — c est justement ce qu on veut.
    """
    try:
        os.setgroups([])
    except (OSError, AttributeError):
        pass                       # sans groupes secondaires a abandonner
    os.setgid(gid)
    os.setuid(uid)
    # Le dossier du code appartient a root : l ecriture des .pyc y echouerait a
    # chaque demarrage. On l en dispense plutot que de laisser Python essayer.
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def main():
    quoi, uid, gid, pourquoi = projet()
    dire(pourquoi)
    if quoi == "descendre":
        donner(CONFIG_DIR, uid, gid)
        descendre(uid, gid)
        dire("droits abandonnes : uid %d, gid %d" % (os.getuid(), os.getgid()))
    # Deux chemins constants : l interpreteur courant et le fichier voisin.
    # Rien de ce qui vient de l exterieur n entre ici, et l on remplace le
    # processus plutot que d en creer un second — le serveur devient PID 1, et
    # recoit donc directement l arret demande par Docker.
    os.execv(sys.executable, [sys.executable, SERVEUR])  # nosec B606


if __name__ == "__main__":
    main()
