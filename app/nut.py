"""Atrium — les onduleurs, lus directement par le protocole NUT.

NUT (Network UPS Tools) est ce qui fait tourner la plupart des onduleurs
branches sur un NAS : Unraid, Synology, TrueNAS et Proxmox l installent tous, et
il ecoute sur le port 3493. Le dialogue tient en quelques lignes de texte :

    LIST VAR onduleur
    BEGIN LIST VAR onduleur
    VAR onduleur battery.charge "100"
    VAR onduleur ups.status "OL"
    END LIST VAR onduleur

Aucun nuage, aucune cle d API : la machine qui porte l onduleur le publie sur
le reseau local, et Atrium le lit. C est la difference avec une station
portable de type EcoFlow, qui n a pas d interface locale et dont les mesures ne
passent que par le service du constructeur — d ou le detour par Home Assistant.

La regle du reseau est la meme qu ailleurs : le nom est resolu, toutes ses
adresses doivent etre privees, et l on se connecte a l adresse validee. Un
onduleur ne se trouve pas sur Internet.
"""
import socket

import reseau

PORT = 3493
DELAI = 4
# Un onduleur publie une trentaine de variables ; le double suffit largement,
# et borne ce qu un service bavard peut nous faire lire.
MAX_LIGNES = 200
MAX_OCTETS = 64 * 1024


class Refus(Exception):
    """Le serveur a repondu, et a refuse. Le motif est dans le message."""


def _adresse(hote):
    """L adresse privee derriere ce nom, ou None si l on n en veut pas.

    Le nom peut deja etre une adresse. Dans tous les cas, ce qui est joint est
    ce qui a ete verifie : entre le controle et la connexion, une reponse DNS
    ne doit pas pouvoir changer de cible.
    """
    hote = (hote or "").strip()
    if not hote:
        return None
    if reseau.prive(hote):
        return hote                 # adresse litterale, deja privee
    ips = [i for i in reseau.resoudre(hote) if reseau.prive(i)]
    if not ips or len(ips) != len(reseau.resoudre(hote)):
        # une seule adresse publique derriere le nom suffit a refuser
        return None
    return ips[0]


def _decoder(brut):
    """« "Eaton 5E \\"850\\"" » -> Eaton 5E "850".

    Les valeurs sont entre guillemets, et les guillemets internes echappes.
    """
    if not brut.startswith('"'):
        return brut.strip()
    sortie, echappe = [], False
    for c in brut[1:]:
        if echappe:
            sortie.append(c)
            echappe = False
        elif c == "\\":
            echappe = True
        elif c == '"':
            break
        else:
            sortie.append(c)
    return "".join(sortie)


class _Dialogue:
    """Une connexion a upsd, le temps de poser quelques questions."""

    def __init__(self, hote, port=PORT, delai=DELAI):
        self.ip = _adresse(hote)
        self.port = int(port or PORT)
        self.delai = delai
        self.sock = None
        self._reste = b""

    def __enter__(self):
        if not self.ip:
            raise Refus("adresse hors du reseau prive")
        self.sock = socket.create_connection((self.ip, self.port), self.delai)
        self.sock.settimeout(self.delai)
        return self

    def __exit__(self, *a):
        try:
            if self.sock:
                self.sock.sendall(b"LOGOUT\n")
        except OSError:
            pass
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass

    def _ligne(self):
        """La prochaine ligne, sans son retour."""
        lu = 0
        while b"\n" not in self._reste:
            paquet = self.sock.recv(4096)
            if not paquet:
                return None
            lu += len(paquet)
            self._reste += paquet
            if len(self._reste) > MAX_OCTETS:
                raise Refus("reponse trop longue")
        ligne, self._reste = self._reste.split(b"\n", 1)
        return ligne.decode("utf-8", "replace").rstrip("\r")

    def demander(self, commande):
        """Une commande, et les lignes qui lui repondent.

        Une reponse a plusieurs lignes est encadree par BEGIN et END ; une
        reponse courte tient sur une ligne. Un ERR est une reponse, pas une
        panne : c est ainsi qu upsd dit « mot de passe requis ».
        """
        self.sock.sendall((commande + "\n").encode("utf-8"))
        premiere = self._ligne()
        if premiere is None:
            raise Refus("connexion fermee sans reponse")
        if premiere.startswith("ERR "):
            raise Refus(premiere[4:].strip().lower().replace("-", " "))
        if not premiere.startswith("BEGIN "):
            return [premiere]
        lignes = []
        for _ in range(MAX_LIGNES):
            ligne = self._ligne()
            if ligne is None or ligne.startswith("END "):
                return lignes
            lignes.append(ligne)
        raise Refus("liste sans fin")

    def s_identifier(self, utilisateur, motdepasse):
        """Certains upsd exigent un compte, meme pour lire."""
        if utilisateur:
            self.demander("USERNAME " + utilisateur)
        if motdepasse:
            self.demander("PASSWORD " + motdepasse)


