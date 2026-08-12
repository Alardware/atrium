# Atrium

Tableau de bord auto-hébergé pour votre maison : vos applications, l'état de vos
serveurs et ce qui se passe en ce moment, sur une seule page.

Atrium est une **application autonome**. Elle ne dépend d'aucun autre logiciel
pour fonctionner : chaque intégration (Plex, Unraid, UniFi, Home Assistant…) est
optionnelle et se branche depuis l'interface.

![Le tableau de bord](docs/accueil.png)

## Ce qu'Atrium fait

**Il surveille.** Le serveur sonde vos services toutes les trente secondes et
mesure leur temps de réponse. Il ne se contente pas de « en ligne / hors ligne » :
un service qui répond mais dont une mesure a franchi un seuil est **dégradé**, et
c'est dit. Les seuils sont déclaratifs — un disque à 95 % est critique, une
température de grappe à 55 °C mérite un avertissement.

**Il n'invente rien.** Chaque chiffre affiché vient d'une mesure. Pas de courbe
sans relevés, pas de tendance sans historique assez ancien, pas de barre sans
échelle. Quand une information n'est pas connue, la page le dit.

**Il se souvient.** La disponibilité de chaque service est conservée heure par
heure, sur trente jours : deux entiers par service et par heure, une centaine de
kilo-octets en tout. Le tableau « Santé des services » en montre les
vingt-quatre dernières, et distingue une heure sans panne d'une heure sans
mesure — un service ajouté ce matin n'affiche pas « 100 % sur 24 h ».

**Il continue sans vous.** Les mesures sont prises par le serveur, jamais par le
navigateur : l'état reste connu même sans onglet ouvert, et un rechargement
n'attend aucun appel réseau.

### La page Serveurs

CPU, mémoire, stockage et température de la machine, avec la tendance sur le
dernier quart d'heure et une heure d'historique. Si la socket Docker est montée,
la liste des conteneurs avec leur consommation et de quoi les redémarrer.

![La page Serveurs](docs/serveurs.png)

### Les connexions

Une source est « branchée » quand elle a effectivement livré des mesures — c'est
la seule preuve qui vaille. La page donne le temps de réponse de chacune, ce
qu'elle remonte, et la raison quand ça ne marche pas.

![Les connexions](docs/connexions.png)

### La sécurité

Un état des lieux vérifié **côté serveur** : une page qui s'auto-déclarerait sûre
ne prouverait rien. Mot de passe, tentatives limitées, relais restreint,
provenance de la connexion, journal des accès refusés, appareils connectés.

![La page Sécurité](docs/securite.png)

## Installation

L'image est publiée sur GitHub Container Registry pour `amd64`, `arm64` et `armv7`.

### Docker Compose (recommandé)

```yaml
services:
  atrium:
    image: ghcr.io/alardware/atrium:latest
    container_name: atrium
    restart: unless-stopped
    ports:
      - "8420:8420"
    volumes:
      - ./data:/config
    environment:
      TZ: Europe/Paris
```

```bash
docker compose up -d
```

Puis ouvrez `http://<ip-du-serveur>:8420`.

### Docker (ligne de commande)

```bash
docker run -d --name atrium -p 8420:8420 -v atrium_config:/config --restart unless-stopped ghcr.io/alardware/atrium:latest
```

### Unraid

Le fichier `unraid-template.xml` est un template prêt à l'emploi. Copiez-le dans
`/boot/config/plugins/dockerMan/templates-user/` sur votre serveur, puis
**Docker → Add Container → Template : Atrium**.

Sinon, en une commande depuis le terminal Unraid :

```bash
docker run -d --name atrium -p 8420:8420 -v /mnt/user/appdata/atrium:/config --restart unless-stopped ghcr.io/alardware/atrium:latest
```

### Add-on Home Assistant

