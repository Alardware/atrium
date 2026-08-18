"""La barre d attente Docker doit dire la mesure, pas une duree inventee.

Une barre qui avance toute seule promet une fin qu elle ne connait pas : elle
atteint cent pour cent, et la page continue d attendre. Ce test verifie que le
compteur du serveur ne decrit que du travail reellement fait.

Le demon est simule : lister les conteneurs prend un temps, mesurer chacun en
prend un autre, et l on echantillonne le compteur pendant ce temps-la.

Sortie : 0 si le compte est honnete, 1 au premier point qui cede.
"""
import os
import sys
import threading
import time

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

from app import conteneurs          # noqa: E402

COMBIEN = 10
TEMPS_LISTE = 0.20        # le demon rend la liste des conteneurs
TEMPS_MESURE = 0.25       # puis la consommation de chacun, un appel par tete

ECHECS = []


def verifier(condition, titre, detail=""):
    print(("  ok   " if condition else "  ECHEC ") + titre + (" — " + detail if detail else ""))
    if not condition:
        ECHECS.append(titre)


def _faux_get(chemin):
    """Un demon lent, comme celui d un NAS qui heberge trente conteneurs."""
    if chemin.startswith("/containers/json"):
        time.sleep(TEMPS_LISTE)
        return [{"Id": "c%02d" % i, "Names": ["/app-%02d" % i], "State": "running",
                 "Image": "image:latest", "Status": "Up 2 days"}
                for i in range(COMBIEN)]
    time.sleep(TEMPS_MESURE)
    return {
        "cpu_stats": {"cpu_usage": {"total_usage": 2000}, "system_cpu_usage": 100000,
                      "online_cpus": 4},
        "precpu_stats": {"cpu_usage": {"total_usage": 1000}, "system_cpu_usage": 90000},
        "memory_stats": {"usage": 300 * 1024 * 1024, "limit": 2 * 1024 * 1024 * 1024,
                         "stats": {"inactive_file": 40 * 1024 * 1024}},
    }


def main():
    conteneurs.disponible = lambda: True
    conteneurs._get = _faux_get

    print("\n== au repos, aucun releve en cours ==")
    verifier(conteneurs.avancement()["phase"] == "repos", "aucune mesure annoncee",
             conteneurs.avancement()["phase"])

    resultat = {}

    def travailler():
        lignes = conteneurs.liste()
        resultat["lignes"] = lignes
        resultat["fin"] = time.time()

    print("\n== pendant le releve, le compteur suit le travail ==")
    fil = threading.Thread(target=travailler)
    releves = []
    debut = time.time()
    fil.start()
    while fil.is_alive():
        a = conteneurs.avancement()
        releves.append((time.time() - debut, a["phase"], a["fait"], a["total"]))
        time.sleep(0.03)
    fil.join()
    a_fin = conteneurs.avancement()

    phases = [r[1] for r in releves]
    verifier("liste" in phases, "la demande de la liste est annoncee comme telle")

    avant_total = [r for r in releves if r[1] == "liste"]
    verifier(all(r[3] == 0 for r in avant_total),
             "aucun total tant que la liste n est pas revenue",
             "un pourcentage y serait invente")

    mesures = [r for r in releves if r[1] == "mesure"]
    verifier(bool(mesures), "la phase de mesure est annoncee")
    verifier(all(r[3] == COMBIEN for r in mesures), "le total est celui du demon",
             str(sorted({r[3] for r in mesures})))

    faits = [r[2] for r in mesures]
    verifier(all(b >= a for a, b in zip(faits, faits[1:])), "le compte ne recule jamais",
             str(faits))
    verifier(all(r[2] <= r[3] for r in mesures), "jamais plus de mesures que de conteneurs")
    verifier(any(0 < f < COMBIEN for f in faits),
             "le compte passe par des valeurs intermediaires",
             "sinon la barre saute de zero a tout : %s" % faits)

    print("\n== cent pour cent veut dire fini ==")
    # Le defaut corrige : l ancienne barre atteignait 100 % au bout de 2,2 s,
    # que la mesure soit arrivee ou non.
    pleins = [r for r in releves if r[3] and r[2] == r[3]]
    premier_plein = pleins[0][0] if pleins else None
    duree = resultat["fin"] - debut
    verifier(premier_plein is None or premier_plein <= duree + 0.01,
             "le compte n est plein qu au terme du travail",
             "plein a %.2f s, travail fini a %.2f s" % (premier_plein or -1, duree))
    verifier(a_fin["phase"] == "fini" and a_fin["fait"] == a_fin["total"] == COMBIEN,
             "l etat final est complet", str(a_fin))
    verifier(len(resultat["lignes"]) == COMBIEN, "toutes les lignes sont rendues",
             str(len(resultat["lignes"])))
    verifier(resultat["lignes"][0]["cpu"] is not None
             and resultat["lignes"][0]["memoire"] is not None,
             "compter n empeche pas de mesurer")

    print("\n== un demon muet ne laisse pas un releve en suspens ==")
    conteneurs._get = lambda chemin: None
    conteneurs.liste()
    verifier(conteneurs.avancement()["phase"] == "repos",
             "le compteur retombe au repos", conteneurs.avancement()["phase"])

    print()
    if ECHECS:
        print("%d point(s) en echec : %s" % (len(ECHECS), " ; ".join(ECHECS)))
        return 1
    print("le compteur ne decrit que du travail fait.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
