"""Atrium — les metriques, et ce qu on en deduit.

Une mesure n est plus une chaine de caracteres. Elle porte un identifiant, une
valeur numerique et une nature. Le libelle et le formatage viennent d ici, une
seule fois, et le moteur d alertes raisonne sur le nombre.

Avant, une pastille valait « 95 % » et il fallait deviner au suffixe s il
s agissait d un remplissage ou d un pourcentage anodin. Ajouter une regle
demandait d ecrire une nouvelle analyse de texte. Desormais une regle est une
ligne dans SEUILS.
"""

# nature -> comment l afficher
POURCENT = "pourcent"
TEMPERATURE = "temperature"
NOMBRE = "nombre"
DEBIT_MB = "debit_mb"
DEBIT_OCTETS = "debit_octets"
LIBRE = "libre"          # la mesure fournit elle-meme son texte

# identifiant : (libelle affiche, nature)
METRIQUES = {
    # charge et materiel
    "cpu": ("CPU", POURCENT),
    "ram": ("RAM", POURCENT),
    "disque": ("DISQUE", POURCENT),
    "temp": ("TEMPÉRATURE", TEMPERATURE),
    "uptime": ("UPTIME", LIBRE),
    "docker": ("DOCKER", LIBRE),
    # media
    "lectures": ("LECTURES", NOMBRE),
    "spectateurs": ("SPECTATEURS", NOMBRE),
    "transcodages": ("TRANSCODAGES", NOMBRE),
    "bibliotheques": ("BIBLIOTHÈQUES", NOMBRE),
    "debit": ("DÉBIT", DEBIT_MB),
    "debit_o": ("DÉBIT", DEBIT_OCTETS),
    "reception": ("RÉCEPTION", DEBIT_OCTETS),
    "envoi": ("ENVOI", DEBIT_OCTETS),
    # suites de telechargement
    "manque": ("MANQUE", NOMBRE),
    "file": ("FILE", NOMBRE),
    "series": ("SÉRIES", NOMBRE),
    "films": ("FILMS", NOMBRE),
    "artistes": ("ARTISTES", NOMBRE),
    "auteurs": ("AUTEURS", NOMBRE),
    "episodes": ("ÉPISODES", NOMBRE),
    "indexeurs": ("INDEXEURS", NOMBRE),
    # reseau
    "requetes": ("REQUÊTES", NOMBRE),
    "bloquees": ("BLOQUÉES", NOMBRE),
    "clients": ("CLIENTS", NOMBRE),
    "equipements": ("ÉQUIPEMENTS", NOMBRE),
    "bornes": ("BORNES WIFI", NOMBRE),
    # conteneurs et supervision
    "actifs": ("ACTIFS", NOMBRE),
    "arretes": ("ARRÊTÉS", NOMBRE),
    "en_ligne": ("EN LIGNE", NOMBRE),
    "hors_ligne": ("HORS LIGNE", NOMBRE),
    # maison
    "indispo": ("INDISPO", NOMBRE),
    "autom_off": ("AUTOM. OFF", NOMBRE),
    "lumieres": ("LUMIÈRES", NOMBRE),
    "ouvertures": ("OUVERTURES", NOMBRE),
    "presents": ("PRÉSENTS", NOMBRE),
    # fichiers
    "photos": ("PHOTOS", NOMBRE),
    "videos": ("VIDÉOS", NOMBRE),
    "documents": ("DOCUMENTS", NOMBRE),
    "fichiers": ("FICHIERS", NOMBRE),
    "utilisateurs": ("UTILISATEURS", NOMBRE),
}

# Seuils par metrique, du plus grave au plus calme. Ajouter une surveillance
# tient desormais en une ligne : plus aucune analyse de texte.
SEUILS = {
    "disque": ((95, "critique"), (85, "avertissement"), (70, "surveillance")),
    "ram": ((95, "avertissement"), (85, "surveillance")),
    # Au-dela de 55 degres la duree de vie d un disque chute, au-dela de 60 le
    # constructeur sort de sa plage. Le processeur n est pas concerne : un pic
    # a 80 y est normal, et « cpu » n a donc pas de seuil.
    "temp": ((60, "critique"), (55, "avertissement"), (50, "surveillance")),
}


def _n(v):
    """56055 -> 56 055."""
    try:
        return "{:,}".format(int(v)).replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


def _octets(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    for unite in ("o/s", "Ko/s", "Mo/s", "Go/s"):
        if v < 1024:
            return "%.0f %s" % (v, unite) if unite == "o/s" else "%.1f %s" % (v, unite)
        v /= 1024
    return "%.1f To/s" % v


def texte(nature, num):
    if nature == POURCENT:
        return "%d %%" % round(num)
    if nature == TEMPERATURE:
        return "%d °C" % round(num)
    if nature == DEBIT_MB:
        return "%.1f Mb/s" % num
    if nature == DEBIT_OCTETS:
        return _octets(num)
    return _n(num)


def M(ident, num, libre=None):
    """Une mesure : identifiant, nombre, et son rendu.

    « lab » et « val » restent presents pour l interface, qui n a pas a
    connaitre le registre ; « id » et « num » servent aux regles.
    """
    libelle, nature = METRIQUES.get(ident, (ident.upper(), NOMBRE))
    return {
        "id": ident,
        "lab": libelle,
        "num": num,
        "val": libre if libre is not None else texte(nature, num),
    }


def niveau(ident, num):
    """Gravite atteinte par cette metrique, ou None si elle n est pas surveillee."""
    table = SEUILS.get(ident)
    if not table or num is None:
        return None
    for limite, grav in table:
        if num >= limite:
            return grav
    return None


def libelle(ident):
    return METRIQUES.get(ident, (ident.upper(), NOMBRE))[0]