def _couple(cle):
    """« utilisateur:motdepasse » — les deux sont facultatifs."""
    cle = (cle or "").strip()
    if not cle:
        return "", ""
    if ":" in cle:
        u, _, m = cle.partition(":")
        return u.strip(), m.strip()
    # Un mot de passe seul n aurait pas de sens ici : upsd demande d abord un
    # compte. On prend donc la valeur pour un nom d utilisateur.
    return cle, ""


def onduleurs(hote, port=PORT, cle="", delai=DELAI):
    """Les onduleurs declares par ce serveur : [(nom, description)]."""
    utilisateur, motdepasse = _couple(cle)
    with _Dialogue(hote, port, delai) as d:
        d.s_identifier(utilisateur, motdepasse)
        trouves = []
        for ligne in d.demander("LIST UPS"):
            # UPS eaton "Eaton 5E 850 au sous-sol"
            morceaux = ligne.split(" ", 2)
            if len(morceaux) >= 2 and morceaux[0] == "UPS":
                desc = _decoder(morceaux[2]) if len(morceaux) > 2 else ""
                trouves.append((morceaux[1], desc))
        return trouves


def variables(hote, port=PORT, cle="", nom=None, delai=DELAI):
    """Tout ce que l onduleur publie, en un aller-retour.

    Sans nom, le premier declare : une installation domestique n en a qu un, et
    demander lequel avant de savoir ce qui existe serait mettre la charrue
    devant les boeufs.
    """
    utilisateur, motdepasse = _couple(cle)
    with _Dialogue(hote, port, delai) as d:
        d.s_identifier(utilisateur, motdepasse)
        if not nom:
            for ligne in d.demander("LIST UPS"):
                morceaux = ligne.split(" ", 2)
                if len(morceaux) >= 2 and morceaux[0] == "UPS":
                    nom = morceaux[1]
                    break
        if not nom:
            raise Refus("aucun onduleur declare")
        vars_ = {}
        for ligne in d.demander("LIST VAR " + nom):
            # VAR eaton battery.charge "100"
            morceaux = ligne.split(" ", 3)
            if len(morceaux) >= 4 and morceaux[0] == "VAR":
                vars_[morceaux[2]] = _decoder(morceaux[3])
        return nom, vars_


def identifier(hote, port=PORT, cle="", delai=2.0):
    """Y a-t-il un upsd derriere cette adresse, et que sert-il ?

    Rendu sous la forme attendue par la detection : (nom affiche, version).
    Le nom du modele vaut mieux que « NUT » : c est ce que l utilisateur a
    branche, pas le logiciel qui le publie.
    """
    try:
        nom, vars_ = variables(hote, port, cle, delai=delai)
    except Refus:
        raise
    except OSError:
        return None
    if not vars_:
        return None
    modele = " ".join(x for x in (vars_.get("device.mfr") or vars_.get("ups.mfr"),
                                  vars_.get("device.model") or vars_.get("ups.model"))
                      if x).strip()
    return (modele or nom, vars_.get("ups.firmware") or "")
