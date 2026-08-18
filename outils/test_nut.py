"""Un onduleur lu par NUT, face a un faux upsd.

Le protocole tient en quelques lignes de texte, ce qui rend la lecture facile
a croire sur parole. Ce test monte donc un vrai serveur qui parle NUT, et
verifie ce qu Atrium en tire : les mesures, l authentification quand elle est
exigee, le refus quand la reponse est un ERR, et l interdiction de sortir du
reseau prive.

Sortie : 0 si l onduleur est lu comme il doit l etre, 1 sinon.
"""
import os
import socket
import socketserver
import sys
import threading

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RACINE, "app"))

import nut                          # noqa: E402
import widgets                      # noqa: E402

ECHECS = []

# Un Eaton 5E 850, sur secteur, batterie pleine.
VARS = {
    "device.mfr": "EATON",
    "device.model": "5E 850i",
    "battery.charge": "100",
    "battery.runtime": "4320",
    "battery.voltage": "13.5",
    "ups.status": "OL",
    "ups.load": "23",
    "ups.realpower.nominal": "480",
    "input.voltage": "233.0",
    "ups.firmware": "01.05",
    # une valeur avec des guillemets internes : le decodage doit tenir
    "ups.serial": 'AB\\"12',
}


def verifier(condition, titre, detail=""):
    print(("  ok    " if condition else "  ECHEC ") + titre + (" — " + detail if detail else ""))
    if not condition:
        ECHECS.append(titre)


class FauxUpsd(socketserver.StreamRequestHandler):
    """upsd, reduit a ce qu Atrium lui demande."""

    exige_compte = False
    vars_ = VARS

    def handle(self):
        identifie = not self.exige_compte
        while True:
            ligne = self.rfile.readline()
            if not ligne:
                return
            cmd = ligne.decode("utf-8", "replace").strip()
            self.server.recu.append(cmd)
            if cmd.startswith("USERNAME "):
                self.wfile.write(b"OK\n")
            elif cmd.startswith("PASSWORD "):
                identifie = True
                self.wfile.write(b"OK\n")
            elif cmd == "LOGOUT":
                self.wfile.write(b"OK Goodbye\n")
                return
            elif not identifie:
                self.wfile.write(b"ERR ACCESS-DENIED\n")
            elif cmd == "LIST UPS":
                self.wfile.write(b'BEGIN LIST UPS\nUPS eaton "Onduleur du garage"\n'
                                 b"END LIST UPS\n")
            elif cmd.startswith("LIST VAR "):
                nom = cmd.split(" ", 2)[2]
                self.wfile.write(("BEGIN LIST VAR %s\n" % nom).encode())
                for k, v in self.vars_.items():
                    self.wfile.write(('VAR %s %s "%s"\n' % (nom, k, v)).encode())
                self.wfile.write(("END LIST VAR %s\n" % nom).encode())
            else:
                self.wfile.write(b"ERR UNKNOWN-COMMAND\n")


