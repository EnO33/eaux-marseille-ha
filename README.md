# Eaux de Marseille — Home Assistant integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Release](https://img.shields.io/github/v/release/EnO33/eaux-marseille-ha)](https://github.com/EnO33/eaux-marseille-ha/releases)
[![Tests](https://github.com/EnO33/eaux-marseille-ha/actions/workflows/tests.yml/badge.svg)](https://github.com/EnO33/eaux-marseille-ha/actions/workflows/tests.yml)
[![Quality scale: silver](https://img.shields.io/badge/quality--scale-silver-silver.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

🇫🇷 [Lire en français](README.fr.md)

Unofficial Home Assistant integration for the [Eaux de Marseille](https://www.eauxdemarseille.fr) customer portal (`espaceclients.eauxdemarseille.fr`).

It pulls your water consumption from the portal every hour and exposes it as Home Assistant sensors, plus monthly statistics that plug into the Energy dashboard.

> This project is not affiliated with or endorsed by Eaux de Marseille or the Société des Eaux de Marseille (SEM/SOMEI).

---

## Features

- UI-based configuration — no YAML editing
- 12 sensors per contract (consumption, meter index, daily average, billing periods)
- Historical monthly statistics imported back to 2024 — works with the HA Energy dashboard
- Automatic hourly refresh with retry/backoff on transient errors
- Reauthentication flow when the portal password changes — no need to delete and recreate the integration
- Secure: cross-origin redirects refused (CVE-2018-18074-class protection)
- French and English translations
- Home Assistant **Silver** quality scale (98% test coverage, all required rules satisfied)

## Sensors

| Entity | Description | Unit |
|---|---|---|
| `sensor.eaux_de_marseille_<contract>_mois_en_cours` | Current month consumption | m³ |
| `sensor.eaux_de_marseille_<contract>_mois_en_cours_litres` | Current month consumption | L |
| `sensor.eaux_de_marseille_<contract>_annee_en_cours` | Year-to-date consumption | m³ |
| `sensor.eaux_de_marseille_<contract>_index_compteur` | Current meter index | m³ |
| `sensor.eaux_de_marseille_<contract>_moyenne_journaliere` | Daily average | m³ |
| `sensor.eaux_de_marseille_<contract>_dernier_releve` | Last billed consumption | m³ |
| `sensor.eaux_de_marseille_<contract>_dernier_releve_litres` | Last billed consumption | L |
| `sensor.eaux_de_marseille_<contract>_date_du_dernier_releve` | Last billed reading date | — |
| `sensor.eaux_de_marseille_<contract>_duree_de_la_periode` | Days in last billed period | days |
| `sensor.eaux_de_marseille_<contract>_releve_precedent` | Previous billed consumption | m³ |
| `sensor.eaux_de_marseille_<contract>_date_du_releve_precedent` | Previous reading date | — |
| `sensor.eaux_de_marseille_<contract>_nombre_de_releves` | Total available readings | — |

A **monthly external statistic** is also imported under the ID `eaux_marseille:monthly_consumption_<contract>` — usable in `statistics-graph` cards and the Energy dashboard.

## Requirements

- Home Assistant 2024.1 or later
- An active account on [espaceclients.eauxdemarseille.fr](https://espaceclients.eauxdemarseille.fr)
- Your contract number (visible on bills or in the portal URL after login)

## Installation

### Via HACS (recommended)

1. In Home Assistant, open **HACS**
2. Go to **Integrations → ⋮ menu → Custom repositories**
3. Add `https://github.com/EnO33/eaux-marseille-ha` with category **Integration**
4. Search for **Eaux de Marseille** in the HACS integrations list and install
5. **Restart Home Assistant**
6. Continue to [Configuration](#configuration)

### Manual installation

1. Download the latest release from the [releases page](https://github.com/EnO33/eaux-marseille-ha/releases)
2. Copy the `custom_components/eaux_marseille` folder into your Home Assistant `config/custom_components/` directory
3. **Restart Home Assistant**
4. Continue to [Configuration](#configuration)

## Configuration

1. In Home Assistant, go to **Settings → Devices & Services → ➕ Add Integration**
2. Search for **Eaux de Marseille**
3. Fill the form (see [parameters](#configuration-parameters) below)
4. Submit

The integration will validate the credentials, then expose the device and its sensors. The first run also imports the available historical monthly statistics (this happens in the background and may take a few seconds).

### Configuration parameters

| Field | Required | Format | Description | Where to find it |
|---|---|---|---|---|
| **Email** | Yes | Email address | Login email for the portal | The address you use on [espaceclients.eauxdemarseille.fr](https://espaceclients.eauxdemarseille.fr) |
| **Password** | Yes | String | Portal password | Same as the website. Stored encrypted at rest by Home Assistant. |
| **Contract number** | Yes | Numeric (typically 7 digits) | Your contract identifier | Visible on your water bills under "Numéro de contrat" and in the portal URL after login (`https://espaceclients.eauxdemarseille.fr/#/dashboard/<contract>`) |

The contract number also acts as the unique identifier for the integration entry — you cannot add the same contract twice.

### Reauthentication

If you change your portal password, Home Assistant will detect the failed authentication on the next polling cycle (≤ 1 hour) and show a notification. Click it to re-enter the new password — the integration keeps the contract, sensors, and historical statistics intact.

### Multiple contracts

You can add the integration multiple times, once per contract. Each contract becomes a separate device with its own sensors.

## Removal

1. In Home Assistant, go to **Settings → Devices & Services**
2. Find the **Eaux de Marseille** integration card
3. Click the **⋮ menu → Delete**
4. (Optional) To also remove the imported historical statistics, go to **Developer tools → Statistics** and delete entries matching `eaux_marseille:monthly_consumption_*`
5. (Optional, manual install only) Remove the `custom_components/eaux_marseille` folder

## Troubleshooting

### "Unable to connect to the portal" on setup

Enable info-level logs by adding to your `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.eaux_marseille: info
```

Restart Home Assistant, retry the setup, then check `home-assistant.log` for `Authentication: step X/Y` lines. The step at which the failure occurs tells you whether it's a network, credentials, or portal-side issue.

If you see no `Authentication` lines at all, the integration isn't being loaded — usually a HACS cache issue. Verify the version in `/config/custom_components/eaux_marseille/manifest.json` matches the latest release.

### Sensors show "unavailable"

Check the integration card for an error message. Common causes:
- Portal session expired or password changed → remove and re-add the integration
- Portal is down → Home Assistant will retry on the next refresh
- Network issue → check that Home Assistant can reach `espaceclients.eauxdemarseille.fr`

### Reporting bugs

Please include:
- Home Assistant version
- Integration version (from `manifest.json`)
- Relevant log lines (with `custom_components.eaux_marseille: debug`)
- Whether the issue happens at setup or during regular operation

[Open an issue →](https://github.com/EnO33/eaux-marseille-ha/issues)

## How it works

The portal at `espaceclients.eauxdemarseille.fr` is an AngularJS SPA backed by a REST API. Authentication follows a five-step flow:

1. **GET** the portal landing page to acquire a session cookie
2. **POST** `/webapi/Acces/generateToken` — exchanges a static application key (embedded in the portal JS bundle) for a short-lived token
3. **POST** `/webapi/Utilisateur/authentification` — exchanges user credentials and the short-lived token for a session token (`tokenAuthentique`)
4. **GET** `/webapi/Abonnement/getContratParDefaut/` — fetches the default contract metadata
5. The session token is set in the `aelToken` cookie and a serialized context is set in the `AEL_CONTEXT` cookie

Subsequent requests carry the session token in the `token` HTTP header alongside a per-request `ConversationId` header. The integration uses a dedicated `aiohttp.ClientSession` with its own cookie jar (per contract) so cookies don't leak across integrations.

## Security

This integration handles your portal credentials. Internally:
- The password is stored in the Home Assistant config entry (encrypted at rest by HA)
- HTTP redirects are validated against the portal hostname — off-portal redirects are refused to prevent credential leakage (CVE-2018-18074-class protection)
- HTTPS→HTTP scheme downgrades are refused
- All API requests use TLS

## Disclaimer

This project reverse-engineers the portal's web API for personal use. It is not supported, sponsored, or endorsed by Eaux de Marseille or the Société des Eaux de Marseille. The application keys embedded in the portal's public JS bundle are reused as-is — they may change without notice, in which case the integration will need to be updated.

Use at your own risk.

## Contributing

Contributions are welcome. Please:
1. Open an issue first for non-trivial changes
2. Run `pytest tests/` and ensure all tests pass
3. Follow the existing code style (Black-formatted)

## License

[MIT](LICENSE)
