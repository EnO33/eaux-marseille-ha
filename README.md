# eaux-marseille-ha

Unofficial Home Assistant integration for the [Eaux de Marseille](https://www.eauxdemarseille.fr) customer portal.

Retrieves water consumption data from `espaceclients.eauxdemarseille.fr` and exposes it as a [`command_line`](https://www.home-assistant.io/integrations/command_line/) sensor in Home Assistant.

> This project is not affiliated with or endorsed by Eaux de Marseille or the Société des Eaux de Marseille (SEM/SOMEI).

---

## Features

- Current month consumption (m3 and litres)
- Year-to-date consumption
- Last and previous billed readings with dates and period length
- Current meter index
- Daily average consumption
- Works with any contract served by the `espaceclients.eauxdemarseille.fr` portal (GSEM/SOMEI)

## Requirements

- Python 3.11+
- Home Assistant with [`command_line`](https://www.home-assistant.io/integrations/command_line/) integration
- An active account on [espaceclients.eauxdemarseille.fr](https://espaceclients.eauxdemarseille.fr)

## Installation

### 1. Copy files to Home Assistant

Place the following files inside your Home Assistant configuration directory:

```
config/
  scripts/
    fetch.py
    eaux_marseille/
      __init__.py
      client.py
      config.py
    .env
```

### 2. Install dependencies

```bash
pip3 install -r requirements.txt --break-system-packages
```

Or inside the Home Assistant container:

```bash
docker exec homeassistant pip3 install requests python-dotenv
```

### 3. Configure credentials

Copy `.env.example` to `config/scripts/.env` and fill in your values:

```bash
cp .env.example config/scripts/.env
```

```ini
EDM_LOGIN=your.email@example.com
EDM_PASSWORD=your_password
EDM_CONTRACT_ID=1234567
```

Your contract ID is visible on your bills and in the portal URL after login
(e.g. `https://espaceclients.eauxdemarseille.fr/#/dashboard/1234567`).

### 4. Test the script

```bash
python3 config/scripts/fetch.py
```

Expected output:

```json
{
  "index_m3": 521.0,
  "last_reading_m3": 18.0,
  "last_reading_litres": 21000,
  "last_reading_date": "2026-03-05",
  "last_reading_days": 94,
  "daily_average_m3": 0.1915,
  "previous_reading_m3": 18.0,
  "previous_reading_date": "2025-12-01",
  "current_month_m3": 5.156,
  "current_month_litres": 6230,
  "current_year_m3": 21.43,
  "total_readings": 10
}
```

### 5. Add to Home Assistant

Add the following to your `configuration.yaml`:

```yaml
command_line:
  - sensor:
      name: "Eaux de Marseille"
      command: "python3 /config/scripts/fetch.py"
      scan_interval: 3600
      value_template: "{{ value_json.current_month_m3 }}"
      unit_of_measurement: "m3"
      device_class: water
      state_class: measurement
      json_attributes:
        - index_m3
        - last_reading_m3
        - last_reading_litres
        - last_reading_date
        - last_reading_days
        - daily_average_m3
        - previous_reading_m3
        - previous_reading_date
        - current_month_litres
        - current_year_m3
        - total_readings
```

Restart Home Assistant. The sensor `sensor.eaux_de_marseille` will appear after the first polling cycle.

## Configuration reference

| Variable        | Required | Default   | Description                                |
|-----------------|----------|-----------|--------------------------------------------|
| `EDM_LOGIN`     | yes      | —         | Portal login (email)                       |
| `EDM_PASSWORD`  | yes      | —         | Portal password                            |
| `EDM_CONTRACT_ID` | yes    | —         | Contract number                            |
| `EDM_TIMEOUT`   | no       | `15`      | HTTP request timeout in seconds            |
| `EDM_LOG_LEVEL` | no       | `WARNING` | Python log level (DEBUG, INFO, WARNING...) |

## Sensor attributes

| Attribute              | Description                                    |
|------------------------|------------------------------------------------|
| `index_m3`             | Current meter index (m3)                       |
| `last_reading_m3`      | Last billed consumption (m3)                   |
| `last_reading_litres`  | Last billed consumption (litres)               |
| `last_reading_date`    | Date of the last billed reading                |
| `last_reading_days`    | Number of days in the last billed period       |
| `daily_average_m3`     | Average daily consumption (m3)                 |
| `previous_reading_m3`  | Previous billed consumption (m3)               |
| `previous_reading_date`| Date of the previous billed reading            |
| `current_month_m3`     | Consumption for the current month (m3)         |
| `current_month_litres` | Consumption for the current month (litres)     |
| `current_year_m3`      | Year-to-date consumption (m3)                  |
| `total_readings`       | Total number of available historical readings  |

## How it works

The portal at `espaceclients.eauxdemarseille.fr` is an AngularJS SPA backed by a REST API. Authentication follows a three-step flow:

1. A `POST` to `/webapi/Acces/generateToken` exchanges a static application key (embedded in the portal JS bundle) for a short-lived token.
2. A `POST` to `/webapi/Utilisateur/authentification` exchanges user credentials and the short-lived token for a session token (`aelToken`).
3. Subsequent requests carry the session token in both the `token` header and the `aelToken` cookie, alongside a per-request `ConversationId` header.

## Disclaimer

This project reverse-engineers the portal API for personal use. It is not supported or endorsed by Eaux de Marseille. Use it at your own risk. Do not use it for commercial purposes.

## License

MIT
