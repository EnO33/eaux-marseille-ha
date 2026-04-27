# Eaux de Marseille — Home Assistant integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Release](https://img.shields.io/github/v/release/EnO33/eaux-marseille-ha)](https://github.com/EnO33/eaux-marseille-ha/releases)
[![Tests](https://github.com/EnO33/eaux-marseille-ha/actions/workflows/tests.yml/badge.svg)](https://github.com/EnO33/eaux-marseille-ha/actions/workflows/tests.yml)
[![Quality scale: gold](https://img.shields.io/badge/quality--scale-gold-yellow.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

🇫🇷 [Lire en français](README.fr.md)

Unofficial Home Assistant integration for the customer portals of the three water utilities serving the Marseille area. Despite the geographic overlap, each utility runs its own portal and serves a distinct set of communes:

- **Société des Eaux de Marseille (SEM)** — Ventabren, Bandol, Vitrolles, Trets, Fuveau, Cabriès, Bouc-Bel-Air, Le Puy-Sainte-Réparade, Forcalquier and other peripheral communes — [`espaceclients.eauxdemarseille.fr`](https://espaceclients.eauxdemarseille.fr)
- **Eau de Marseille Métropole (SEMM)** — Marseille proper, La Ciotat, Cassis, Carnoux, Carry-le-Rouet, Allauch, Marignane, Septèmes-les-Vallons, Gémenos and others — [`espaceclients.eaudemarseille-metropole.fr`](https://espaceclients.eaudemarseille-metropole.fr)
- **Vivaigo** — Salon-de-Provence, Berre-l'Étang, Lambesc, Eyguières, Pélissanne, Velaux, Rognac, Sénas, Lançon-de-Provence and the wider Salon/Berre area — [`espaceclients.vivaigo.fr`](https://espaceclients.vivaigo.fr)

The three utilities share the same back-end stack (operated by SOMEI/Veolia), so this integration handles all of them — you pick yours from a dropdown when configuring the integration.

It pulls your water consumption from the chosen portal every hour and exposes it as Home Assistant sensors, plus monthly statistics that plug into the Energy dashboard.

> This project is not affiliated with or endorsed by Société des Eaux de Marseille, Eau de Marseille Métropole, Vivaigo or SOMEI.

---

## Features

- UI-based configuration — no YAML editing
- Supports SEM, SEMM and Vivaigo via a single integration (provider selector in the form)
- 12 sensors per contract (consumption, meter index, daily average, billing periods)
- Historical monthly statistics imported back to 2024 — works with the HA Energy dashboard
- Automatic hourly refresh with retry/backoff on transient errors
- Reauthentication flow when the portal password changes — no need to delete and recreate the integration
- Secure: cross-origin redirects refused (CVE-2018-18074-class protection)
- French and English translations
- Home Assistant **Gold** quality scale (every required rule satisfied; 98% test coverage)

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

## Use cases

### Track water on the Energy dashboard

The most common setup. Once the integration is configured:

1. Go to **Settings → Dashboards → Energy**
2. Add a **Water source** in the Water consumption block
3. Pick the imported statistic `eaux_marseille:monthly_consumption_<contract>`
4. Optionally set a price per m³ to track cost alongside consumption

You'll get monthly consumption history (back to 2024) plus the live current month, side-by-side with electricity and gas if you also track them.

### Monthly bill alert

Trigger a notification when the current month exceeds your average. Example automation:

```yaml
alias: "Water: monthly consumption above average"
trigger:
  - platform: numeric_state
    entity_id: sensor.eaux_de_marseille_<contract>_mois_en_cours
    above: 12  # adjust to your typical monthly consumption in m³
action:
  - service: notify.mobile_app_<your_phone>
    data:
      title: "Water alert"
      message: >-
        Monthly water consumption is {{ states('sensor.eaux_de_marseille_<contract>_mois_en_cours') }} m³,
        above the {{ 12 }} m³ threshold.
```

### Daily consumption snapshot

The portal does not expose daily consumption directly, but you can derive a rolling daily figure from the meter index. Example template sensor in `configuration.yaml`:

```yaml
template:
  - sensor:
      - name: "Water consumption today"
        unit_of_measurement: "L"
        device_class: water
        state_class: total_increasing
        state: >-
          {% set now = states('sensor.eaux_de_marseille_<contract>_index_compteur') | float(0) %}
          {% set start = states.sensor.eaux_de_marseille_<contract>_index_compteur.attributes.last_changed | as_local %}
          {{ ((now - start) * 1000) | round(0) }}
```

(Resets to 0 at midnight via a daily automation that stores the previous day's index.)

### Long-term trend chart

Drop a `statistics-graph` card on a dashboard:

```yaml
type: statistics-graph
title: Water consumption (monthly)
entities:
  - eaux_marseille:monthly_consumption_<contract>
period: month
stat_types:
  - sum
days_to_show: 365
```

This pulls directly from the imported external statistic — no separate template needed.

## Requirements

- Home Assistant 2025.4 or later
- An active account on one of the three customer portals (SEM, SEMM or Vivaigo)
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
| **Water utility** | Yes | Dropdown | Which utility serves your address | SEM (peripheral communes: Ventabren, Bandol, Vitrolles, Trets…), SEMM (Marseille proper, La Ciotat, Cassis, Marignane…) or Vivaigo (Salon, Berre, Lambesc, Eyguières…). Use the same one whose customer portal you log in to. |
| **Email** | Yes | Email address | Login email for the portal | The address you use on the portal of your utility |
| **Password** | Yes | String | Portal password | Same as the website. Stored encrypted at rest by Home Assistant. |
| **Contract number** | Yes | Numeric (typically 7 digits) | Your contract identifier | Visible on your water bills under "Numéro de contrat" and in the portal URL after login (`https://<portal>/#/dashboard/<contract>`) |

The combination *(utility + contract number)* acts as the unique identifier for the integration entry — you cannot add the same contract on the same portal twice.

### Data update

The integration polls the customer portal **once per hour**. This interval is intentionally conservative: water consumption updates on the portal are themselves infrequent (most meters telemeter once or twice a day, billed readings are monthly), so a higher polling rate would just hammer the portal without surfacing new data.

After the initial setup the integration also runs a **historical backfill** of monthly consumption from January 2024 onward, in the background. Subsequent restarts reuse what's already in the recorder and only fetch the current year forward, so the backfill cost is paid once.

Authentication itself uses a cached session token that's reused across polls (since v1.12.0). The full 5-step handshake against the portal only happens at startup or when the portal returns 401/403 (token expired). Steady-state load on the portal is 3 HTTP requests per hour per contract.

If a poll fails — portal down, network blip, etc. — Home Assistant retries on the next hour and surfaces the entity as `unavailable` until the next successful refresh.

### Reauthentication

If you change your portal password, Home Assistant will detect the failed authentication on the next polling cycle (≤ 1 hour) and show a notification. Click it to re-enter the new password — the integration keeps the contract, sensors, and historical statistics intact.

### Reconfiguration

If you need to change the email address, contract number or even the water utility (e.g. you moved house) without losing the sensor history and imported statistics:

1. **Settings → Devices & Services → Eaux de Marseille → ⋮ menu → Reconfigure**
2. Edit any field; password is required (same one as the portal)
3. Submit — the integration validates the new credentials and reloads the entry in place

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
- Network issue → check that Home Assistant can reach the portal host of your selected utility (`espaceclients.eauxdemarseille.fr`, `espaceclients.eaudemarseille-metropole.fr` or `espaceclients.vivaigo.fr`)

### Reporting bugs

Please include:
- Home Assistant version
- Integration version (from `manifest.json`)
- Relevant log lines (with `custom_components.eaux_marseille: debug`)
- Whether the issue happens at setup or during regular operation

[Open an issue →](https://github.com/EnO33/eaux-marseille-ha/issues)

## How it works

All three portals are AngularJS SPAs backed by the same REST API (operated by SOMEI/Veolia). Only the host name and the embedded application credentials differ between providers. Authentication follows a five-step flow:

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

## Known limitations

### No real-time data

Water meters telemeter once or twice a day, and the customer portal aggregates that data. There is no equivalent of an electricity smart-meter live reading — the freshest value you'll see is yesterday's. The 1-hour polling interval reflects the portal's update rhythm, not a constraint of the integration.

### Application credentials may rotate without notice

The integration impersonates the portal's own JavaScript bundle, including the static `ClientId` and `AccessKey` it embeds. SOMEI/Veolia could rotate these at any time without warning, in which case authentication will start failing and the integration will need a release with the new keys. If you see `Token generation failed` after a previously working setup, this is the most likely cause — please [open an issue](https://github.com/EnO33/eaux-marseille-ha/issues) so the constants can be updated.

### Provider-territory mapping may shift

The list of communes served by each utility (SEM, SEMM, Vivaigo) is set by their public-service contracts and reorganises occasionally. The integration's documentation reflects the situation at release time; if you live in an area that just changed utility, the only effect is that you should pick the new one in the dropdown — the underlying portal API is shared.

### One contract per entry

Each integration entry handles a single contract. If you have several contracts (a primary residence and a secondary one, say), add the integration once per contract — they show up as separate devices with their own sensor history.

### Historical statistics start at 2024

The historical-statistics backfill goes back to January 2024 only. Earlier data is not exposed by the portal in the format the integration uses for the recorder.

### Energy dashboard cost tracking is per-m³ flat rate

Home Assistant's Energy dashboard takes a single price per m³ to compute cost. Real water bills include a fixed subscription portion plus tiered volumetric pricing — the dashboard cannot model that, so the displayed cost is an approximation.

## Disclaimer

This project reverse-engineers the customer portals' web API for personal use. It is not supported, sponsored, or endorsed by SEM, SEMM, Vivaigo or SOMEI. The application keys embedded in each portal's public JS bundle are reused as-is — they may change without notice, in which case the integration will need to be updated.

Use at your own risk.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the local test setup, style and quality gates.

Quick checklist:
1. Open an issue first for non-trivial changes
2. Install the full test stack: `pip install -r requirements_test.txt`
3. Run `python -m pytest tests/` and confirm all 49 tests pass (no skips)
4. Run `python -m ruff check`, `python -m ruff format --check`, `python -m mypy custom_components/eaux_marseille/`

## License

[MIT](LICENSE)
