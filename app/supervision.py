"""Atrium — supervision et alertes.

Le serveur sonde les services en continu : joignabilite, temps de reponse,
seuils de remplissage, mises a jour. Il en deduit des alertes de niveau
croissant. Le navigateur ne fait plus que les afficher.

Niveaux : info < surveillance < avertissement < critique
"""
import collections
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import metriques
import reseau

NIVEAUX = {"info": 0, "surveillance": 1, "avertissement": 2, "critique": 3}

# Deux echecs consecutifs avant d alerter : une coupure d une seule sonde est
# du bruit, pas une panne.
ECHECS_AVANT_ALERTE = 2

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

_etats = {}                       # nom -> etat courant du service
_historique = collections.defaultdict(lambda: collections.deque(maxlen=40))
_alertes = {}                     # cle -> alerte
_lock = threading.Lock()


def _sonder(url, delai=6):
    """(joignable, latence en ms). Un code HTTP, meme 401 ou 403, prouve que le
    service repond : c est sa disponibilite qu on mesure, pas le droit d y
    acceder."""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    # Les URL viennent de la configuration : rien ne garantit qu'elles visent
    # le reseau local. Sonder au-dela ferait d'Atrium un scanner a distance.
    if not reseau.autorise(url):
        return False, None
    # perf_counter et non monotonic : sous Windows ce dernier est cadence a
    # 15,6 ms, ce qui ecraserait toutes les mesures locales a 0 ou 16 ms.
    debut = time.perf_counter()
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "Atrium")
    try:
        with urllib.request.urlopen(req, timeout=delai, context=_CTX) as r:
            r.read(1)
    except urllib.error.HTTPError:
        pass
    except Exception:
        return False, None
    return True, max(1, round((time.perf_counter() - debut) * 1000))


def sonder_apps(apps):
    """Sonde toutes les applications en parallele : une dizaine de services
    injoignables ne doit pas allonger le cycle a plusieurs minutes."""
    cibles = [(a.get("nom"), (a.get("url") or "").strip())
              for a in apps if a.get("nom") and (a.get("url") or "").strip()]
    if not cibles:
        return {}
    with ThreadPoolExecutor(max_workers=min(12, len(cibles))) as pool:
        mesures = list(pool.map(lambda c: _sonder(c[1]), cibles))

    maintenant = time.time()
    with _lock:
        vivants = {nom for nom, _ in cibles}
        for perdu in [n for n in _etats if n not in vivants]:
            _etats.pop(perdu, None)
            _historique.pop(perdu, None)
        for (nom, _url), (joignable, latence) in zip(cibles, mesures):
            ancien = _etats.get(nom) or {}
            if latence is not None:
                _historique[nom].append((maintenant, latence))
            lat = [v for _, v in _historique[nom]]
            _etats[nom] = {
                "en_ligne": joignable,
                "latence_ms": latence,
                "latence_moy": round(sum(lat) / len(lat)) if lat else None,
                "vu_a": maintenant,
                # « depuis » ne repart de zero que lorsque l etat bascule
                "depuis": maintenant if ancien.get("en_ligne") != joignable
                          else ancien.get("depuis", maintenant),
                "echecs": 0 if joignable else ancien.get("echecs", 0) + 1,
            }
        return {k: dict(v) for k, v in _etats.items()}


def sonder_un(app):
    """Resonde une seule application, sans attendre le cycle suivant."""
    nom, url = app.get("nom"), (app.get("url") or "").strip()
    if not nom or not url:
        return None
    joignable, latence = _sonder(url)
    maintenant = time.time()
    with _lock:
        ancien = _etats.get(nom) or {}
        if latence is not None:
            _historique[nom].append((maintenant, latence))
        lat = [v for _, v in _historique[nom]]
        _etats[nom] = {
            "en_ligne": joignable,
            "latence_ms": latence,
            "latence_moy": round(sum(lat) / len(lat)) if lat else None,
            "vu_a": maintenant,
            "depuis": maintenant if ancien.get("en_ligne") != joignable
                      else ancien.get("depuis", maintenant),
            "echecs": 0 if joignable else ancien.get("echecs", 0) + 1,
        }
        return dict(_etats[nom])


def etats():
    with _lock:
        return {k: dict(v) for k, v in _etats.items()}


def historique(nom):
    with _lock:
        return list(_historique.get(nom) or ())


# --- alertes -----------------------------------------------------------------

