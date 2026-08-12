"""Atrium — disponibilite des services, heure par heure.

C est la premiere serie temporelle qu Atrium conserve sur disque. Le reste de
l historique (temps de reponse, charge de la machine) vit en memoire et
disparait au redemarrage, ce qui convient a une courbe de quelques minutes mais
pas a une disponibilite sur vingt-quatre heures : elle serait remise a zero au
premier « docker restart ».

Ce qui est ecrit est volontairement minuscule : par service et par heure, deux
entiers — combien de sondes, combien d echecs. Douze services sur trente jours
tiennent dans environ cent quarante kilo-octets. Aucune date individuelle n est
conservee, aucun temps de reponse : ce fichier ne sert qu a repondre « ce
service repondait-il, cette heure-la ».
"""
import json
import os
import threading
import time

HEURE = 3600
RETENTION = 30 * 24          # seaux gardes par service, soit trente jours
ECRITURE = 300               # au plus une ecriture toutes les cinq minutes


def _maintenant():
    return int(time.time() // HEURE)


class Historique:
    def __init__(self, chemin):
        self.chemin = chemin
        self.lock = threading.Lock()
        self.data = {}
        self._dernier_ecrit = 0
        self._sale = False
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                brut = json.load(f) or {}
            # les cles JSON sont des chaines : on les ramene a des entiers
            self.data = {nom: {int(h): list(v) for h, v in seaux.items()}
                         for nom, seaux in brut.items()}
        except (OSError, ValueError, AttributeError, TypeError):
            self.data = {}

    def noter(self, nom, joignable):
        """Enregistre une sonde. Appele a chaque cycle, pour chaque service."""
        if not nom:
            return
        h = _maintenant()
        with self.lock:
            seaux = self.data.setdefault(nom, {})
            seau = seaux.setdefault(h, [0, 0])
            seau[0] += 1
            if not joignable:
                seau[1] += 1
            self._sale = True
            self._elaguer(seaux)
        self._peut_etre_ecrire()

    def oublier(self, noms_vivants):
        """Retire les services qui ne sont plus configures.

        Sans cela, renommer une application laisserait son historique grossir
        indefiniment sous un nom que plus rien ne reclame.
        """
        with self.lock:
            perdus = [n for n in self.data if n not in noms_vivants]
            for n in perdus:
                del self.data[n]
            if perdus:
                self._sale = True

    def seaux(self, nom, combien=24):
        """Les « combien » dernieres heures, de la plus ancienne a maintenant.

        Une heure sans aucune sonde rend None plutot que zero : « pas de
        mesure » et « aucune reponse » ne sont pas la meme chose, et le
        graphique doit pouvoir les distinguer.
        """
        fin = _maintenant()
        with self.lock:
            seaux = self.data.get(nom) or {}
            return [(list(seaux[h]) if h in seaux else None)
                    for h in range(fin - combien + 1, fin + 1)]

    def resume(self, nom, combien=24):
        """Disponibilite, heures couvertes et nombre d incidents.

        Un incident est une suite d heures consecutives comportant au moins un
        echec. Deux heures de panne d affilee comptent donc pour un, ce qui
        correspond a ce qu on percoit : une coupure, pas deux.
        """
        seaux = self.seaux(nom, combien)
        connus = [s for s in seaux if s]
        sondes = sum(s[0] for s in connus)
        echecs = sum(s[1] for s in connus)
        incidents, dedans = 0, False
        for s in seaux:
            mauvais = bool(s and s[1])
            if mauvais and not dedans:
                incidents += 1
            dedans = mauvais
        return {
            "seaux": seaux,
            "heures": len(connus),
            "sondes": sondes,
            "dispo": round((sondes - echecs) * 100.0 / sondes, 1) if sondes else None,
            "incidents": incidents,
        }

    def _elaguer(self, seaux):
        if len(seaux) <= RETENTION:
            return
        for h in sorted(seaux)[:-RETENTION]:
            del seaux[h]

    def _peut_etre_ecrire(self):
        """Ecrit au plus toutes les cinq minutes.

        Sauver a chaque cycle userait le disque pour rien ; ne sauver qu au
        changement d heure ferait perdre jusqu a soixante minutes. Cinq minutes
        est le compromis : le fichier est minuscule, et un arret brutal ne coute
        que quelques sondes.
        """
        maintenant = time.time()
        with self.lock:
            if not self._sale or maintenant - self._dernier_ecrit < ECRITURE:
                return
            self._dernier_ecrit = maintenant
            self._sale = False
            copie = {nom: {str(h): v for h, v in seaux.items()}
                     for nom, seaux in self.data.items()}
        try:
            tmp = self.chemin + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(copie, f, separators=(",", ":"))
            os.replace(tmp, self.chemin)
        except OSError:
            pass                      # un disque plein ne doit pas tout arreter
