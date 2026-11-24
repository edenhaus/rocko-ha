"""The rocko integration: the rocko media daemon as the cameras' WebRTC provider."""

from dataclasses import dataclass
import logging

from rocko_client import ApiVersionError, RockoClient, RockoError

from homeassistant.components.camera import (
    DOMAIN as CAMERA_DOMAIN,
    async_register_webrtc_provider,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from .connection import RockoConnection
from .const import CONF_BINARY, CONF_MODE, CONF_SOCKET, DOMAIN, MODE_SPAWN
from .issues import async_delete_sandbox_issues, async_update_sandbox_issues
from .provider import DATA_HANDOVER, RockoWebRTCProvider, stream_id_for
from .server import RockoServer, RockoServerError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RockoData:
    """What a loaded entry keeps."""

    connection: RockoConnection
    provider: RockoWebRTCProvider


type RockoConfigEntry = ConfigEntry[RockoData]


async def async_setup_entry(hass: HomeAssistant, entry: RockoConfigEntry) -> bool:
    """Connect to rocko, or run it, and register it as the WebRTC provider."""
    endpoint: str | RockoServer
    if entry.data[CONF_MODE] == MODE_SPAWN:
        endpoint = RockoServer(hass, entry.data[CONF_BINARY])
    else:
        endpoint = entry.data[CONF_SOCKET]
    connection = RockoConnection(hass, entry, endpoint)
    try:
        await connection.async_start()
    except ApiVersionError as err:
        await connection.async_stop()
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="api_version",
            translation_placeholders={"daemon": err.daemon, "client": err.client},
        ) from err
    except (RockoError, RockoServerError) as err:
        await connection.async_stop()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    provider = RockoWebRTCProvider(hass, entry, connection)
    entry.runtime_data = RockoData(connection, provider)

    async def _async_on_connected(client: RockoClient) -> None:
        """Check the new daemon's sandbox, then take the sessions back."""
        if (info := connection.info) is not None:
            async_update_sandbox_issues(hass, info.sandbox)
        await provider.async_on_connected(client)

    connection.async_add_listener(_async_on_connected)

    async def _async_on_stop(_event: Event) -> None:
        """Stop the daemon with Home Assistant."""
        await connection.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_on_stop)
    )
    entry.async_on_unload(async_register_webrtc_provider(hass, provider))

    # Sessions the previous entry left behind are adopted on this connection.
    provider.async_take_handover()
    if (client := connection.client) is not None:
        await _async_on_connected(client)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RockoConfigEntry) -> bool:
    """Unload the entry; a daemon Home Assistant runs is stopped."""
    data = entry.runtime_data
    # A daemon that keeps running keeps the sessions for its grace period,
    # so the next entry can adopt them.
    data.provider.async_unload(handover=data.connection.server is None)
    await data.connection.async_stop()
    async_delete_sandbox_issues(hass)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: RockoConfigEntry) -> None:
    """Delete the cameras' streams from a daemon that keeps running."""
    # No entry follows that could adopt the sessions left behind.
    hass.data.pop(DATA_HANDOVER, None)
    if entry.data[CONF_MODE] == MODE_SPAWN:
        return
    camera_streams = {
        stream_id_for(entity_id)
        for entity_id in hass.states.async_entity_ids(CAMERA_DOMAIN)
    }
    client = RockoClient(entry.data[CONF_SOCKET])
    try:
        await client.connect()
        try:
            streams = await client.stream.list()
            for stream_id in sorted(camera_streams & streams.streams.keys()):
                await client.stream.delete(stream_id=stream_id)
                _LOGGER.debug("Deleted stream %s", stream_id)
        finally:
            await client.close()
    except RockoError as err:
        _LOGGER.warning("Could not delete the cameras' streams from rocko: %s", err)
