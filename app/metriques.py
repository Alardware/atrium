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
    # demandes de media (Seerr et ses ancetres)
    "en_attente": ("EN ATTENTE", NOMBRE),
    "approuvees": ("APPROUVÉES", NOMBRE),
    "disponibles": ("DISPONIBLES", NOMBRE),
    # reseau
    "requetes": ("REQUÊTES", NOMBRE),
    "bloquees": ("BLOQUÉES", NOMBRE),
    "clients": ("CLIENTS", NOMBRE),
    "equipements": ("ÉQUIPEMENTS", NOMBRE),
    "bornes": ("BORNES WIFI", NOMBRE),
    # bibliotheques et depots
    "titres": ("TITRES", NOMBRE),
    "livres": ("LIVRES", NOMBRE),
    "depots": ("DÉPÔTS", NOMBRE),
    "tableaux": ("TABLEAUX", NOMBRE),
    # domotique et reseau
    "objets": ("OBJETS", NOMBRE),
    "hotes": ("HÔTES", NOMBRE),
    "evenements": ("ÉVÉNEMENTS", NOMBRE),
    "grappes": ("GRAPPES", NOMBRE),
    # video-surveillance
    "cameras": ("CAMÉRAS", NOMBRE),
    "detections": ("DÉTECTIONS", NOMBRE),
    # synchronisation et virtualisation
    "dossiers": ("DOSSIERS", NOMBRE),
    "appareils": ("APPAREILS", NOMBRE),
    "machines": ("MACHINES", NOMBRE),
    "alarmes": ("ALARMES", NOMBRE),
    # conteneurs et supervision
    "erreurs": ("EN ERREUR", NOMBRE),
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
    # onduleur
    "batterie": ("BATTERIE", POURCENT),
    "autonomie": ("AUTONOMIE", LIBRE),
    "charge_ups": ("CHARGE", POURCENT),
    "alim": ("ALIMENTATION", LIBRE),
    "tension": ("TENSION", LIBRE),
    "puissance": ("PUISSANCE", LIBRE),
    # fichiers
    "photos": ("PHOTOS", NOMBRE),
    "videos": ("VIDÉOS", NOMBRE),
    "documents": ("DOCUMENTS", NOMBRE),
    "fichiers": ("FICHIERS", NOMBRE),
    "utilisateurs": ("UTILISATEURS", NOMBRE),
}

# Forme au singulier, quand elle differe. « 1 lectures » se voit tout de suite,
# et se lit comme une interface inachevee. Seules les mesures qui comptent des
# choses figurent ici : un pourcentage ou un debit ne s accordent pas.
#
# Ecrite plutot que devinee : retirer le « s » final marcherait sur cette liste,
# mais pas sur le premier libelle invariable qu on ajouterait (« temps »,
# « acces »), et la faute serait alors invisible jusqu a ce qu un utilisateur la
# voie. Le test verifie qu aucun libelle pluriel n a ete oublie ici.
SINGULIER = {
    "lectures": "LECTURE",
    "spectateurs": "SPECTATEUR",
    "transcodages": "TRANSCODAGE",
    "bibliotheques": "BIBLIOTHÈQUE",
    "series": "SÉRIE",
    "films": "FILM",
    "artistes": "ARTISTE",
    "auteurs": "AUTEUR",
    "episodes": "ÉPISODE",
    "indexeurs": "INDEXEUR",
    "titres": "TITRE",
    "livres": "LIVRE",
    "depots": "DÉPÔT",
    "tableaux": "TABLEAU",
    "objets": "OBJET",
    "hotes": "HÔTE",
    "evenements": "ÉVÉNEMENT",
    "grappes": "GRAPPE",
    "cameras": "CAMÉRA",
    "detections": "DÉTECTION",
    "dossiers": "DOSSIER",
    "appareils": "APPAREIL",
    "machines": "MACHINE",
    "alarmes": "ALARME",
    "approuvees": "APPROUVÉE",
    "disponibles": "DISPONIBLE",
    "requetes": "REQUÊTE",
    "bloquees": "BLOQUÉE",
    "clients": "CLIENT",
    "equipements": "ÉQUIPEMENT",
    "bornes": "BORNE WIFI",
    "actifs": "ACTIF",
    "arretes": "ARRÊTÉ",
    "lumieres": "LUMIÈRE",
    "ouvertures": "OUVERTURE",
    "presents": "PRÉSENT",
    "photos": "PHOTO",
    "videos": "VIDÉO",
    "documents": "DOCUMENT",
    "fichiers": "FICHIER",
    "utilisateurs": "UTILISATEUR",
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

# Certaines grandeurs se jugent a l envers : une batterie ne devient inquietante
# qu en descendant. Ecrit dans une table separee plutot qu avec un signe ou une
# convention a retenir — la regle se lit telle qu on la dirait.
SEUILS_BAS = {
    "batterie": ((10, "critique"), (25, "avertissement"), (50, "surveillance")),
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
    mesure = {
        "id": ident,
        "lab": libelle,
        "num": num,
        "val": libre if libre is not None else texte(nature, num),
    }
    # « lab » reste la forme de reference — c est elle qui sert de cle aux
    # masques — et « lab1 » n apparait que la ou l accord existe.
    if ident in SINGULIER:
        mesure["lab1"] = SINGULIER[ident]
    # « hist » dit a l interface qu une serie existe pour cette mesure : elle
    # n a pas a connaitre les natures pour savoir ou mene un clic.
    if historisable(ident):
        mesure["hist"] = True
    return mesure


# Ce qu on retient d une mesure dans le temps depend de ce qu elle mesure.
#
# Une occupation ou une temperature se resume par sa moyenne et son pire : on
# veut savoir si le disque est plein souvent, et jusqu ou il est monte. Un
# compteur, lui, ne se moyenne pas utilement — « 1 386 episodes manquants en
# moyenne » n apprend rien que « entre 1 386 et 1 452 » ne dise mieux.
#
# Les mesures a texte libre (un uptime, un « 23 / 24 ») n ont pas de serie : leur
# nombre ne veut rien dire hors de leur phrase.
AGREGATS = {
    POURCENT: ("moy", "max"),
    TEMPERATURE: ("moy", "max"),
    DEBIT_MB: ("moy", "max"),
    DEBIT_OCTETS: ("moy", "max"),
    NOMBRE: ("moy", "min", "max"),
    LIBRE: (),
}


def nature(ident):
    return METRIQUES.get(ident, (ident.upper(), NOMBRE))[1]


def agregats(ident):
    """Les resumes qui ont un sens pour cette mesure."""
    return AGREGATS.get(nature(ident), ())


def historisable(ident):
    """Cette mesure merite-t-elle une serie dans le temps ?"""
    return bool(agregats(ident))


def niveau(ident, num):
    """Gravite atteinte par cette metrique, ou None si elle n est pas surveillee."""
    if num is None:
        return None
    for limite, grav in SEUILS.get(ident) or ():
        if num >= limite:
            return grav
    for limite, grav in SEUILS_BAS.get(ident) or ():
        if num <= limite:
            return grav
    return None


def surveille(ident):
    """Cette metrique a-t-elle une regle, dans un sens ou dans l autre ?"""
    return ident in SEUILS or ident in SEUILS_BAS


def libelle(ident):
    return METRIQUES.get(ident, (ident.upper(), NOMBRE))[0]
