"""Atrium — prevenir, quand quelque chose change vraiment.

Un tableau de bord qui surveille sans jamais rien dire ne sert qu a celui qui
le regarde. Ce module envoie une notification quand un service tombe, revient,
ou franchit un seuil — et seulement alors.

Trois regles tiennent l ensemble :

  1. On ne previent que sur une bascule. Le cycle passe toutes les trente
     secondes ; repeter « toujours hors ligne » quarante fois par heure ferait
     couper les notifications par leur destinataire, ce qui revient a ne plus
     etre prevenu du tout.
  2. Un service qui clignote est mis en sourdine. Passe quelques bascules
     rapprochees, on se tait sur celui-la pendant un quart d heure.
  3. L adresse est celle que l utilisateur a donnee, et rien d autre. Elle peut
     etre locale — un ntfy sur le NAS — ou publique — Discord. C est le seul
     endroit d Atrium qui sort du reseau local, et il faut l avoir demande.

Le corps envoye depend du service reconnu a son adresse : Discord, ntfy,
Gotify, ou un objet JSON generique.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 8
# Sourdine : au-dela de cette cadence, un service est juge instable.
FENETRE_CLIGNOTEMENT = 600
BASCULES_AVANT_SOURDINE = 4
DUREE_SOURDINE = 900

_verrou = threading.Lock()
_recent = {}          # service -> [instants de bascule]
_sourdine = {}        # service -> instant de fin de sourdine
_dernier = {"envoi": 0, "erreur": ""}


def _format_de(url):
    """Le service vise, devine a son adresse.

    Discord et ntfy ont chacun leur forme ; le reste recoit un objet JSON, que
    n importe quel receveur sait lire.
    """
    u = (url or "").lower()
    if "discord.com/api/webhooks" in u or "discordapp.com/api/webhooks" in u:
        return "discord"
    if "/message?token=" in u or "gotify" in u:
        return "gotify"
    if "ntfy" in u:
        return "ntfy"
    return "json"


def corps(url, titre, texte, niveau):
    """(corps, en-tetes) pour ce receveur."""
    forme = _format_de(url)
    if forme == "discord":
        # Une couleur par gravite : Discord la peint sur le bord gauche.
        teinte = {"critique": 0xF0685A, "avertissement": 0xF5A524}.get(niveau, 0x5B8CFF)
        return json.dumps({"embeds": [{"title": titre, "description": texte,
                                       "color": teinte}]}).encode(), \
            {"Content-Type": "application/json"}
    if forme == "ntfy":
        # ntfy lit le titre et la priorite dans les en-tetes, le texte en corps.
        priorite = {"critique": "urgent", "avertissement": "high"}.get(niveau, "default")
        return texte.encode("utf-8"), {
            "Content-Type": "text/plain; charset=utf-8",
            "Title": titre.encode("ascii", "replace").decode("ascii"),
            "Priority": priorite,
            "Tags": {"critique": "rotating_light", "avertissement": "warning"}
                    .get(niveau, "information_source"),
        }
    if forme == "gotify":
        priorite = {"critique": 8, "avertissement": 5}.get(niveau, 2)
        return json.dumps({"title": titre, "message": texte,
                           "priority": priorite}).encode(), \
            {"Content-Type": "application/json"}
    return json.dumps({"titre": titre, "texte": texte, "niveau": niveau,
                       "source": "atrium", "instant": int(time.time())},
                      ensure_ascii=False).encode(), \
        {"Content-Type": "application/json"}


def envoyer(url, titre, texte, niveau="info"):
    """Envoie une notification. Rend (ok, motif).

    Aucune reponse du receveur n est rendue ailleurs qu ici : Atrium ne doit
    pas devenir un moyen de lire, depuis le navigateur, ce qu une adresse
    quelconque repond.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False, "adresse invalide"
    donnees, entetes = corps(url, titre, texte, niveau)
    req = urllib.request.Request(url, data=donnees, method="POST")
    req.add_header("User-Agent", "Atrium")
    for k, v in entetes.items():
        req.add_header(k, v)
    try:
        # Schema deja valide ci-dessus : http ou https, rien d autre. C est la
        # seule sortie deliberee vers Internet, et sa reponse ne remonte pas au
        # navigateur — seulement le code HTTP, ici.
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # nosec B310
            code = r.status
            r.read(2000)
    except urllib.error.HTTPError as e:
        with _verrou:
            _dernier["erreur"] = "HTTP %s" % e.code
        return False, "HTTP %s" % e.code
    except Exception as e:                       # reseau, DNS, delai
        with _verrou:
            _dernier["erreur"] = str(e)[:120]
        return False, str(e)[:120]
    with _verrou:
        _dernier["envoi"] = time.time()
        _dernier["erreur"] = ""
    return True, str(code)


