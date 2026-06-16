# Vulture whitelist.
#
# These names look "unused" to vulture because they are called by the Home
# Assistant framework (config-entry lifecycle hooks, the config-flow steps,
# the coordinator update method, the sensor platform entry point, entity
# attributes/properties), not by our own code. Listing them here marks them
# as used so vulture only reports genuinely dead code.
#
# Regenerate with:  vulture custom_components/eaux_marseille --make-whitelist

CONFIG_SCHEMA  # __init__.py
async_setup  # __init__.py
config  # __init__.py
async_setup_entry  # __init__.py / sensor.py
async_unload_entry  # __init__.py
EauxDeMarseilleConfigFlow  # config_flow.py
VERSION  # config_flow.py
_.async_step_user  # config_flow.py
_.async_step_reauth  # config_flow.py
entry_data  # config_flow.py
_.async_step_reconfigure  # config_flow.py
_._async_update_data  # coordinator.py
async_get_config_entry_diagnostics  # diagnostics.py
PARALLEL_UPDATES  # sensor.py
_._attr_unique_id  # sensor.py
_._attr_device_info  # sensor.py
_.native_value  # sensor.py
