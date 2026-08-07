"""Atrium — authentification.

Les mots de passe sont derives avec PBKDF2-HMAC-SHA256 (sel aleatoire par
utilisateur, 240 000 iterations). Le mot de passe en clair ne quitte jamais la
requete de connexion et n'est jamais ecrit sur le disque.

Une session valide est un jeton aleatoire de 32 octets, conserve cote serveur et
transmis au navigateur dans un cookie HttpOnly (donc illisible par JavaScript,
ce qui neutralise le vol de session par injection de script).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

ITERATIONS = 240_000
SESSION_TTL = 30 * 24 * 3600      # 30 jours
SESSION_TTL_COURT = 12 * 3600     # sans « se souvenir de moi »
COOKIE = "atrium_session"


def hacher(motdepasse, sel=None):
    """Retourne 'pbkdf2$iterations$sel$empreinte' (base64)."""
    if sel is None:
        sel = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", motdepasse.encode("utf-8"), sel, ITERATIONS)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return "pbkdf2$%d$%s$%s" % (ITERATIONS, b64(sel), b64(dk))


def verifier(motdepasse, stocke):
    """Comparaison a temps constant : ne fuit pas d'information par la duree."""
    if not stocke or not motdepasse:
        return False
    try:
        algo, iters, sel_b64, dk_b64 = stocke.split("$")
        if algo != "pbkdf2":
            return False
        sel = base64.b64decode(sel_b64)
        attendu = base64.b64decode(dk_b64)
        calcule = hashlib.pbkdf2_hmac("sha256", motdepasse.encode("utf-8"), sel, int(iters))
        return hmac.compare_digest(calcule, attendu)
    except (ValueError, TypeError):
        return False


class Sessions:
    """Sessions en memoire, persistees pour survivre a un redemarrage."""

    def __init__(self, chemin):
        self.chemin = chemin
        self.lock = threading.Lock()
        self.data = {}
        self._charger()

    def _charger(self):
        try:
            with open(self.chemin, "r", encoding="utf-8") as f:
                brut = json.load(f)
            maintenant = time.time()
            self.data = {k: v for k, v in brut.items() if v.get("exp", 0) > maintenant}
        except (OSError, ValueError):
            self.data = {}

    def _sauver(self):
        try:
            tmp = self.chemin + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.chemin)
            try:
                os.chmod(self.chemin, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    def creer(self, utilisateur, longue=False):
        jeton = secrets.token_urlsafe(32)
        with self.lock:
            self.data[jeton] = {
                "user": utilisateur,
                "exp": time.time() + (SESSION_TTL if longue else SESSION_TTL_COURT),
            }
            self._purger()
            self._sauver()
        return jeton

    def lire(self, jeton):
        if not jeton:
            return None
        with self.lock:
            s = self.data.get(jeton)
            if not s:
                return None
            if s["exp"] < time.time():
                self.data.pop(jeton, None)
                self._sauver()
                return None
            return s["user"]

    def supprimer(self, jeton):
        with self.lock:
            if self.data.pop(jeton, None) is not None:
                self._sauver()

    def supprimer_utilisateur(self, nom):
        """Revoque toutes les sessions d'un utilisateur (suppression, changement
        de mot de passe)."""
        with self.lock:
            avant = len(self.data)
            self.data = {k: v for k, v in self.data.items() if v.get("user") != nom}
            if len(self.data) != avant:
                self._sauver()

    def _purger(self):
        maintenant = time.time()
        self.data = {k: v for k, v in self.data.items() if v.get("exp", 0) > maintenant}


class Limiteur:
    """Limite les tentatives de connexion : 8 essais par tranche de 5 minutes."""

    def __init__(self, essais=8, fenetre=300):
        self.essais = essais
        self.fenetre = fenetre
        self.lock = threading.Lock()
        self.hist = {}

    def autorise(self, cle):
        maintenant = time.time()
        with self.lock:
            t = [x for x in self.hist.get(cle, []) if maintenant - x < self.fenetre]
            self.hist[cle] = t
            return len(t) < self.essais

    def echec(self, cle):
        with self.lock:
            self.hist.setdefault(cle, []).append(time.time())

    def reussite(self, cle):
        with self.lock:
            self.hist.pop(cle, None)