def _poser(cle, niveau, service, code, message, param=None):
    """Cree ou actualise une alerte. Son anciennete est conservee tant que le
    probleme dure ; elle ne redevient « non lue » qu en cas d aggravation.

    « code » et « param » permettent a l interface de traduire le libelle ;
    « message » reste la formulation par defaut, cote serveur."""
    with _lock:
        exist = _alertes.get(cle)
        if exist and exist["niveau"] == niveau and exist["message"] == message:
            return
        aggrave = not exist or NIVEAUX.get(niveau, 0) > NIVEAUX.get(exist["niveau"], 0)
        _alertes[cle] = {
            "cle": cle,
            "niveau": niveau,
            "service": service,
            "code": code,
            "param": param or {},
            "message": message,
            "depuis": (exist or {}).get("depuis") or time.time(),
            "lue": False if aggrave else (exist or {}).get("lue", False),
        }


def _lever(cle):
    with _lock:
        _alertes.pop(cle, None)


def _evaluer_tuiles(tuiles):
    """Applique les seuils declares dans metriques.SEUILS.

    Aucune analyse de texte : chaque mesure porte son identifiant et son
    nombre. Surveiller une nouvelle grandeur ne demande plus de toucher a ce
    code, seulement d ajouter une ligne au registre.
    """
    vus = set()
    for service, mesures in (tuiles or {}).items():
        for m in mesures:
            ident, num = m.get("id"), m.get("num")
            grav = metriques.niveau(ident, num)
            if grav is None and ident not in metriques.SEUILS:
                continue
            cle = "seuil:%s:%s" % (service, ident)
            vus.add(cle)
            if grav:
                # code « seuil » et non l identifiant : l interface a une
                # seule formulation a traduire, quelle que soit la grandeur
                _poser(cle, grav, service, "seuil",
                       "%s : %s" % (metriques.libelle(ident), m.get("val")),
                       {"val": m.get("val"), "lab": metriques.libelle(ident),
                        "metrique": ident})
            else:
                _lever(cle)
    for cle in [c for c in list(_alertes) if c.startswith("seuil:")]:
        if cle not in vus:
            _lever(cle)


def evaluer(apps, hote, maj, erreurs_integration, tuiles=None):
    """Recalcule l ensemble des alertes a partir de ce que le serveur sait."""
    noms = {a.get("nom") for a in apps if a.get("nom")}

    for nom, e in etats().items():
        cle = "hors-ligne:" + nom
        if nom in noms and not e["en_ligne"] and e["echecs"] >= ECHECS_AVANT_ALERTE:
            _poser(cle, "avertissement", nom, "hors_ligne", "Service injoignable")
        else:
            _lever(cle)

    if hote and hote.get("disponible"):
        nom = hote.get("nom") or "Serveur"
        for champ, ident, texte in (("disque", "disque", "Stockage occupé à %d %%"),
                                    ("memoire", "ram", "Mémoire occupée à %d %%")):
            pc = (hote.get(champ) or {}).get("pourcent")
            grav = metriques.niveau(ident, pc)
            if grav:
                _poser(ident, grav, nom, ident, texte % pc, {"pc": pc})
            else:
                _lever(ident)

    for nom, msg in (erreurs_integration or {}).items():
        cle = "integration:" + nom
        if msg and nom in noms:
            _poser(cle, "surveillance", nom, "cle", msg)
        else:
            _lever(cle)
    for cle in [c for c in list(_alertes) if c.startswith("integration:")]:
        if cle.split(":", 1)[1] not in (erreurs_integration or {}):
            _lever(cle)

    if maj:
        _poser("maj", "info", "Mises à jour", "maj",
               "%d mise%s à jour disponible%s" % (maj, "s" if maj > 1 else "",
                                                  "s" if maj > 1 else ""),
               {"n": maj})
    else:
        _lever("maj")

    _evaluer_tuiles(tuiles)
    return alertes()


def alertes():
    with _lock:
        liste = [dict(a) for a in _alertes.values()]
    liste.sort(key=lambda a: (-NIVEAUX.get(a["niveau"], 0), a["depuis"]))
    return liste


def marquer_lues():
    with _lock:
        for a in _alertes.values():
            a["lue"] = True


def resume():
    """Ce qu affiche la cloche : nombre d alertes, non lues, niveau le plus
    grave rencontre."""
    liste = alertes()
    pire = max((NIVEAUX.get(a["niveau"], 0) for a in liste), default=-1)
    inverse = {v: k for k, v in NIVEAUX.items()}
    return {
        "total": len(liste),
        "non_lues": sum(1 for a in liste if not a.get("lue")),
        "problemes": sum(1 for a in liste if NIVEAUX.get(a["niveau"], 0) >= 1),
        "niveau": inverse.get(pire, ""),
        "alertes": liste,
    }
