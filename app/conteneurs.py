"""Atrium — conteneurs Docker de la machine hote.

Lecture seule, par la socket du demon (/var/run/docker.sock). Le montage est
facultatif : sans lui, la liste est simplement vide et l interface masque le
tableau.

Attention en montant cette socket : elle donne acces au demon Docker, donc a
l equivalent du compte root de la machine. Atrium n y fait que des GET, mais
c est bien le montage qui accorde le pouvoir, pas l usage qu on en fait. A ne
monter que si l on veut la vue des conteneurs.
"""
import http.client
import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor

SOCKET = os.environ.get("ATRIUM_DOCKER_SOCKET", "/var/run/docker.sock")
TIMEOUT = 5
MAX_CONTENEURS = 60


class _ConnexionUnix(http.client.HTTPConnection):
    """http.client ne parle pas aux sockets Unix : on remplace juste l ouverture."""

    def __init__(self, chemin, timeout=TIMEOUT):
        super().__init__("localhost", timeout=timeout)
        self._chemin = chemin

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._chemin)
        self.sock = s


def disponible():
    return os.path.exists(SOCKET)


def _get(chemin):
    c = _ConnexionUnix(SOCKET)
    try:
        c.request("GET", chemin, headers={"Host": "localhost", "Accept": "application/json"})
        r = c.getresponse()
        corps = r.read(4 * 1024 * 1024)
        if r.status != 200:
            return None
        return json.loads(corps.decode("utf-8", "replace"))
    except Exception:
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass


def _pourcent_cpu(s):
    """Formule officielle de Docker : la part du temps processeur consomme par
    le conteneur sur l intervalle, rapportee au nombre de coeurs."""
    try:
        cpu = s["cpu_stats"]
        pre = s.get("precpu_stats") or {}
        d_conteneur = cpu["cpu_usage"]["total_usage"] - (pre.get("cpu_usage") or {}).get("total_usage", 0)
        d_systeme = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        coeurs = cpu.get("online_cpus") or len((cpu["cpu_usage"].get("percpu_usage") or [])) or 1
        if d_systeme <= 0 or d_conteneur < 0:
            return None
        return round(d_conteneur / d_systeme * coeurs * 100, 1)
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def _memoire(s):
    """Docker compte le cache de pages dans « usage » ; on le retire, sinon un
    conteneur qui a beaucoup lu semble saturer la memoire."""
    m = s.get("memory_stats") or {}
    usage = m.get("usage")
    if usage is None:
        return None, None
    detail = m.get("stats") or {}
    cache = detail.get("inactive_file")          # cgroup v2
    if cache is None:
        cache = detail.get("total_inactive_file", detail.get("cache", 0))   # cgroup v1
    return max(0, usage - (cache or 0)), m.get("limit") or None


def _un(c):
    ident = c.get("Id", "")
    nom = (c.get("Names") or ["/?"])[0].lstrip("/")
    etat = c.get("State", "")
    ligne = {
        "nom": nom,
        "image": (c.get("Image") or "").split("@")[0],
        "etat": etat,
        "statut": c.get("Status", ""),
        "cpu": None,
        "memoire": None,
        "memoire_max": None,
    }
    if etat == "running":
        s = _get("/containers/%s/stats?stream=false&one-shot=false" % ident)
        if s:
            ligne["cpu"] = _pourcent_cpu(s)
            ligne["memoire"], ligne["memoire_max"] = _memoire(s)
    return ligne


def liste():
    """Conteneurs et leur consommation. Liste vide si la socket n est pas la."""
    if not disponible():
        return []
    brut = _get("/containers/json?all=1")
    if not isinstance(brut, list):
        return []
    brut = brut[:MAX_CONTENEURS]
    # Chaque mesure est un appel distinct : en serie, trente conteneurs
    # depasseraient le cycle de collecte.
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(brut)))) as pool:
        lignes = list(pool.map(_un, brut))
    lignes.sort(key=lambda l: (l["etat"] != "running", l["nom"].lower()))
    return lignes
