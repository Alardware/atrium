"""La version annoncee doit etre la meme partout.

Atrium la declare a deux endroits — le serveur et le module Home Assistant — et
la chaine de publication en fabrique les etiquettes de l image a partir du tag
git. Trois sources pour une seule verite : il suffit d en oublier une pour
qu un conteneur s annonce sous une version qu il n est pas.

Ce controle relit les trois et refuse le desaccord. Sur un tag « v1.2.0 », il
verifie en plus que le tag correspond a ce que le code annonce.

Usage :
    python outils/verifier_version.py

Sortie : 0 si tout concorde, 1 sinon.
"""
import io
import os
import re
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ECHECS = []


def verifier(condition, titre, detail=""):
    print(("  ok    " if condition else "  ECHEC ") + titre + (" — " + detail if detail else ""))
    if not condition:
        ECHECS.append(titre)


def lire(rel):
    return io.open(os.path.join(RACINE, rel), encoding="utf-8").read()


def main():
    serveur = re.search(r'^VERSION = "([^"]+)"', lire("app/server.py"), re.M)
    module = re.search(r'^version: "([^"]+)"', lire("addon/config.yaml"), re.M)

    print("\n== la version, la ou elle est ecrite ==")
    verifier(bool(serveur), "le serveur declare une version")
    verifier(bool(module), "le module Home Assistant declare une version")
    if not (serveur and module):
        return 1
    v_serveur, v_module = serveur.group(1), module.group(1)
    print("    serveur : %s   module : %s" % (v_serveur, v_module))
    verifier(v_serveur == v_module, "les deux declarations concordent",
             "%s / %s" % (v_serveur, v_module))
    verifier(bool(re.match(r"^\d+\.\d+\.\d+$", v_serveur)),
             "la forme est celle d une version", v_serveur)

    # Sur un tag, la chaine de publication etiquette l image d apres le tag :
    # un tag qui ne dit pas la meme chose que le code publierait une image
    # « 1.2.0 » qui s annonce « 1.1.0 » dans son propre ecran.
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/v"):
        tag = ref[len("refs/tags/v"):]
        print("\n== la publication en cours ==")
        verifier(tag == v_serveur, "le tag correspond au code",
                 "tag %s / code %s" % (tag, v_serveur))
    else:
        print("\n(aucun tag en cours : rien a confronter)")

    print()
    if ECHECS:
        print("%d point(s) en echec : %s" % (len(ECHECS), " ; ".join(ECHECS)))
        return 1
    print("la version est la meme partout : %s" % v_serveur)
    return 0


if __name__ == "__main__":
    sys.exit(main())
