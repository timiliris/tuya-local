# Tuya Local

Fork Docker autonome de [DanEng1982/tuya-rtsp-bridge](https://github.com/DanEng1982/tuya-rtsp-bridge), avec le moteur corrigé et la nouvelle interface dans une seule image.

- Vidéo locale, sortie RTSP et lecteur WebRTC. Conversion locale de secours en H.264 si le navigateur ne décode pas la HD H.265.
- Réveil à la demande avec tentatives limitées, diagnostics, détection des vidéos figées et reconnexion automatique.
- Interface en français, anglais, néerlandais, allemand et espagnol ; thèmes clair, sombre et automatique.
- Mode sentinelle : caméras au choix, mosaïque ou rotation, plein écran, réglages mémorisés et maintien de l’écran éveillé si le navigateur le permet.
- Connexion par QR code, captures d’image et PTZ selon le modèle.
- Réglages typés par caméra, modifications groupées, réveil automatique des caméras sur batterie et confirmation des valeurs remontées.
- IA NanoDet locale sur le serveur, activable par caméra, avec filtres d’objets, de confiance, de taille et de zone ; historique local et vignettes facultatives.
- Analyse continue pour les caméras alimentées ou analyse passive d’un flux VLC/navigateur existant. Les modèles sur batterie reconnus restent en mode passif : l’analyse s’arrête avec le dernier lecteur.

L’IA fonctionne sans navigateur ouvert. Les modèles sont intégrés à l’image Docker avec vérification de leur empreinte ; les images ne sont pas envoyées à un service d’analyse externe. La page IA affiche les états par caméra, les détections et les observations regroupées. L’historique conserve au maximum sept jours et 500 observations ; les vignettes sont facultatives. Les réglages IA et la base contenant les événements et leurs vignettes restent dans le volume de données, exclus de Git et du contexte de construction Docker.

Les réglages des caméras peuvent utiliser le cloud Tuya, comme le compte, le réveil et la signalisation. La vidéo et l’analyse IA restent locales. NanoDet et ses modèles proviennent d’OpenCV Zoo sous licence Apache-2.0 ; voir [vendor/nanodet/LICENSE](vendor/nanodet/LICENSE) et [NOTICE.md](NOTICE.md).

Le compte, le réveil et la négociation dépendent encore de Tuya. Le PTZ peut utiliser le cloud. Les relais vidéo sont refusés ; la compatibilité et le réveil après une longue veille dépendent du modèle et du firmware. La surveillance continue consomme la batterie des caméras solaires.

## Installer Tuya Local

```sh
git clone https://github.com/timiliris/tuya-local.git
cd tuya-local
cp .env.example .env
# Modifier TUYA_HOST_IP dans .env : adresse IPv4 locale de l’ordinateur Docker.
docker compose up -d --build
```

Ouvrir [l’interface locale](http://127.0.0.1:18788/) et scanner son QR code avec Tuya / Smart Life. Le VLAN des caméras doit communiquer avec l’hôte Docker. Les ports 52000–52019/UDP servent au flux des caméras et 52100–52119/TCP à WebRTC dans le navigateur. L’interface et RTSP restent accessibles uniquement depuis l’hôte par défaut. L’interface web ne possède pas de connexion utilisateur propre : conserver cette restriction ou limiter l’accès au réseau de confiance.

Les sessions et clés des caméras restent dans les volumes Docker `tuya_data` et `tuya_config`. Le fichier `.env`, les listes de caméras, les captures et les données d’exécution sont exclus de Git et du contexte de compilation Docker. Les constantes publiques des profils d’application Tuya restent dans le code ; elles ne correspondent pas aux sessions privées ou aux clés des caméras. Le scanner n’autorise que ces constantes exactes, sans exclure de fichiers source.

Les licences et les auteurs d’origine sont conservés. La documentation historique ci-dessous concerne le projet d’origine et ses anciens modes d’installation.

---

# Pont RTSP Tuya

<p align="center">
  <img src="docs/images/logo.png" width="128" alt="Tuya RTSP Bridge">
</p>

[![Licence : MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![English](README.md)](README.md) [![Deutsch](README.de.md)](README.de.md) [![Nederlands](README.nl.md)](README.nl.md) [![Français](README.fr.md)](README.fr.md) [![Español](README.es.md)](README.es.md) [![Português](README.pt.md)](README.pt.md) [![Italiano](README.it.md)](README.it.md) [![Polski](README.pl.md)](README.pl.md) [![Čeština](README.cs.md)](README.cs.md) [![Русский](README.ru.md)](README.ru.md) [![Українська](README.uk.md)](README.uk.md) [![Bahasa Indonesia](README.id.md)](README.id.md) [![简体中文](README.zh.md)](README.zh.md) [![हिन्दी](README.hi.md)](README.hi.md) [![日本語](README.ja.md)](README.ja.md) [![한국어](README.ko.md)](README.ko.md) [![עברית](README.he.md)](README.he.md) [![ייִדיש](README.yi.md)](README.yi.md)

**Transformez n’importe quelle caméra Tuya / Smart Life / iSmartLife en caméra RTSP normale** — pour Frigate, Agent DVR, go2rtc, Home Assistant ou VLC.

Pas de flash firmware. Pas d’ONVIF (le firmware d’origine n’en a pas). Un QR à scanner, puis une URL RTSP à copier.

| Vous êtes… | Commencez ici |
|---|---|
| Vous voulez que ça marche | [En 5 minutes](#installation-en-5-minutes) |
| Labo / NVR | [docs/nvr.md](docs/nvr.md) |
| Développeur | [docs/architecture.md](docs/architecture.md) |

Ce dépôt ne contient **aucun** compte, identifiant d’appareil ou IP personnelle.

## Pourquoi les caméras Tuya bon marché en ont besoin

Ces caméras « Smart Life » à 20–40 € ressemblent à des caméras IP. Elles n’en sont pas. Firmware d’origine : **pas d’ONVIF**, **pas de case RTSP**. Le direct passe par l’appli du fabricant et un cloud que vous ne contrôlez pas. Un second téléphone ou un « NVR cloud » veut souvent un abonnement — ou vole l’unique session live.

Vous avez payé un capteur sur *votre* mur. L’enregistrement doit aller sur *votre* disque.

Cette appli est un petit pont local : scannez un QR dans l’appli que vous avez déjà, puis chaque caméra a une URL normale :

```
rtsp://<ce-pc>:8554/<NomCamera>/hd
```

La signalisation reste chez Tuya. Depuis ce PC, la vidéo reste en général sur le LAN. Texte long : [docs/fr/why.md](docs/fr/why.md).

### L’application

Premier lancement — langue, région, QR, confirmation dans Smart Life :

![Écran d’accueil. Liste vide, pas de QR, localhost seulement.](docs/images/ui-welcome.png)

Après connexion — noms de démo uniquement. Les aperçus restent noirs dans la doc (pas d’image live) :

![Deux caméras fictives, RTSP HD sur 127.0.0.1.](docs/images/ui-ready.png)

## Crédits

Le moteur RTSP est **[tuya-ipc-terminal](https://github.com/seydx/tuya-ipc-terminal)** de **[seydx](https://github.com/seydx)** (MIT), commit `d65b3e9`. Voir [CREDITS.md](CREDITS.md) et [NOTICE.md](NOTICE.md).

## Ce que vous obtenez

- HD : `rtsp://<ce-pc>:8554/<NomCamera>/hd` (souvent HEVC 1080p)
- SD : `…/sd` (H.264)
- Toutes les caméras partagent **une** IP de pont ; seul le chemin change
- Aperçu si [VLC](https://www.videolan.org/) est installé
- Langues : 18, dont le français. L’installateur Windows en a 17 (le yiddish est dans l’appli).

## Limites honnêtes

- Pas d’ONVIF, pas de RTSP natif sur la caméra
- Beaucoup de modèles sortent environ **10 im/s** en HD — c’est la caméra
- VLC 3 est souvent noir en HEVC/RTSP ; Agent DVR / Frigate sont les clients visés
- L’enregistrement se fait dans votre NVR, pas sur le pont

Régions : Europe de l’Ouest/Est, USA Ouest/Est, Chine, Inde.

## Installation en 5 minutes

1. Windows 10/11 **ou Arch Linux**
2. Un compte Smart Life / Tuya Smart qui voit déjà les caméras

Sous Windows : pas besoin d’installer Python, VLC ni ffmpeg. C’est dans le Setup.

Windows : `TuyaRtspBridge-Setup.exe` dans [Releases](https://github.com/DanEng1982/tuya-rtsp-bridge/releases) — suivant, suivant, terminer. Détails : [docs/windows.md](docs/windows.md).  
Arch : [docs/arch-linux.md](docs/arch-linux.md) — `./launch.sh`

Connexion : créer le QR → scanner dans Smart Life → **confirmer** → copier `rtsp://127.0.0.1:8554/<Nom>/hd`.

Sessions : `%APPDATA%\TuyaRtspBridge\` (Windows) ou `~/.local/share/tuya-rtsp-bridge/` (Linux).

## Licence

Notre code : MIT. Moteur embarqué : MIT, Copyright (c) 2025 seydx. Non affilié à Tuya Inc.

Noms, données locales, licences fournies : [docs/legal.md](docs/legal.md).

## Nouveau en 1.2.4+

- Login QR : canvas fixe **320×320** (bug fente Windows corrigé)
- **Add-on Home Assistant OS :** [homeassistant/tuya_rtsp_bridge/](homeassistant/tuya_rtsp_bridge/) (réseau hôte)
- Docker/HA : [docs/docker.md](docs/docker.md)
- PTZ : LAN d’abord (TCP 6668), **cloud PTZ** optionnel après email/mot de passe — pas de clés IoT developer
- Session protect : re-login auto avec mot de passe stocké

