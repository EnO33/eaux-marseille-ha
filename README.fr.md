# Eaux de Marseille — Intégration Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Release](https://img.shields.io/github/v/release/EnO33/eaux-marseille-ha)](https://github.com/EnO33/eaux-marseille-ha/releases)
[![Tests](https://github.com/EnO33/eaux-marseille-ha/actions/workflows/tests.yml/badge.svg)](https://github.com/EnO33/eaux-marseille-ha/actions/workflows/tests.yml)
[![Niveau de qualité : silver](https://img.shields.io/badge/quality--scale-silver-silver.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale)
[![Licence : MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

🇬🇧 [Read in English](README.md)

Intégration Home Assistant non officielle pour les portails clients des trois fournisseurs d'eau du bassin marseillais. Malgré le chevauchement géographique, chaque fournisseur opère son propre portail et dessert des communes différentes :

- **Société des Eaux de Marseille (SEM)** — Ventabren, Bandol, Vitrolles, Trets, Fuveau, Cabriès, Bouc-Bel-Air, Le Puy-Sainte-Réparade, Forcalquier et autres communes périphériques — [`espaceclients.eauxdemarseille.fr`](https://espaceclients.eauxdemarseille.fr)
- **Eau de Marseille Métropole (SEMM)** — Marseille même, La Ciotat, Cassis, Carnoux, Carry-le-Rouet, Allauch, Marignane, Septèmes-les-Vallons, Gémenos et autres — [`espaceclients.eaudemarseille-metropole.fr`](https://espaceclients.eaudemarseille-metropole.fr)
- **Vivaigo** — Salon-de-Provence, Berre-l'Étang, Lambesc, Eyguières, Pélissanne, Velaux, Rognac, Sénas, Lançon-de-Provence et le pays salonais/berrois — [`espaceclients.vivaigo.fr`](https://espaceclients.vivaigo.fr)

Les trois fournisseurs partagent la même infrastructure technique (opérée par SOMEI/Veolia), c'est pourquoi cette intégration unique gère les trois — vous choisissez le vôtre dans un menu déroulant lors de la configuration.

L'intégration récupère votre consommation d'eau depuis le portail choisi toutes les heures et l'expose sous forme de capteurs Home Assistant, ainsi que des statistiques mensuelles compatibles avec le tableau de bord Énergie.

> Ce projet n'est pas affilié à Société des Eaux de Marseille, Eau de Marseille Métropole, Vivaigo ou SOMEI, ni sponsorisé par eux.

---

## Fonctionnalités

- Configuration via l'interface Home Assistant — pas de YAML
- Supporte SEM, SEMM et Vivaigo via une seule intégration (sélecteur de fournisseur dans le formulaire)
- 12 capteurs par contrat (consommation, index compteur, moyenne journalière, périodes de facturation)
- Import des statistiques mensuelles historiques depuis 2024 — compatible avec le tableau de bord Énergie de HA
- Rafraîchissement automatique toutes les heures avec retry/backoff sur erreurs transitoires
- Flux de réauthentification quand le mot de passe du portail change — plus besoin de supprimer/recréer l'intégration
- Sécurisé : redirections cross-origin refusées (protection contre les fuites de type CVE-2018-18074)
- Traductions française et anglaise
- Niveau de qualité Home Assistant **Silver** (98 % de couverture de tests, toutes les règles requises remplies)

## Capteurs

| Entité | Description | Unité |
|---|---|---|
| `sensor.eaux_de_marseille_<contrat>_mois_en_cours` | Consommation du mois en cours | m³ |
| `sensor.eaux_de_marseille_<contrat>_mois_en_cours_litres` | Consommation du mois en cours | L |
| `sensor.eaux_de_marseille_<contrat>_annee_en_cours` | Consommation depuis le début de l'année | m³ |
| `sensor.eaux_de_marseille_<contrat>_index_compteur` | Index actuel du compteur | m³ |
| `sensor.eaux_de_marseille_<contrat>_moyenne_journaliere` | Moyenne journalière | m³ |
| `sensor.eaux_de_marseille_<contrat>_dernier_releve` | Dernière consommation facturée | m³ |
| `sensor.eaux_de_marseille_<contrat>_dernier_releve_litres` | Dernière consommation facturée | L |
| `sensor.eaux_de_marseille_<contrat>_date_du_dernier_releve` | Date du dernier relevé facturé | — |
| `sensor.eaux_de_marseille_<contrat>_duree_de_la_periode` | Nombre de jours dans la dernière période | jours |
| `sensor.eaux_de_marseille_<contrat>_releve_precedent` | Avant-dernière consommation facturée | m³ |
| `sensor.eaux_de_marseille_<contrat>_date_du_releve_precedent` | Date du relevé précédent | — |
| `sensor.eaux_de_marseille_<contrat>_nombre_de_releves` | Nombre total de relevés disponibles | — |

Une **statistique externe mensuelle** est également importée sous l'identifiant `eaux_marseille:monthly_consumption_<contrat>` — utilisable dans les cartes `statistics-graph` et le tableau de bord Énergie.

## Prérequis

- Home Assistant 2025.4 ou plus récent
- Un compte actif sur l'un des trois portails clients (SEM, SEMM ou Vivaigo)
- Votre numéro de contrat (visible sur vos factures ou dans l'URL du portail après connexion)

## Installation

### Via HACS (recommandé)

1. Dans Home Assistant, ouvrez **HACS**
2. Allez dans **Intégrations → menu ⋮ → Dépôts personnalisés**
3. Ajoutez `https://github.com/EnO33/eaux-marseille-ha` avec la catégorie **Intégration**
4. Cherchez **Eaux de Marseille** dans la liste HACS et installez
5. **Redémarrez Home Assistant**
6. Continuez avec la [Configuration](#configuration)

### Installation manuelle

1. Téléchargez la dernière version depuis la [page des releases](https://github.com/EnO33/eaux-marseille-ha/releases)
2. Copiez le dossier `custom_components/eaux_marseille` dans le répertoire `config/custom_components/` de votre Home Assistant
3. **Redémarrez Home Assistant**
4. Continuez avec la [Configuration](#configuration)

## Configuration

1. Dans Home Assistant, allez dans **Paramètres → Appareils et services → ➕ Ajouter une intégration**
2. Cherchez **Eaux de Marseille**
3. Remplissez le formulaire (voir [paramètres](#parametres-de-configuration) ci-dessous)
4. Validez

L'intégration vérifie les identifiants, puis crée l'appareil et ses capteurs. Au premier lancement, les statistiques mensuelles historiques disponibles sont également importées (en arrière-plan, quelques secondes).

### Paramètres de configuration

| Champ | Obligatoire | Format | Description | Où le trouver |
|---|---|---|---|---|
| **Fournisseur d'eau** | Oui | Liste déroulante | Quel fournisseur dessert votre adresse | SEM (Ventabren, Bandol, Vitrolles, Trets…), SEMM (Marseille, La Ciotat, Cassis, Marignane…) ou Vivaigo (Salon, Berre, Lambesc, Eyguières…). Choisissez celui dont vous utilisez le portail client. |
| **Email** | Oui | Adresse e-mail | Email de connexion au portail | L'adresse utilisée sur le portail de votre fournisseur |
| **Mot de passe** | Oui | Chaîne | Mot de passe du portail | Identique au site. Stocké chiffré au repos par Home Assistant. |
| **Numéro de contrat** | Oui | Numérique (typiquement 7 chiffres) | Identifiant de votre contrat | Visible sur vos factures sous « Numéro de contrat » et dans l'URL du portail après connexion (`https://<portail>/#/dashboard/<contrat>`) |

La combinaison *(fournisseur + numéro de contrat)* sert d'identifiant unique pour l'entrée de configuration — vous ne pouvez pas ajouter deux fois le même contrat sur le même portail.

### Réauthentification

Si vous changez votre mot de passe du portail, Home Assistant détectera l'échec d'authentification au prochain cycle de polling (≤ 1 heure) et affichera une notification. Cliquez dessus pour saisir le nouveau mot de passe — l'intégration conserve le contrat, les capteurs et les statistiques historiques.

### Plusieurs contrats

Vous pouvez ajouter l'intégration plusieurs fois, une fois par contrat. Chaque contrat devient un appareil séparé avec ses propres capteurs.

## Suppression

1. Dans Home Assistant, allez dans **Paramètres → Appareils et services**
2. Trouvez la carte de l'intégration **Eaux de Marseille**
3. Cliquez sur le **menu ⋮ → Supprimer**
4. (Optionnel) Pour également supprimer les statistiques historiques importées, allez dans **Outils de développement → Statistiques** et supprimez les entrées correspondant à `eaux_marseille:monthly_consumption_*`
5. (Optionnel, installation manuelle uniquement) Supprimez le dossier `custom_components/eaux_marseille`

## Dépannage

### « Impossible de se connecter au portail » lors de l'ajout

Activez les logs au niveau info en ajoutant à votre `configuration.yaml` :

```yaml
logger:
  default: warning
  logs:
    custom_components.eaux_marseille: info
```

Redémarrez Home Assistant, retentez l'ajout, puis consultez `home-assistant.log` pour les lignes `Authentication: step X/Y`. L'étape qui échoue indique s'il s'agit d'un problème réseau, d'identifiants ou côté portail.

Si **aucune** ligne `Authentication` n'apparaît, c'est que l'intégration n'est pas chargée — généralement un cache HACS. Vérifiez la version dans `/config/custom_components/eaux_marseille/manifest.json` correspond bien à la dernière release.

### Les capteurs affichent « indisponible »

Vérifiez la carte de l'intégration pour le message d'erreur. Causes courantes :
- Session du portail expirée ou mot de passe modifié → supprimez et recréez l'intégration
- Portail en panne → Home Assistant réessaiera à la prochaine actualisation
- Problème réseau → vérifiez que Home Assistant peut joindre l'hôte du portail de votre fournisseur (`espaceclients.eauxdemarseille.fr`, `espaceclients.eaudemarseille-metropole.fr` ou `espaceclients.vivaigo.fr`)

### Signaler un bug

Merci d'inclure :
- La version de Home Assistant
- La version de l'intégration (depuis `manifest.json`)
- Les lignes de log pertinentes (avec `custom_components.eaux_marseille: debug`)
- Si l'erreur survient à l'ajout ou en fonctionnement normal

[Ouvrir une issue →](https://github.com/EnO33/eaux-marseille-ha/issues)

## Fonctionnement

Les trois portails sont des SPA AngularJS reposant sur la même API REST (opérée par SOMEI/Veolia). Seuls le nom d'hôte et les credentials applicatifs intégrés diffèrent entre fournisseurs. L'authentification suit un flux en cinq étapes :

1. **GET** sur la page d'accueil du portail pour obtenir un cookie de session
2. **POST** `/webapi/Acces/generateToken` — échange une clé applicative statique (embarquée dans le bundle JS du portail) contre un token de courte durée
3. **POST** `/webapi/Utilisateur/authentification` — échange les identifiants utilisateur et le token de courte durée contre un token de session (`tokenAuthentique`)
4. **GET** `/webapi/Abonnement/getContratParDefaut/` — récupère les métadonnées du contrat par défaut
5. Le token de session est posé dans le cookie `aelToken` et un contexte sérialisé dans le cookie `AEL_CONTEXT`

Les requêtes suivantes embarquent le token de session dans l'en-tête HTTP `token`, accompagné d'un en-tête `ConversationId` régénéré à chaque requête. L'intégration utilise une `aiohttp.ClientSession` dédiée avec son propre cookie jar (par contrat) pour éviter toute fuite de cookies entre intégrations.

## Sécurité

Cette intégration manipule vos identifiants du portail. En interne :
- Le mot de passe est stocké dans le config entry de Home Assistant (chiffré au repos par HA)
- Les redirections HTTP sont validées contre le nom d'hôte du portail — toute redirection hors du portail est refusée pour empêcher la fuite d'identifiants (protection contre les attaques de type CVE-2018-18074)
- Les downgrades HTTPS→HTTP sont refusés
- Toutes les requêtes API utilisent TLS

## Avertissement

Ce projet effectue de la rétro-ingénierie des API web des portails clients à des fins personnelles. Il n'est ni supporté, ni sponsorisé, ni endorsé par SEM, SEMM, Vivaigo ou SOMEI. Les clés applicatives embarquées dans les bundles JS publics de chaque portail sont réutilisées telles quelles — elles peuvent changer sans préavis, auquel cas l'intégration devra être mise à jour.

À utiliser à vos propres risques.

## Contribuer

Les contributions sont les bienvenues. Merci de :
1. Ouvrir une issue avant tout changement non trivial
2. Lancer `pytest tests/` et vérifier que tous les tests passent
3. Suivre le style existant (formaté avec Black)

## Licence

[MIT](LICENSE)
