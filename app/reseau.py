"""Atrium — controle des destinations joignables.

Le serveur relaie des appels vers les services de l utilisateur. Sans garde,
il devient un rebond : on lui fait demander n importe quelle adresse, y compris
sur Internet ou sur des interfaces internes qu il est le seul a pouvoir
atteindre.

Le filtrage se fait sur l adresse resolue, jamais sur le texte du nom d hote :
« 10.evil.com » commence par « 10. » sans etre pour autant sur le reseau local.
"""
import ipaddress
import os
import socket
import urllib.parse

# Prefixes supplementaires, pour les installations qui utilisent des noms
# d hotes internes. Une variable definie mais vide vaut « non renseignee ».
_extra = (os.environ.get("ATRIUM_ALLOW_NET") or "").strip()
NOMS_AUTORISES = tuple(h.strip().lower() for h in _extra.split(",") if h.strip())

_cache = {}
DUREE_CACHE = 60


def _prive(ip):
    """Vrai si l adresse appartient au reseau local ou a la boucle locale.

    Les adresses de lien local sont exclues : c est la ou se trouvent les
    services de metadonnees des hebergeurs (169.254.169.254), qui n ont rien
    a faire dans un tableau de bord domestique.
    """
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # La boucle locale se juge en premier : en IPv6, ::1 appartient a ::/8, que
    # Python classe « reserve ». Le test suivant la rejetterait a tort.
    if a.is_loopback:
        return True
    if a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified:
        return False
    # IPv6 mappant une adresse IPv4 : on juge l adresse reelle
    if getattr(a, "ipv4_mapped", None):
        return _prive(str(a.ipv4_mapped))
    return a.is_private


def resoudre(hote):
    """Adresses derriere un nom d hote, ou liste vide si la resolution echoue."""
    cle = hote.lower()
    entree = _cache.get(cle)
    maintenant = __import__("time").time()
    if entree and maintenant - entree[0] < DUREE_CACHE:
        return entree[1]
    try:
        infos = socket.getaddrinfo(hote, None, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
    except (socket.gaierror, UnicodeError, ValueError):
        ips = []
    _cache[cle] = (maintenant, ips)
    return ips


def autorise(url):
    """Vrai si l URL vise le reseau local. Toutes les adresses derriere le nom
    doivent l etre : une seule adresse publique suffit a refuser."""
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    hote = (p.hostname or "").strip()
    if not hote:
        return False
    if hote.lower() in NOMS_AUTORISES:
        return True
    # adresse litterale : pas de resolution necessaire
    try:
        ipaddress.ip_address(hote)
        return _prive(hote)
    except ValueError:
        pass
    ips = resoudre(hote)
    return bool(ips) and all(_prive(ip) for ip in ips)


def adresse_epinglee(url):
    """(url visant l adresse resolue, nom d hote d origine).

    On se connecte a l adresse validee plutot qu au nom : entre la
    verification et la connexion, une reponse DNS ne peut plus changer de
    cible. L en-tete Host garde le nom, que le service attend souvent.
    """
    p = urllib.parse.urlparse(url)
    hote = p.hostname or ""
    try:
        ipaddress.ip_address(hote)
        return url, None            # deja une adresse : rien a epingler
    except ValueError:
        pass
    ips = [i for i in resoudre(hote) if _prive(i)]
    if not ips:
        return None, None
    ip = ips[0]
    litteral = "[%s]" % ip if ":" in ip else ip
    netloc = litteral + (":%d" % p.port if p.port else "")
    return urllib.parse.urlunparse(p._replace(netloc=netloc)), p.netloc
