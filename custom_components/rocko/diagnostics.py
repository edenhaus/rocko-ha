"""Diagnostics support for rocko.

Stream source URLs carry camera credentials. The daemon already reports
them without their userinfo, but every URL and credential is redacted here
as well, so a diagnostics download names neither cameras' addresses nor
TURN credentials.

A remote entry's client key, both certificates and the daemon's host are
redacted too. The certificates' pins are not secret and name both ends as
the daemon lists them (`rocko ctl clients list`), so they are reported.
"""

from typing import Any

from rocko_client import RockoError, certificate_pin

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import RockoConfigEntry
from .const import (
    CONF_CERTIFICATE,
    CONF_MODE,
    CONF_PRIVATE_KEY,
    CONF_SERVER_CERTIFICATE,
    MODE_REMOTE,
)

TO_REDACT = {
    CONF_CERTIFICATE,
    CONF_HOST,
    CONF_PRIVATE_KEY,
    CONF_SERVER_CERTIFICATE,
    "credential",
    "password",
    "resolved_url",
    "url",
    "urls",
    "username",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RockoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    connection = entry.runtime_data.connection
    data: dict[str, Any] = {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "connected": connection.client is not None,
        "server_pid": None if connection.server is None else connection.server.pid,
        "sessions": entry.runtime_data.provider.session_count,
        "info": None if connection.info is None else connection.info.to_dict(),
    }
    if entry.data[CONF_MODE] == MODE_REMOTE:
        data["client_pin"] = certificate_pin(entry.data[CONF_CERTIFICATE])
        data["server_pin"] = certificate_pin(entry.data[CONF_SERVER_CERTIFICATE])
    if (client := connection.client) is not None:
        try:
            data["metrics"] = (await client.metrics.get()).to_dict()
            data["streams"] = (await client.stream.list()).to_dict()["streams"]
        except RockoError as err:
            data["error"] = str(err)
    return async_redact_data(data, TO_REDACT)