def _instable(service):
    """Ce service bascule-t-il trop souvent pour qu on en parle encore ?"""
    maintenant = time.time()
    with _verrou:
        if _sourdine.get(service, 0) > maintenant:
            return True
        vus = [t for t in _recent.get(service, []) if maintenant - t < FENETRE_CLIGNOTEMENT]
        vus.append(maintenant)
        _recent[service] = vus
        if len(vus) > BASCULES_AVANT_SOURDINE:
            _sourdine[service] = maintenant + DUREE_SOURDINE
            return True
    return False


def reglages(cfg):
    """Les reglages de notification, avec leurs valeurs par defaut."""
    n = (cfg or {}).get("notif")
    if not isinstance(n, dict):
        n = {}
    return {
        "actif": bool(n.get("actif")) and bool((n.get("url") or "").strip()),
        "url": (n.get("url") or "").strip(),
        # Ce dont on veut etre prevenu. Le retour en ligne n est pas coche par
        # defaut : savoir qu un service est retombe interesse plus que de
        # savoir qu il s est releve.
        "hors_ligne": n.get("hors_ligne", True),
        "retour": n.get("retour", False),
        "seuil": n.get("seuil", True),
    }


def etat():
    """De quoi afficher l etat du dernier envoi."""
    with _verrou:
        return {"dernier": _dernier["envoi"], "erreur": _dernier["erreur"],
                "sourdine": [s for s, fin in _sourdine.items() if fin > time.time()]}


def sur_evenement(cfg, service, code, param=None):
    """Prevenir, si ce genre d evenement interesse l utilisateur.

    « service » est le nom lisible de la fiche, pas sa clef : c est ce que
    l utilisateur lira sur son telephone.
    """
    r = reglages(cfg)
    if not r["actif"]:
        return False
    param = param or {}
    if code == "hors_ligne" and not r["hors_ligne"]:
        return False
    if code == "en_ligne" and not r["retour"]:
        return False
    if code in ("seuil", "seuil_fin") and not r["seuil"]:
        return False
    if code not in ("hors_ligne", "en_ligne", "seuil", "seuil_fin"):
        return False
    if _instable(service):
        return False

    if code == "hors_ligne":
        titre, texte, niveau = ("Atrium — %s ne répond plus" % service,
                                "%s est injoignable." % service, "critique")
    elif code == "en_ligne":
        titre, texte, niveau = ("Atrium — %s est revenu" % service,
                                "%s répond de nouveau." % service, "info")
    elif code == "seuil":
        mesure = param.get("metrique") or "une mesure"
        niveau_seuil = param.get("niveau") or "avertissement"
        titre = "Atrium — %s : seuil franchi" % service
        texte = "%s : %s au-delà du seuil (%s)." % (service, mesure, niveau_seuil)
        niveau = "critique" if niveau_seuil == "critique" else "avertissement"
    else:
        mesure = param.get("metrique") or "une mesure"
        titre = "Atrium — %s : rentré dans l'ordre" % service
        texte = "%s : %s est revenue sous son seuil." % (service, mesure)
        niveau = "info"
    ok, _ = envoyer(r["url"], titre, texte, niveau)
    return ok
