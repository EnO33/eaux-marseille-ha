#!/usr/bin/env python3
"""
Entry point for the Home Assistant command_line sensor.

Outputs a single JSON object to stdout. Home Assistant reads this output
and maps it to sensor state and attributes.

Exit codes
----------
0 : Success — valid JSON printed to stdout.
1 : Failure — error JSON printed to stdout.

Example Home Assistant configuration (configuration.yaml)::

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
"""

import json
import logging
import sys
from pathlib import Path

# Allow running the script directly from the repo root or from HA /config/scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eaux_marseille import ApiError, AuthenticationError, Config, EauxDeMarseilleClient


def main() -> int:
    try:
        config = Config.from_env()
    except EnvironmentError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1

    logging.basicConfig(level=config.log_level)

    try:
        with EauxDeMarseilleClient(config) as client:
            client.authenticate()
            data = client.fetch_consumption_summary()
    except (AuthenticationError, ApiError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    except Exception as exc:  # noqa: BLE001
        logging.exception("Unexpected error")
        print(json.dumps({"error": f"Unexpected error: {exc}"}))
        return 1

    print(json.dumps(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
