"""Tests for setting up and unloading the rocko integration."""

from unittest.mock import MagicMock, Mock, call, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from rocko_client import ApiVersionError, CannotConnectError, ConnectionClosedError

from custom_components.rocko import RockoData
from custom_components.rocko.provider import DATA_HANDOVER, RockoWebRTCProvider
from custom_components.rocko.server import RockoServerError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import MockCamera
from .conftest import (
    IDENTITY,
    REMOTE_HOST,
    REMOTE_PORT,
    SERVER_CERTIFICATE,
    SOCKET_PATH,
    SPAWN_SOCKET_PATH,
    MockClients,
    async_setup_camera,
)


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_clients: MockClients
) -> None:
    """The entry connects to the socket, and unloading closes the connection."""
    entry = init_integration
    assert entry.state is ConfigEntryState.LOADED
    assert mock_clients.paths == [SOCKET_PATH]
    client = mock_clients.last
    client.connect.assert_awaited_once()
    client.info.assert_awaited_once()
    assert isinstance(entry.runtime_data, RockoData)
    assert isinstance(entry.runtime_data.provider, RockoWebRTCProvider)
    connection = entry.runtime_data.connection
    assert connection.client is client
    assert connection.server is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    client.close.assert_awaited_once()
    assert connection.client is None


async def test_setup_cannot_connect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """A daemon that cannot be reached makes setup retry."""
    mock_clients.connect_errors = [CannotConnectError("refused")]
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_config_entry.reason == "Could not connect to rocko: refused"


async def test_setup_info_fails(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """A connection that drops before `info` is answered is closed and retried."""
    mock_clients.configure = [
        lambda client: setattr(
            client.info, "side_effect", ConnectionClosedError("closed")
        )
    ]
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_clients.last.close.assert_awaited_once()


async def test_setup_api_version(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """A daemon of another API version fails the setup for good."""
    mock_clients.connect_errors = [ApiVersionError("0.2.0", "0.1.0")]
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_config_entry.reason == (
        "rocko speaks control API version 0.2.0, but this integration needs a "
        "version compatible with 0.1.0"
    )


async def test_stop_with_home_assistant(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_clients: MockClients
) -> None:
    """Home Assistant stopping closes the connection."""
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    mock_clients.last.close.assert_awaited_once()
    assert init_integration.runtime_data.connection.client is None


async def test_spawn_setup_and_unload(
    hass: HomeAssistant,
    mock_spawn_entry: MockConfigEntry,
    mock_server: MagicMock,
    mock_clients: MockClients,
) -> None:
    """In spawn mode the daemon is started first and stopped on unload."""
    mock_spawn_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_spawn_entry.entry_id)
    assert mock_spawn_entry.state is ConfigEntryState.LOADED
    mock_server.async_ensure_running.assert_awaited_once_with(restart=False)
    assert mock_clients.paths == [SPAWN_SOCKET_PATH]
    assert mock_spawn_entry.runtime_data.connection.server is mock_server

    assert await hass.config_entries.async_unload(mock_spawn_entry.entry_id)
    mock_clients.last.close.assert_awaited_once()
    mock_server.async_stop.assert_awaited_once()


async def test_spawn_setup_fails(
    hass: HomeAssistant,
    mock_spawn_entry: MockConfigEntry,
    mock_server: MagicMock,
    mock_clients: MockClients,
) -> None:
    """A daemon that does not start makes setup retry, and is stopped."""
    mock_server.async_ensure_running.side_effect = RockoServerError("exited")
    mock_spawn_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_spawn_entry.entry_id)
    assert mock_spawn_entry.state is ConfigEntryState.SETUP_RETRY
    mock_server.async_stop.assert_awaited_once()
    assert not mock_clients.clients


@pytest.mark.usefixtures("mock_clients")
async def test_spawn_api_version(
    hass: HomeAssistant,
    mock_spawn_entry: MockConfigEntry,
    mock_server: MagicMock,
) -> None:
    """A spawned daemon of another API version fails the setup and is stopped."""
    mock_server.async_ensure_running.side_effect = ApiVersionError("0.2.0", "0.1.0")
    mock_spawn_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_spawn_entry.entry_id)
    assert mock_spawn_entry.state is ConfigEntryState.SETUP_ERROR
    mock_server.async_stop.assert_awaited_once()


