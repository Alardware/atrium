# Atrium

Tableau de bord auto-hébergé pour votre maison : vos applications, l'état de vos
serveurs et les lectures en cours, sur une seule page.

Atrium est une **application autonome**. Elle ne dépend d'aucun autre logiciel
pour fonctionner : chaque intégration (Plex, Unraid, UniFi, Home Assistant…) est
optionnelle et se branche depuis l'interface.

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

### Construire l'image soi-même

```bash
docker build -t atrium .
```

### Add-on Home Assistant

Le dossier `addon/` contient de quoi installer Atrium comme add-on (avec ingress,
donc accessible depuis la barre latérale de HA). Copiez `addon/` et `app/` dans un
dépôt d'add-ons, puis ajoutez ce dépôt dans **Paramètres → Modules complémentaires
→ Boutique → Dépôts**.

C'est une **méthode d'installation**, pas une dépendance : l'application est la
même et fonctionne sans Home Assistant.

## Configuration

| Variable | Défaut | Rôle |
|---|---|---|
| `ATRIUM_PORT` | `8420` | Port d'écoute |
| `ATRIUM_CONFIG_DIR` | `/config` | Dossier de la configuration (`atrium.json`) |
| `ATRIUM_ALLOW_NET` | réseaux privés | Préfixes que le relais a le droit de joindre |
| `ATRIUM_DEBUG` | — | Journalise les requêtes si défini |

La configuration (applications, utilisateurs, clés) est stockée dans
`/config/atrium.json` — montez ce volume pour la conserver entre deux mises à jour.

## Intégrations

Chacune se configure sur la fiche de son application (survolez sa tuile → ✎) :

| Service | Ce qu'Atrium en tire | Identifiant requis |
|---|---|---|
| **Plex** | Sessions en cours, pochettes, progression, utilisateur | Token `X-Plex-Token` |
| **Unraid** | CPU, RAM, remplissage de la grappe | Clé API (Réglages → Management Access) |
| **UniFi** | CPU/RAM du routeur, appareils connectés | Clé API (Admins & Users → Create API Key) |
| **Home Assistant** | Domotique, lecteurs, mises à jour, capteurs système | Jeton d'accès longue durée |
| **N'importe quelle app** | En ligne / hors ligne | URL seule |

Les API de ces services n'autorisent pas les appels directs depuis un navigateur
(CORS absent, certificats auto-signés) : le serveur d'Atrium les relaie. Le relais
n'accepte que des cibles sur le réseau privé.

## Sécurité

Atrium propose des profils utilisateurs et un verrouillage d'écran. C'est un
**garde-fou familial**, pas une authentification : tout le contrôle se fait côté
navigateur. Pour une exposition réelle, placez Atrium derrière un reverse proxy
authentifiant (Authelia, Authentik) ou derrière l'ingress de Home Assistant.

N'exposez pas Atrium directement sur Internet.

## Licence

MIT
