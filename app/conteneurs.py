"""Atrium — conteneurs Docker de la machine hote.

Par la socket du demon (/var/run/docker.sock). Le montage est facultatif :
sans lui, la liste est vide et l interface masque le tableau comme l action de
redemarrage.

Atrium se limite a deux choses : lire l etat et la consommation, et redemarrer
un conteneur a la demande explicite de l utilisateur. Rien ne cree, ne
supprime, ni ne modifie un conteneur.

Attention en montant cette socket : elle donne acces au demon Docker, donc a
l equivalent du compte root de la machine. C est le montage qui accorde ce
pouvoir, pas l usage qu Atrium en fait. A ne monter que si l on veut ces deux
fonctions.
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


def _appel(methode, chemin, delai=TIMEOUT):
    """Retourne (code HTTP, corps decode) ; (0, None) si le demon est muet."""
    c = _ConnexionUnix(SOCKET, timeout=delai)
    try:
        c.request(methode, chemin, headers={"Host": "localhost", "Accept": "application/json"})
        r = c.getresponse()
        corps = r.read(4 * 1024 * 1024)
        if not corps:
            return r.status, None
        try:
            return r.status, json.loads(corps.decode("utf-8", "replace"))
        except ValueError:
            return r.status, corps.decode("utf-8", "replace")
    except Exception:
        return 0, None
    finally:
        try:
            c.close()
        except OSError:
            pass          # deja ferme par la pile reseau : rien a signaler


def _get(chemin):
    code, corps = _appel("GET", chemin)
    return corps if code == 200 else None


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


def _normaliser(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def noms():
    """Nom et etat de chaque conteneur, sans mesurer sa consommation : un seul
    appel, assez leger pour repondre a l ouverture d un menu."""
    if not disponible():
        return []
    brut = _get("/containers/json?all=1")
    if not isinstance(brut, list):
        return []
    return [{"nom": (c.get("Names") or ["/?"])[0].lstrip("/"), "etat": c.get("State", "")}
            for c in brut[:MAX_CONTENEURS]]


def trouver(nom):
    """Identifiant du conteneur correspondant, en ignorant casse et
    ponctuation : « Nginx Proxy Manager » retrouve « nginx-proxy-manager »."""
    brut = _get("/containers/json?all=1")
    if not isinstance(brut, list):
        return None
    cible = _normaliser(nom)
    if not cible:
        return None
    for c in brut:
        for n in (c.get("Names") or []):
            if _normaliser(n.lstrip("/")) == cible:
                return c.get("Id")
    return None


def redemarrer(nom):
    """Redemarre un conteneur designe par son nom. Retourne (ok, message)."""
    if not disponible():
        return False, "La socket Docker n'est pas montée."
    ident = trouver(nom)
    if not ident:
        return False, "Aucun conteneur nommé « %s »." % nom
    # le demon peut prendre du temps a arreter proprement le conteneur
    code, corps = _appel("POST", "/containers/%s/restart?t=10" % ident, delai=45)
    if code == 204:
        return True, ""
    if code == 0:
        return False, "Le démon Docker n'a pas répondu."
    detail = corps.get("message") if isinstance(corps, dict) else (corps or "")
    return False, "Docker a refusé (HTTP %d) %s" % (code, detail or "")


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
