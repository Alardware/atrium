# -*- coding: utf-8 -*-
"""Aucune couleur ne doit designer un jeton qui n existe pas.

« color: var(--text-1) » ne fait pas d erreur : la propriete est simplement
ignoree, et le texte herite d une couleur voisine. Un chiffre peut ainsi
devenir presque invisible sans que rien ne le signale — c est arrive au
pourcentage inscrit dans les anneaux de la page d accueil, sur un jeton jamais
defini.

Ce controle relit la feuille de style de l interface, ramasse les jetons
declares et ceux employes, et refuse les seconds qui ne figurent pas parmi les
premiers. Les emplois avec valeur de repli — « var(--x, #fff) » — sont laisses
tranquilles : ils disent quoi faire en cas d absence.

Sortie : 0 si tous les jetons existent, 1 sinon.
"""
import io
import os
import re
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(RACINE, "app", "static", "index.html")


def main():
    source = io.open(PAGE, encoding="utf-8").read()
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", source, re.S))
    print("feuille de style : %d Ko" % (len(styles) / 1024))

    declares = set(re.findall(r"(--[\w-]+)\s*:", styles))
    # Un emploi sans repli : « var(--x) », pas « var(--x, quelque chose) ».
    employes = set(re.findall(r"var\(\s*(--[\w-]+)\s*\)", styles))
    # Certains jetons sont poses par le script, pas par la feuille.
    poses_ailleurs = set(re.findall(r"setProperty\(\s*'(--[\w-]+)'", source))

    print("jetons declares : %d" % len(declares))
    print("jetons employes sans repli : %d" % len(employes))

    manquants = sorted(employes - declares - poses_ailleurs)
    if manquants:
        print("\nJetons employes mais jamais declares :")
        for m in manquants:
            for n, ligne in enumerate(styles.split("\n"), 1):
                if "var(%s)" % m in ligne:
                    print("  %-22s %s" % (m, ligne.strip()[:96]))
                    break
        print("\n%d jeton(s) a corriger." % len(manquants))
        return 1
    print("\ntoutes les couleurs designent un jeton qui existe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
