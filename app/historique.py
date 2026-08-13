"""Atrium — disponibilite des services, heure par heure.

C est la premiere serie temporelle qu Atrium conserve sur disque. Le reste de
l historique (temps de reponse, charge de la machine) vit en memoire et
disparait au redemarrage, ce qui convient a une courbe de quelques minutes mais
pas a une disponibilite sur vingt-quatre heures : elle serait remise a zero au
premier « docker restart ».

Ce qui est ecrit est volontairement minuscule : par service et par heure, cinq
entiers — combien de sondes, combien d echecs, puis combien de temps de reponse
releves, leur somme et le pire. Aucune date individuelle n est conservee, aucune
mesure prise isolement : ce fichier repond a « ce service repondait-il cette
heure-la, et en combien de temps ». Douze services sur trente jours tiennent
dans quelques centaines de kilo-octets.

La somme plutot que la moyenne : additionner deux heures de moyennes n a pas de
sens quand elles n ont pas le meme nombre de sondes, tandis que deux sommes
s additionnent sur n importe quelle duree.
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


def _complet(seau):
    """Ramene un seau a cinq entiers.

    Les fichiers ecrits par les versions precedentes n en portent que deux :
    les heures d avant la mise a jour gardent leur disponibilite et n annoncent
    aucun temps de reponse, ce qui est exactement la verite les concernant.
    """
    v = [int(x) for x in list(seau)[:5]]
    return v + [0] * (5 - len(v))


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
            self.data = {nom: {int(h): _complet(v) for h, v in seaux.items()}
                         for nom, seaux in brut.items()}
        except (OSError, ValueError, AttributeError, TypeError):
            self.data = {}

    def noter(self, nom, joignable, latence=None):
        """Enregistre une sonde. Appele a chaque cycle, pour chaque service.

        « latence » est absente quand le service n a pas repondu : un echec n a
        pas de temps de reponse, et en compter un fausserait la moyenne.
        """
        if not nom:
            return
        h = _maintenant()
        with self.lock:
            seaux = self.data.setdefault(nom, {})
            seau = seaux.setdefault(h, [0, 0, 0, 0, 0])
            seau[0] += 1
            if not joignable:
                seau[1] += 1
            if latence is not None:
                try:
                    ms = int(round(float(latence)))
                except (TypeError, ValueError):
                    ms = None
                if ms is not None and ms >= 0:
                    seau[2] += 1
                    seau[3] += ms
                    seau[4] = max(seau[4], ms)
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
            # _complet plutot que list : un seau venu d un fichier ancien, ou
            # pose par un test, n a que ses deux premiers entiers.
            return [(_complet(seaux[h]) if h in seaux else None)
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
        # Le temps de reponse ne se calcule que sur les sondes qui en ont un :
        # une heure entierement en panne n en fournit aucun, et ne doit pas
        # tirer la moyenne vers le bas.
        releves = sum(s[2] for s in connus)
        pires = [s[4] for s in connus if s[4]]
        return {
            "seaux": seaux,
            "heures": len(connus),
            "sondes": sondes,
            "dispo": round((sondes - echecs) * 100.0 / sondes, 1) if sondes else None,
            "incidents": incidents,
            "releves": releves,
            "lat_moy": round(sum(s[3] for s in connus) / float(releves)) if releves else None,
            "lat_max": max(pires) if pires else None,
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


class Mesures:
    """Les mesures des services, heure par heure.

    Meme grain et meme retention que la disponibilite, dans un fichier a part :
    l un pese quelques centaines de kilo-octets, l autre environ un mega, et
    les melanger obligerait a tout relire pour repondre a une question sur l un
    ou sur l autre.

    Par service, par mesure et par heure : combien de relevés, leur somme, le
    plus bas et le plus haut. De quoi calculer une moyenne ponderee sur
    n importe quelle duree, et retrouver les extremes sans les avoir lisses.

    Ce qui est retenu ensuite depend de ce que la mesure mesure : une occupation
    se resume par sa moyenne et son pire, un compteur par ses extremes. Ce choix
    n est pas fait ici — metriques.agregats le dit, et l interface l applique.
    """

    def __init__(self, chemin):
        self.chemin = chemin
        self.lock = threading.Lock()
        self.data = {}
        self._dernier_ecrit = 0
        self._sale = False
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                brut = json.load(f) or {}
            self.data = {
                service: {mes: {int(h): [float(x) for x in v] for h, v in heures.items()}
                          for mes, heures in mesures.items()}
                for service, mesures in brut.items()}
        except (OSError, ValueError, AttributeError, TypeError):
            self.data = {}

    def noter(self, service, ident, valeur):
        """Ajoute un releve. Les valeurs non numeriques sont ignorees."""
        if not service or not ident:
            return
        try:
            v = float(valeur)
        except (TypeError, ValueError):
            return
        if v != v or v in (float("inf"), float("-inf")):   # NaN, infinis
            return
        h = _maintenant()
        with self.lock:
            heures = self.data.setdefault(service, {}).setdefault(ident, {})
            seau = heures.get(h)
            if seau is None:
                heures[h] = [1, v, v, v]
            else:
                seau[0] += 1
                seau[1] += v
                seau[2] = min(seau[2], v)
                seau[3] = max(seau[3], v)
            self._sale = True
            self._elaguer(heures)
        self._peut_etre_ecrire()

    def oublier(self, services, releves=None):
        """Retire ce qui n a plus de raison d etre garde.

        « services » est l ensemble des services configures : ceux qui n y sont
        plus ont ete supprimes, leur historique part avec eux.

        « releves » associe a chaque service les mesures qu il vient de rendre.
        Un service absent de ce dictionnaire n a rien rendu a ce cycle — panne,
        cle refusee, redemarrage — et ce silence ne dit rien de ses mesures : on
        ne touche pas a son historique. Sans cette distinction, une heure
        d indisponibilite effacerait trente jours de series.
        """
        releves = releves or {}
        with self.lock:
            for service in [s for s in self.data if s not in services]:
                del self.data[service]
                self._sale = True
            for service, gardees in releves.items():
                mesures = self.data.get(service)
                if not mesures or not gardees:
                    continue
                for mes in [m for m in mesures if m not in gardees]:
                    del mesures[mes]
                    self._sale = True

    def serie(self, service, ident, combien=24):
        """Les « combien » dernieres heures, de la plus ancienne a maintenant.

        Une heure sans releve rend None : le graphique doit pouvoir montrer un
        trou plutot que de relier deux points qui ne se suivent pas.
        """
        fin = _maintenant()
        with self.lock:
            heures = (self.data.get(service) or {}).get(ident) or {}
            points = []
            for h in range(fin - combien + 1, fin + 1):
                s = heures.get(h)
                points.append(None if not s else {
                    "n": int(s[0]),
                    "moy": s[1] / s[0] if s[0] else None,
                    "min": s[2],
                    "max": s[3],
                })
            return points

    def resume(self, service, ident, combien=24):
        """Moyenne ponderee, extremes, et ce que la plage couvre reellement."""
        points = self.serie(service, ident, combien)
        connus = [p for p in points if p]
        n = sum(p["n"] for p in connus)
        return {
            "points": points,
            "heures": len(connus),
            "releves": n,
            "moy": round(sum(p["moy"] * p["n"] for p in connus) / n, 2) if n else None,
            "min": min((p["min"] for p in connus), default=None),
            "max": max((p["max"] for p in connus), default=None),
        }

    def mesures_de(self, service):
        with self.lock:
            return sorted((self.data.get(service) or {}).keys())

    def _elaguer(self, heures):
        if len(heures) <= RETENTION:
            return
        for h in sorted(heures)[:-RETENTION]:
            del heures[h]

    def _peut_etre_ecrire(self):
        maintenant = time.time()
        with self.lock:
            if not self._sale or maintenant - self._dernier_ecrit < ECRITURE:
                return
            self._dernier_ecrit = maintenant
            self._sale = False
            copie = {
                service: {mes: {str(h): [_court(x) for x in v] for h, v in heures.items()}
                          for mes, heures in mesures.items()}
                for service, mesures in self.data.items()}
        try:
            tmp = self.chemin + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(copie, f, separators=(",", ":"))
            os.replace(tmp, self.chemin)
        except OSError:
            pass


class Evenements:
    """Le journal d Atrium, service par service.

    Ce qu Atrium a lui-meme constate : une bascule d etat, un seuil franchi puis
    rentre dans l ordre, une premiere apparition. Pas les evenements du service
    — Atrium ne lit pas les journaux de Plex ou de Home Assistant, et pretendre
    le contraire remplirait la page de choses inventees.

    Chaque entree porte un instant, un code et ses parametres ; le texte est
    traduit par l interface, comme pour les alertes. Cinquante entrees par
    service : au-dela, ce n est plus un journal, c est une archive, et ce n est
    pas ce qu on vient y chercher.
    """

    GARDE = 50

    def __init__(self, chemin):
        self.chemin = chemin
        self.lock = threading.Lock()
        self.data = {}
        self._dernier_ecrit = 0
        self._sale = False
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                brut = json.load(f) or {}
            self.data = {nom: [dict(e) for e in liste][-self.GARDE:]
                         for nom, liste in brut.items()}
        except (OSError, ValueError, AttributeError, TypeError):
            self.data = {}

    def noter(self, service, code, param=None):
        if not service or not code:
            return
        with self.lock:
            liste = self.data.setdefault(service, [])
            liste.append({"a": int(time.time()), "code": code, "param": param or {}})
            del liste[:-self.GARDE]
            self._sale = True
        self._peut_etre_ecrire()

    def lister(self, service, combien=20):
        with self.lock:
            return [dict(e) for e in (self.data.get(service) or [])[-combien:]][::-1]

    def tout(self, combien=200):
        """Les evenements de tous les services, du plus recent au plus ancien.

        Chaque entree porte le nom de son service : sans lui, un journal
        commun ne dirait pas de quoi il parle.
        """
        with self.lock:
            liste = []
            for service, evs in self.data.items():
                for e in evs:
                    ligne = dict(e)
                    ligne["service"] = service
                    liste.append(ligne)
        liste.sort(key=lambda e: e.get("a", 0), reverse=True)
        return liste[:combien]

    def oublier(self, services):
        with self.lock:
            for perdu in [s for s in self.data if s not in services]:
                del self.data[perdu]
                self._sale = True

    def _peut_etre_ecrire(self):
        maintenant = time.time()
        with self.lock:
            if not self._sale or maintenant - self._dernier_ecrit < ECRITURE:
                return
            self._dernier_ecrit = maintenant
            self._sale = False
            copie = {nom: list(liste) for nom, liste in self.data.items()}
        try:
            tmp = self.chemin + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(copie, f, separators=(",", ":"), ensure_ascii=False)
            os.replace(tmp, self.chemin)
        except OSError:
            pass


def _court(x):
    """Un entier reste un entier : « 95 » plutot que « 95.0 » dans le fichier.

    Sur sept cent vingt heures et douze services, l economie n est pas
    decorative.
    """
    e = int(x)
    return e if e == x else round(x, 2)