async def test_remove_deletes_camera_streams(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    camera: MockCamera,
    mock_clients: MockClients,
) -> None:
    """Removing the entry deletes the cameras' streams from a running daemon."""
    await camera.async_handle_async_webrtc_offer("v=0", "session", Mock())
    assert await hass.config_entries.async_remove(init_integration.entry_id)
    assert DATA_HANDOVER not in hass.data
    client = mock_clients.last
    assert mock_clients.paths[-1] == SOCKET_PATH
    client.stream.delete.assert_awaited_once_with(stream_id="camera.front_door")
    client.close.assert_awaited_once()


async def test_remove_cannot_connect(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_clients: MockClients,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A daemon that cannot be reached on removal is only logged."""
    mock_clients.connect_errors = [CannotConnectError("refused")]
    assert await hass.config_entries.async_remove(init_integration.entry_id)
    assert "Could not delete the cameras' streams from rocko: refused" in caplog.text


async def test_remove_spawned(
    hass: HomeAssistant,
    mock_spawn_entry: MockConfigEntry,
    mock_server: MagicMock,
    mock_clients: MockClients,
) -> None:
    """Removing an entry that ran the daemon connects to nothing."""
    mock_spawn_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_spawn_entry.entry_id)
    assert await hass.config_entries.async_remove(mock_spawn_entry.entry_id)
    assert len(mock_clients.clients) == 1


async def test_remote_setup_and_unload(
    hass: HomeAssistant, mock_remote_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """A remote entry connects with its paired identity and never runs a daemon."""
    mock_remote_entry.add_to_hass(hass)
    with patch("custom_components.rocko.RockoServer") as server_class:
        assert await hass.config_entries.async_setup(mock_remote_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_remote_entry.state is ConfigEntryState.LOADED
    server_class.assert_not_called()
    assert mock_clients.paths == []
    assert mock_clients.remote_calls == [
        call(
            REMOTE_HOST,
            REMOTE_PORT,
            identity=IDENTITY,
            server_certificate=SERVER_CERTIFICATE,
            session=async_get_clientsession(hass),
        )
    ]
    client = mock_clients.last
    client.connect.assert_awaited_once()
    client.info.assert_awaited_once()
    connection = mock_remote_entry.runtime_data.connection
    assert connection.client is client
    assert connection.server is None

    assert await hass.config_entries.async_unload(mock_remote_entry.entry_id)
    assert mock_remote_entry.state is ConfigEntryState.NOT_LOADED
    client.close.assert_awaited_once()
    assert connection.client is None


async def test_remote_unload_hands_over_sessions(
    hass: HomeAssistant, mock_remote_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """A remote daemon keeps running, so a reload adopts its sessions as for a socket."""
    mock_remote_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_remote_entry.entry_id)
    await hass.async_block_till_done()
    camera = MockCamera("camera_unique_id")
    await async_setup_camera(hass, camera)
    await camera.async_handle_async_webrtc_offer("v=0", "session", Mock())
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(mock_remote_entry.entry_id)
    await hass.async_block_till_done()
    assert len(mock_clients.remote_calls) == 2
    mock_clients.last.session.adopt.assert_awaited_once()


async def test_remote_setup_cannot_connect(
    hass: HomeAssistant, mock_remote_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """A remote daemon that cannot be reached, or revoked the client, makes setup retry."""
    mock_clients.connect_errors = [CannotConnectError("certificate unknown")]
    mock_remote_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_remote_entry.entry_id)
    assert mock_remote_entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_remote_entry.reason == "Could not connect to rocko: certificate unknown"


async def test_remove_remote_deletes_camera_streams(
    hass: HomeAssistant, mock_remote_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """Removing a remote entry deletes the cameras' streams from the daemon."""
    mock_remote_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_remote_entry.entry_id)
    await async_setup_camera(hass, MockCamera("camera_unique_id"))
    assert await hass.config_entries.async_remove(mock_remote_entry.entry_id)
    assert len(mock_clients.remote_calls) == 2
    assert mock_clients.remote_calls[-1] == mock_clients.remote_calls[0]
    client = mock_clients.last
    client.stream.delete.assert_awaited_once_with(stream_id="camera.front_door")
    client.close.assert_awaited_once()