def demarrer(handler):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.recu = []
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def main():
    print("\n== un onduleur ouvert a la lecture ==")
    srv, port = demarrer(FauxUpsd)
    try:
        noms = nut.onduleurs("127.0.0.1", port)
        verifier(noms == [("eaton", "Onduleur du garage")], "l onduleur est nomme",
                 str(noms))

        nom, vars_ = nut.variables("127.0.0.1", port)
        verifier(nom == "eaton", "le premier onduleur est pris par defaut", nom)
        verifier(vars_.get("battery.charge") == "100", "les variables sont lues")
        verifier(vars_.get("ups.serial") == 'AB"12',
                 "les guillemets echappes sont rendus", repr(vars_.get("ups.serial")))

        trouve = nut.identifier("127.0.0.1", port)
        verifier(trouve == ("EATON 5E 850i", "01.05"),
                 "la detection nomme le modele, pas le logiciel", str(trouve))

        print("\n== ce que la tuile affiche ==")
        stats = widgets.w_nut("http://127.0.0.1:%d" % port, "")
        rendu = {m["id"]: m["val"] for m in (stats or [])}
        print("    " + " · ".join("%s %s" % (k, v) for k, v in rendu.items()))
        verifier(rendu.get("batterie") == "100 %", "la charge de la batterie",
                 rendu.get("batterie"))
        # Sous une heure et demie, les minutes restent plus parlantes : c est la
        # meme regle que la carte Onduleur de l interface.
        verifier(rendu.get("autonomie") == "72 min", "l autonomie se lit en duree",
                 rendu.get("autonomie"))
        verifier(widgets._duree_courte(7200) == "2 h"
                 and widgets._duree_courte(8100) == "2 h 15",
                 "au-dela, elle passe en heures",
                 "%s / %s" % (widgets._duree_courte(7200), widgets._duree_courte(8100)))
        verifier(rendu.get("alim") == "sur secteur", "l etat vient de ups.status",
                 rendu.get("alim"))
        verifier(rendu.get("charge_ups") == "23 %", "la charge appliquee",
                 rendu.get("charge_ups"))
        verifier(rendu.get("tension") == "233 V", "la tension d entree",
                 rendu.get("tension"))
        verifier(rendu.get("puissance") == "110 W",
                 "la puissance, faute d etre publiee, se calcule",
                 rendu.get("puissance"))
        verifier(stats[0]["id"] == "batterie", "la batterie passe en tete",
                 stats[0]["id"])
    finally:
        srv.shutdown()

    print("\n== sur batterie, et bientot a plat ==")
    class Faible(FauxUpsd):
        vars_ = dict(VARS, **{"battery.charge": "17", "battery.runtime": "540",
                              "ups.status": "OB DISCHRG", "ups.load": "61"})
    srv, port = demarrer(Faible)
    try:
        rendu = {m["id"]: m for m in widgets.w_nut("127.0.0.1:%d" % port, "")}
        verifier(rendu["alim"]["val"] == "sur batterie", "l onduleur dit qu il tient seul",
                 rendu["alim"]["val"])
        verifier(rendu["autonomie"]["val"] == "9 min", "l autonomie restante",
                 rendu["autonomie"]["val"])
        import metriques
        verifier(metriques.niveau("batterie", rendu["batterie"]["num"]) == "avertissement",
                 "une batterie a 17 % declenche un avertissement")
        verifier(metriques.niveau("batterie", 8) == "critique",
                 "et devient critique en dessous de dix")
    finally:
        srv.shutdown()

    print("\n== un upsd qui exige un compte ==")
    class Ferme(FauxUpsd):
        exige_compte = True
    srv, port = demarrer(Ferme)
    try:
        diag = {}
        stats = widgets.w_nut("http://127.0.0.1:%d" % port, "", diag)
        verifier(stats is None, "sans identifiants, rien n est invente")
        verifier(any("access denied" in m for m in diag.get("refus", [])),
                 "et le refus est nomme", str(diag.get("refus")))
        stats = widgets.w_nut("http://127.0.0.1:%d" % port, "atrium:secret")
        verifier(bool(stats), "avec le couple, la lecture passe")
        verifier("USERNAME atrium" in srv.recu and "PASSWORD secret" in srv.recu,
                 "l identification suit l ordre du protocole")
    finally:
        srv.shutdown()

    print("\n== hors du reseau prive, on ne se connecte pas ==")
    try:
        nut.variables("93.184.216.34", 3493)
        verifier(False, "une adresse publique est refusee", "aucune exception")
    except nut.Refus as e:
        verifier("prive" in str(e), "une adresse publique est refusee", str(e))
    except OSError as e:
        verifier(False, "une adresse publique est refusee",
                 "connexion tentee : %s" % e)

    print()
    if ECHECS:
        print("%d point(s) en echec : %s" % (len(ECHECS), " ; ".join(ECHECS)))
        return 1
    print("l onduleur est lu, et refuse ce qui doit l etre.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