Le dossier `addon/` contient de quoi installer Atrium comme add-on (avec ingress,
donc accessible depuis la barre latérale de HA). Copiez `addon/` et `app/` dans un
dépôt d'add-ons, puis ajoutez ce dépôt dans **Paramètres → Modules complémentaires
→ Boutique → Dépôts**.

C'est une **méthode d'installation**, pas une dépendance : l'application est la
même et fonctionne sans Home Assistant.

### Construire l'image soi-même

```bash
docker build -t atrium .
```

### Voir les conteneurs Docker

La page Serveurs liste les conteneurs si vous montez la socket du démon :

```yaml
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

C'est **facultatif**, et ce n'est pas anodin : l'accès à cette socket équivaut à
un accès root sur la machine hôte. Sans elle, tout le reste fonctionne — seule la
liste des conteneurs disparaît.

## Configuration

| Variable | Défaut | Rôle |
|---|---|---|
| `ATRIUM_PORT` | `8420` | Port d'écoute |
| `ATRIUM_CONFIG_DIR` | `/config` | Dossier de la configuration (`atrium.json`) |
| `ATRIUM_ALLOW_NET` | réseaux privés | Noms d'hôtes que le relais a le droit de joindre, en plus des réseaux privés |
| `TZ` | — | Fuseau horaire |
| `ATRIUM_DEBUG` | — | Journalise les requêtes si défini |

Le dossier `/config` contient `atrium.json` (applications, utilisateurs, clés),
`sessions.json`, `journal.json` (connexions refusées, sept jours) et
`historique.json` (disponibilité horaire, trente jours). Montez ce volume pour
les conserver entre deux mises à jour.

## Intégrations

Atrium reconnaît le service à partir de son URL, propose la bonne configuration,
et n'affiche que les mesures qu'il obtient réellement. Chacune se configure sur la
fiche de son application (mode création → ✎ → **Tester**).

| Domaine | Services | Ce qu'Atrium en tire |
|---|---|---|
| **Média** | Plex, Jellyfin, Tautulli | Lectures, spectateurs, transcodages, débit, bibliothèques |
| **Téléchargement** | Sonarr, Radarr, Lidarr, Readarr, Whisparr, Prowlarr, Bazarr, SABnzbd, qBittorrent | Épisodes manquants, files d'attente, indexeurs, débit |
| **Maison** | Home Assistant | Lumières, ouvertures, présences, automatisations, mises à jour |
| **Machines** | Unraid | CPU, mémoire, grappe, température, uptime, conteneurs |
| **Réseau** | UniFi, AdGuard Home, Pi-hole | Clients, équipements, bornes, requêtes DNS, blocages |
| **Fichiers** | Immich, Paperless, Nextcloud | Photos, vidéos, documents, utilisateurs |
| **Outils** | Portainer, Uptime Kuma | Conteneurs actifs, services surveillés |
| **Toute autre app** | — | En ligne / hors ligne, temps de réponse |

Les API de ces services n'autorisent pas les appels directs depuis un navigateur
(CORS absent, certificats auto-signés) : le serveur d'Atrium les relaie. Le relais
résout le nom d'hôte, vérifie que **toutes** les adresses obtenues sont privées,
puis se connecte à l'adresse validée — il refuse les redirections. Il ne peut pas
servir de rebond vers Internet.

## Sécurité

Les mots de passe sont dérivés avec PBKDF2-HMAC-SHA256 (240 000 itérations, sel
par utilisateur) et ne quittent jamais le serveur. Les sessions sont des jetons
serveur transmis dans un cookie `HttpOnly` + `SameSite=Strict`. Les routes de
configuration refusent toute requête sans session valide, un profil ne peut
modifier que son propre mot de passe, et les tentatives de connexion sont
limitées par adresse.

C'est un **garde-fou familial**, pas une authentification d'entreprise :
n'exposez pas Atrium directement sur Internet. Placez-le derrière un reverse
proxy authentifiant (Authelia, Authentik) ou derrière l'ingress de Home
Assistant. La page Sécurité détecte ces portails et vous dit où vous en êtes.

## Licence

MIT
