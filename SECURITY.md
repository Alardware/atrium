# Sécurité

Atrium tourne chez vous, sur votre réseau, et garde tout ce qu'il sait dans un
seul fichier : `/config/atrium.json`. Ce document décrit ce qu'il s'autorise,
ce qu'il s'interdit, et les deux endroits où un analyseur signale à juste titre
un choix assumé.

## Signaler une faille

Ouvrez un ticket privé (Security → Report a vulnerability) plutôt qu'une issue
publique. Le dépôt est public, l'application ne l'est pas : la plupart des
installations ne sont accessibles que depuis un réseau domestique.

## Ce qui est stocké, et comment

- **Mots de passe** : PBKDF2-HMAC-SHA256, 600 000 itérations, sel par profil.
  Le nombre d'itérations est écrit dans l'empreinte : un profil ancien est
  réencodé silencieusement à la connexion suivante. Aucune empreinte ne sort
  du serveur — `/api/config` ne les renvoie jamais.
- **Clés d'API et jetons** des services surveillés : en clair dans
  `/config/atrium.json`, parce qu'il faut les rejouer à chaque interrogation.
  Le fichier n'est jamais versionné. Le navigateur n'en reçoit pas de copie :
  ce qu'il garde en mémoire locale est une liste explicitement filtrée.
- **Sauvegardes** : ni les sessions, ni le journal, ni les comptes cachés n'y
  figurent.

## Ce que le serveur s'interdit

- **Le relais `/px` ne sort pas du réseau privé.** L'adresse demandée est
  résolue, toutes ses adresses doivent être privées (RFC 1918 ou boucle
  locale), et c'est l'adresse résolue qui est appelée — pas le nom, qui
  pourrait pointer ailleurs entre-temps. Les redirections sont refusées. Le
  but est qu'Atrium ne puisse pas servir de rebond vers Internet.
- **Aucune dépendance Python.** L'image ne contient ni pip ni setuptools après
  construction : ce qui n'est pas installé n'a pas de faille connue.
- **Aucune installation à l'exécution.** Le conteneur ne télécharge rien pour
  fonctionner.

## Deux choix assumés

Les analyses automatiques (CodeQL, Trivy) signalent ces deux points. Ils sont
délibérés, et voici pourquoi.

### L'adresse de notification sort vers Internet

C'est la seule sortie volontaire d'Atrium vers l'extérieur, et elle n'existe
que si quelqu'un l'a configurée : Discord, ntfy, Gotify ou un crochet JSON.
Envoyer un message à l'adresse demandée est la fonction même du module —
CodeQL la classe donc en « SSRF complète ».

Ce qui l'encadre :

- le schéma est vérifié avant l'appel : `http` ou `https`, rien d'autre ;
- **les redirections sont refusées.** Un receveur qui répondait « 302 vers une
  adresse interne » était suivi, ce qui transformait une sortie vers Internet
  en sonde du réseau local. Un renvoi est désormais une erreur ;
- la réponse du receveur ne remonte jamais au navigateur : seuls le code HTTP
  et le motif d'échec sont rendus, à la personne qui a saisi l'adresse ;
- écrire cette adresse demande une session — et qui la détient dispose déjà
  des adresses et des clés de tous les services enregistrés.

### Le conteneur tourne en `root`

Atrium écrit dans `/config`, un volume monté depuis l'hôte. Sur les NAS, ce
dossier appartient à des comptes qui changent d'une machine à l'autre :
`99:100` sur Unraid, `1000:1000` ailleurs, `root` parfois. Un utilisateur fixe
dans l'image rendrait `/config` non inscriptible selon l'hôte, et
l'application ne pourrait plus rien enregistrer.

En contrepartie, le conteneur ne demande aucune capacité supplémentaire,
n'installe rien, et `/var/run/docker.sock` reste un montage **facultatif** —
son absence n'enlève que le tableau des conteneurs et le redémarrage à la
demande. Ce montage donne l'équivalent du compte root de la machine : c'est le
montage qui accorde ce pouvoir, pas l'usage qu'Atrium en fait.

La correction propre serait un point d'entrée abaissant les privilèges vers un
`PUID`/`PGID` fourni. Ce n'est pas un oubli, c'est un travail à faire.

## Ce que la chaîne d'intégration vérifie, à chaque poussée

| Analyse | Portée |
|---|---|
| CodeQL | Python et JavaScript |
| Bandit | le code du serveur |
| gitleaks | tout l'historique, à la recherche d'un secret |
| Trivy | l'image construite et les fichiers d'infrastructure |
| Matrice des routes | chaque route appelée sans session, puis avec |
| Démarrage de l'image | le conteneur est lancé et doit répondre |

Les routes ne sont pas recopiées dans le test : elles sont relues dans le
source du serveur. Une route ajoutée sans garde apparaît donc le jour même.
