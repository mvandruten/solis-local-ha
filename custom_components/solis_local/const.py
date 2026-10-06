"""Constants for the Solis Inverter Local integration."""

DOMAIN = "solis_local"
NAME = "Solis Inverter Local"

CONF_DATALOGGER_IP = "datalogger_ip"
CONF_DATALOGGER_PASSWORD = "datalogger_password"
CONF_POLL_INTERVAL = "poll_interval"
CONF_REFRESH_TIMEOUT = "refresh_timeout"
CONF_REFRESH_MODE = "refresh_mode"

DEFAULT_POLL_INTERVAL = 300
DEFAULT_REFRESH_TIMEOUT = 10
DEFAULT_REFRESH_MODE = "watch"

REFRESH_MODES = ("watch", "reboot", "none")

CGI_PATH = "/inverter.cgi"
RESTART_PATH = "/restart.cgi"

SERVICE_FORCE_REFRESH = "force_refresh"