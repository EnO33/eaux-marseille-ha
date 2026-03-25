# eaux-marseille-ha

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Unofficial Home Assistant integration for the [Eaux de Marseille](https://www.eauxdemarseille.fr) customer portal (`espaceclients.eauxdemarseille.fr`).

> This project is not affiliated with or endorsed by Eaux de Marseille or the Société des Eaux de Marseille (SEM/SOMEI).

---

## Features

- Configuration via the Home Assistant UI — no YAML editing required
- 12 sensor entities per contract (consumption, index, averages, reading dates)
- Automatic polling every hour
- French and English translations

## Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| `sensor.eaux_marseille_current_month` | Current month consumption | m³ |
| `sensor.eaux_marseille_current_month_litres` | Current month consumption | L |
| `sensor.eaux_marseille_current_year` | Year-to-date consumption | m³ |
| `sensor.eaux_marseille_meter_index` | Current meter index | m³ |
| `sensor.eaux_marseille_daily_average` | Average daily consumption | m³ |
| `sensor.eaux_marseille_last_reading` | Last billed consumption | m³ |
| `sensor.eaux_marseille_last_reading_litres` | Last billed consumption | L |
| `sensor.eaux_marseille_last_reading_date` | Date of last billed reading | — |
| `sensor.eaux_marseille_last_reading_period` | Days in last billed period | days |
| `sensor.eaux_marseille_previous_reading` | Previous billed consumption | m³ |
| `sensor.eaux_marseille_previous_reading_date` | Date of previous reading | — |
| `sensor.eaux_marseille_total_readings` | Total available readings | — |

## Requirements

- Home Assistant 2024.1 or later
- An active account on [espaceclients.eauxdemarseille.fr](https://espaceclients.eauxdemarseille.fr)

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations → Custom repositories**
3. Add `https://github.com/EnO33/eaux-marseille-ha` with category **Integration**
4. Search for **Eaux de Marseille** and install
5. Restart Home Assistant

### Manual

Copy the `custom_components/eaux_marseille` folder to your Home Assistant `config/custom_components/` directory and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Eaux de Marseille**
3. Enter your portal credentials and contract number

Your contract number is visible on your bills and in the portal URL after login
(e.g. `https://espaceclients.eauxdemarseille.fr/#/dashboard/1234567`).

## How it works

The portal at `espaceclients.eauxdemarseille.fr` is an AngularJS SPA backed by a REST API. Authentication follows a three-step flow:

1. A `POST` to `/webapi/Acces/generateToken` exchanges a static application key (embedded in the portal JS bundle) for a short-lived token.
2. A `POST` to `/webapi/Utilisateur/authentification` exchanges user credentials and the short-lived token for a session token.
3. Subsequent requests carry the session token in both the `token` header and the `aelToken` cookie, alongside a per-request `ConversationId` header.

## Disclaimer

This project reverse-engineers the portal API for personal use. It is not supported or endorsed by Eaux de Marseille. Use it at your own risk.

## License

MIT
