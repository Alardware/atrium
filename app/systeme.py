"""Atrium — mesures de la machine hote.

Lues directement depuis /proc et le systeme de fichiers : aucune dependance, et
surtout aucune integration tierce a installer. Dans un conteneur Docker, /proc
reflete l hote (Unraid, Synology, Raspberry Pi...), ce sont donc bien les
ressources du serveur qui heberge Atrium qui sont mesurees.

Sous Windows (developpement), /proc n existe pas : « disponible » vaut False et
l interface masque simplement la carte.
"""
import os
import threading
import time

_precedent = {"total": 0, "actif": 0}
_cpu_dernier = {"valeur": None}


def _lire(chemin):
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def cpu_pourcent():
    """Charge CPU entre cet appel et le precedent (premier appel : None)."""
    ligne = ""
    for l in _lire("/proc/stat").splitlines():
        if l.startswith("cpu "):
            ligne = l
            break
    if not ligne:
        return None
    champs = [int(x) for x in ligne.split()[1:]]
    total = sum(champs)
    repos = champs[3] + (champs[4] if len(champs) > 4 else 0)  # idle + iowait
    actif = total - repos
    dt = total - _precedent["total"]
    da = actif - _precedent["actif"]
    premier = _precedent["total"] == 0
    _precedent["total"], _precedent["actif"] = total, actif
    if premier or dt <= 0:
        return None          # il faut deux releves pour calculer une charge
    return max(0, min(100, round(da * 100 / dt)))


def memoire():
    txt = _lire("/proc/meminfo")
    if not txt:
        return None
    v = {}
    for l in txt.splitlines():
        p = l.split(":")
        if len(p) == 2:
            v[p[0]] = p[1].strip().split()[0]
    try:
        total = int(v["MemTotal"])
        dispo = int(v.get("MemAvailable", v.get("MemFree", 0)))
    except (KeyError, ValueError):
        return None
    if total <= 0:
        return None
    return {
        "pourcent": round((total - dispo) * 100 / total),
        "utilise_go": round((total - dispo) / 1048576, 1),
        "total_go": round(total / 1048576, 1),
    }


def disque(chemin):
    """Occupation du volume qui porte la configuration."""
    try:
        s = os.statvfs(chemin)
    except (OSError, AttributeError):
        return None
    total = s.f_blocks * s.f_frsize
    libre = s.f_bavail * s.f_frsize
    if total <= 0:
        return None
    utilise = total - libre
    return {
        "pourcent": round(utilise * 100 / total),
        "utilise_go": round(utilise / 1073741824, 1),
        "total_go": round(total / 1073741824, 1),
    }


def demarrage():
    txt = _lire("/proc/uptime")
    if not txt:
        return None
    try:
        secondes = float(txt.split()[0])
    except (ValueError, IndexError):
        return None
    return {"secondes": int(secondes), "depuis": int(time.time() - secondes)}


def nom_hote():
    return (os.environ.get("HOST_HOSTNAME")      # renseigne par Unraid
            or _lire("/etc/host_hostname").strip()
            or os.environ.get("HOSTNAME", "").strip()
            or "serveur")


def systeme_hote():
    return os.environ.get("HOST_OS", "").strip()  # « Unraid », etc.


def _echantillonner():
    """Releve la charge en continu : une valeur est prete des le premier
    affichage, au lieu d attendre deux appels de l interface."""
    while True:
        v = cpu_pourcent()
        if v is not None:
            _cpu_dernier["valeur"] = v
        time.sleep(3)


def demarrer_echantillonnage():
    if not os.path.exists("/proc/stat"):
        return
    threading.Thread(target=_echantillonner, daemon=True).start()


def mesures(chemin_config):
    """Instantane complet ; disponible=False si la plateforme ne l expose pas."""
    cpu = _cpu_dernier["valeur"]
    mem = memoire()
    dsk = disque(chemin_config)
    if cpu is None and mem is None and dsk is None:
        return {"disponible": False}
    return {
        "disponible": True,
        "nom": nom_hote(),
        "os": systeme_hote(),
        "cpu": cpu,
        "memoire": mem,
        "disque": dsk,
        "demarrage": demarrage(),
    }
