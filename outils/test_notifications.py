"""Prevenir quand quelque chose change, et se taire le reste du temps.

Un receveur de notifications est dresse pour la duree du test ; on lui fait
raconter ce qu il a recu. Ce qui est verifie :

  1. la forme du message suit le receveur — Discord, ntfy, Gotify, ou JSON ;
  2. seules les bascules demandees partent ;
  3. un service qui clignote est mis en sourdine, sans quoi la notification
     devient un bruit qu on finit par couper ;
  4. rien ne part quand l utilisateur n a rien demande.

Sortie : 0 si tout tient, 1 sinon.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import notifications  # noqa: E402

ECHECS = []
RECUS = []


def verifier(titre, obtenu, attendu):
    ok = obtenu == attendu
    print("    %-42s %-24s %s" % (titre, obtenu, "" if ok else "!!! attendu %s" % (attendu,)))
    if not ok:
        ECHECS.append(titre)


class Receveur(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        # Un receveur qui renvoie ailleurs : Atrium ne doit pas l y suivre.
        if self.path.startswith("/renvoi"):
            # Le corps se lit meme si l on n en fait rien : sans cela, la
            # connexion est coupee au milieu de l envoi et le client verrait
            # une panne reseau la ou il doit voir une redirection.
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/interne")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        taille = int(self.headers.get("Content-Length") or 0)
        RECUS.append({"chemin": self.path,
                      "corps": self.rfile.read(taille).decode("utf-8", "replace"),
                      "entetes": {k.lower(): v for k, v in self.headers.items()}})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def demarrer():
    s = ThreadingHTTPServer(("127.0.0.1", 0), Receveur)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.server_address[1]


def remise_a_zero():
    RECUS.clear()
    notifications._recent.clear()
    notifications._sourdine.clear()


def main():
    srv, url = demarrer()
    tout = {"notif": {"actif": True, "url": url, "hors_ligne": True,
                      "retour": True, "seuil": True}}
    try:
        print("1. la forme du message suit le receveur")
        for adresse, attendu in (("https://discord.com/api/webhooks/1/x", "discord"),
                                 ("https://ntfy.sh/maison", "ntfy"),
                                 ("http://nas:8080/message?token=x", "gotify"),
                                 ("http://maison/crochet", "json")):
            verifier(adresse.split("/")[2], notifications._format_de(adresse), attendu)
        corps, entetes = notifications.corps("https://ntfy.sh/x", "T", "Texte", "critique")
        verifier("ntfy : priorite dans l en-tete", entetes.get("Priority"), "urgent")
        corps, _ = notifications.corps("https://discord.com/api/webhooks/1/x",
                                       "T", "Texte", "critique")
        verifier("discord : teinte de gravite",
                 json.loads(corps)["embeds"][0]["color"], 0xF0685A)

        print("2. seules les bascules demandees partent")
        remise_a_zero()
        notifications.sur_evenement(tout, "Plex", "hors_ligne")
        notifications.sur_evenement(tout, "Plex", "en_ligne")
        notifications.sur_evenement(tout, "Nébuleuse", "seuil",
                                    {"metrique": "disque", "niveau": "critique"})
        notifications.sur_evenement(tout, "Plex", "suivi")      # jamais notifie
        verifier("messages envoyes", len(RECUS), 3)
        verifier("le nom de la fiche est dedans",
                 "Nébuleuse" in RECUS[2]["corps"], True)

        sans_retour = {"notif": dict(tout["notif"], retour=False, seuil=False)}
        remise_a_zero()
        notifications.sur_evenement(sans_retour, "Plex", "en_ligne")
        notifications.sur_evenement(sans_retour, "Plex", "seuil", {"metrique": "ram"})
        notifications.sur_evenement(sans_retour, "Plex", "hors_ligne")
        verifier("retour et seuils decoches", len(RECUS), 1)

        print("3. un service qui clignote finit en sourdine")
        remise_a_zero()
        for _ in range(8):
            notifications.sur_evenement(tout, "UniFi", "hors_ligne")
        verifier("messages pour un service instable", len(RECUS),
                 notifications.BASCULES_AVANT_SOURDINE)
        verifier("service mis en sourdine",
                 "UniFi" in notifications.etat()["sourdine"], True)
        notifications.sur_evenement(tout, "Plex", "hors_ligne")
        verifier("les autres passent encore", len(RECUS),
                 notifications.BASCULES_AVANT_SOURDINE + 1)

        print("4. rien ne part sans demande")
        remise_a_zero()
        eteint = {"notif": dict(tout["notif"], actif=False)}
        notifications.sur_evenement(eteint, "Plex", "hors_ligne")
        notifications.sur_evenement({}, "Plex", "hors_ligne")
        verifier("aucun message", len(RECUS), 0)

        print("5. une adresse invalide se refuse sans bruit")
        ok, motif = notifications.envoyer("file:///etc/passwd", "T", "T")
        verifier("fichier local refuse", (ok, motif), (False, "adresse invalide"))
        ok, _ = notifications.envoyer("http://127.0.0.1:1/rien", "T", "T")
        verifier("hote injoignable", ok, False)

        print("6. un renvoi n est pas suivi")
        remise_a_zero()
        ok, motif = notifications.envoyer(url + "/renvoi", "T", "T")
        verifier("redirection refusee", (ok, motif), (False, "HTTP 302"))
        verifier("rien n a ete demande ailleurs", len(RECUS), 0)
    finally:
        srv.shutdown()

    print()
    if ECHECS:
        print("RESUME : %d point(s) a corriger" % len(ECHECS))
        for e in ECHECS:
            print("  - " + e)
        return 1
    print("RESUME : on previent sur bascule, et seulement alors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
