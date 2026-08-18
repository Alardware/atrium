"""Chaque nom qu Atrium propose doit avoir un logo, pour de vrai.

Les logos viennent du catalogue « dashboard-icons ». Le nom de fichier s y
deduit presque toujours du nom du service, mais le catalogue a ses habitudes :
« pi-hole » et non « pihole », « adguard-home » et non « adguard ». Une table
d exceptions vit dans l interface ; ce controle la confronte au catalogue reel
plutot qu a une supposition.

Le catalogue est lu en ligne. Sans reseau, le test s abstient — il ne sert a
rien de faire echouer une chaine d integration parce qu un CDN a hoquete.

Usage :
    python outils/verifier_icones.py

Sortie : 0 si chaque nom trouve son dessin, 1 sinon.
"""
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(RACINE, "app", "static", "index.html")
CATALOGUE = "https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/tree.json"


def source():
    with open(PAGE, encoding="utf-8") as f:
        return f.read()


def table_exceptions(src):
    """La table SLUGS, relue dans l interface : une seule source de verite."""
    bloc = src[src.index("const SLUGS = {"):]
    bloc = bloc[:bloc.index("};")]
    return dict(re.findall(r"'?([\w-]+)'?\s*:\s*'([^']+)'", bloc))


def slug(nom, exceptions):
    """La meme reduction que slugIcone, cote navigateur."""
    brut = unicodedata.normalize("NFD", str(nom).lower())
    brut = "".join(c for c in brut if unicodedata.category(c) != "Mn")
    brut = re.sub(r"[^a-z0-9]+", "-", brut).strip("-")
    return exceptions.get(brut) or exceptions.get(brut.replace("-", "")) or brut


def main():
    src = source()
    try:
        with urllib.request.urlopen(CATALOGUE, timeout=30) as r:
            arbre = json.load(r)
    except (urllib.error.URLError, ValueError, OSError) as e:
        print("catalogue injoignable (%s) — controle ignore" % e)
        return 0

    dispo = set(x[:-4] for x in arbre.get("svg", []))
    exceptions = table_exceptions(src)
    print("catalogue : %d dessins   exceptions declarees : %d"
          % (len(dispo), len(exceptions)))

    # Ce qu Atrium propose au premier lancement, et ce qu il sait typer : deux
    # listes de noms qui finiront sur une tuile.
    noms = re.findall(r"A\('([^']+)',", src)
    bloc = src[src.index("const NOMS_CONNUS"):]
    bloc = bloc[:bloc.index("];")]
    tapes = re.findall(r"\['([^']+)'", bloc)

    manquants = []
    for titre, liste in (("catalogue propose", noms), ("noms reconnus", tapes)):
        absents = [n for n in liste if slug(n, exceptions) not in dispo]
        print("  %-18s %3d noms, %d sans dessin" % (titre, len(liste), len(absents)))
        for n in absents:
            print("      %-22s -> %s" % (n, slug(n, exceptions)))
        manquants += absents

    # Une exception qui designe un fichier inexistant est pire que pas
    # d exception : elle promet un dessin que personne ne verra.
    fautives = [(k, v) for k, v in exceptions.items() if v not in dispo]
    for k, v in fautives:
        print("      exception morte : %s -> %s" % (k, v))

    print()
    if manquants or fautives:
        print("RESUME : %d nom(s) sans dessin" % (len(manquants) + len(fautives)))
        return 1
    print("RESUME : chaque nom trouve son dessin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
