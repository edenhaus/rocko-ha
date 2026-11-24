"""Tests for the rocko config flow."""

import asyncio
from collections.abc import Generator
from pathlib import Path
import stat
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from rocko_client import (
    ApiVersionError,
    CannotConnectError,
    ConnectionClosedError,
    PairingCode,
    PairingError,
    PairingExpiredError,
    PairingRejectedError,
    ServerCertificateError,
    ServerPinMismatchError,
    generate_identity,
)

from custom_components.rocko.const import (
    CLIENT_NAME,
    CONF_BINARY,
    CONF_CERTIFICATE,
    CONF_MODE,
    CONF_PAIRING_CODE,
    CONF_PRIVATE_KEY,
    CONF_SERVER_CERTIFICATE,
    CONF_SOCKET,
    DEFAULT_PORT,
    DEFAULT_SOCKET,
    DOMAIN,
    MODE_CONNECT,
    MODE_REMOTE,
    MODE_SPAWN,
)
from custom_components.rocko.server import RockoServerError
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import (
    IDENTITY,
    PAIRING_CODE,
    REMOTE_DATA,
    REMOTE_HOST,
    REMOTE_PORT,
    SERVER_CERTIFICATE,
    SOCKET_PATH,
    MockClients,
)

REMOTE_INPUT = {
    CONF_HOST: REMOTE_HOST,
    CONF_PORT: REMOTE_PORT,
    CONF_PAIRING_CODE: PAIRING_CODE,
}

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


@pytest.fixture
def binary(tmp_path: Path) -> str:
    """An executable file."""
    path = tmp_path / "rocko"
    path.write_text("#!/bin/sh\n")
    path.chmod(stat.S_IRWXU)
    return str(path)


@pytest.fixture
def mock_version() -> Generator[AsyncMock]:
    """Patch running `rocko --version`."""
    with patch(
        "custom_components.rocko.config_flow.async_get_version", return_value="0.1.0"
    ) as get_version:
        yield get_version


@pytest.fixture
def mock_which(binary: str) -> Generator[None]:
    """Patch finding rocko on the PATH."""
    with patch("custom_components.rocko.config_flow.shutil.which", return_value=binary):
        yield


@pytest.fixture
def mock_pair() -> Generator[AsyncMock]:
    """Patch pairing with a remote daemon, which answers with its certificate."""
    with patch(
        "custom_components.rocko.config_flow.pair", return_value=SERVER_CERTIFICATE
    ) as pair:
        yield pair


@pytest.fixture
def mock_generate_identity() -> Generator[MagicMock]:
    """Patch making a client identity, which makes `IDENTITY`."""
    with patch(
        "custom_components.rocko.config_flow.generate_identity", return_value=IDENTITY
    ) as generate:
        yield generate


def _suggested(result: dict, key: str) -> str | None:
    """The suggested value of a form field."""
    for marker in result["data_schema"].schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value")
    raise KeyError(key)


def _default(result: dict, key: str) -> Any:
    """The default value of a form field."""
    for marker in result["data_schema"].schema:
        if marker == key:
            return marker.default()
    raise KeyError(key)


async def _start_remote(hass: HomeAssistant) -> FlowResult:
    """Start a flow and choose a rocko on another host."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": MODE_REMOTE}
    )


@pytest.mark.usefixtures("mock_which")
async def test_spawn(hass: HomeAssistant, binary: str, mock_version: AsyncMock) -> None:
    """Choosing to run rocko checks the binary and creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [MODE_SPAWN, MODE_CONNECT, MODE_REMOTE]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": MODE_SPAWN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == MODE_SPAWN
    assert _suggested(result, CONF_BINARY) == binary

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BINARY: binary}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "rocko"
    assert result["data"] == {CONF_MODE: MODE_SPAWN, CONF_BINARY: binary}
    mock_version.assert_awaited_once_with(binary)


async def test_spawn_not_on_path(hass: HomeAssistant) -> None:
    """Without rocko on the PATH nothing is suggested."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch("custom_components.rocko.config_flow.shutil.which", return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": MODE_SPAWN}
        )
    assert result["type"] is FlowResultType.FORM
    assert _suggested(result, CONF_BINARY) is None


@pytest.mark.usefixtures("mock_which")
async def test_spawn_errors(
    hass: HomeAssistant, tmp_path: Path, binary: str, mock_version: AsyncMock
) -> None:
    """Each binary error is shown, and the flow recovers from it."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": MODE_SPAWN}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BINARY: str(tmp_path / "missing")}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BINARY: "binary_not_found"}
    assert _suggested(result, CONF_BINARY) == str(tmp_path / "missing")

    not_executable = tmp_path / "not-executable"
    not_executable.write_text("")
    not_executable.chmod(stat.S_IRUSR)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BINARY: str(not_executable)}
    )
    assert result["errors"] == {CONF_BINARY: "binary_not_executable"}

    mock_version.side_effect = RockoServerError("not rocko")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BINARY: binary}
    )
    assert result["errors"] == {CONF_BINARY: "invalid_binary"}

    mock_version.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BINARY: binary}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_MODE: MODE_SPAWN, CONF_BINARY: binary}


async def test_connect(hass: HomeAssistant, mock_clients: MockClients) -> None:
    """Choosing a running rocko checks its socket and creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": MODE_CONNECT}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == MODE_CONNECT
    assert _suggested(result, CONF_SOCKET) == DEFAULT_SOCKET

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOCKET: SOCKET_PATH}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "rocko"
    assert result["data"] == {CONF_MODE: MODE_CONNECT, CONF_SOCKET: SOCKET_PATH}
    assert mock_clients.paths == [SOCKET_PATH]
    mock_clients.last.close.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (CannotConnectError("refused"), "cannot_connect"),
        (ApiVersionError("0.2.0", "0.1.0"), "api_version"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_connect_errors(
    hass: HomeAssistant,
    mock_clients: MockClients,
    error: BaseException,
    reason: str,
) -> None:
    """A socket that does not answer as rocko shows an error, and the flow recovers."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": MODE_CONNECT}
    )
    mock_clients.connect_errors = [error]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOCKET: SOCKET_PATH}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}
    assert _suggested(result, CONF_SOCKET) == SOCKET_PATH

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOCKET: SOCKET_PATH}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_single_instance(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Only one entry can exist."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure_connect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """Reconfiguring keeps the mode and suggests the current socket."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": MODE_CONNECT}
    )
    assert _suggested(result, CONF_SOCKET) == SOCKET_PATH

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOCKET: "/run/other/rocko.sock"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {
        CONF_MODE: MODE_CONNECT,
        CONF_SOCKET: "/run/other/rocko.sock",
    }


@pytest.mark.usefixtures("mock_version")
async def test_reconfigure_to_spawn(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    binary: str,
) -> None:
    """Reconfiguring can switch from connecting to running rocko."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    with patch("custom_components.rocko.config_flow.shutil.which", return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": MODE_SPAWN}
        )
    assert _suggested(result, CONF_BINARY) is None

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BINARY: binary}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {CONF_MODE: MODE_SPAWN, CONF_BINARY: binary}


async def test_reconfigure_spawn(
    hass: HomeAssistant, mock_spawn_entry: MockConfigEntry
) -> None:
    """Reconfiguring a spawn entry suggests its binary."""
    mock_spawn_entry.add_to_hass(hass)
    result = await mock_spawn_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": MODE_SPAWN}
    )
    assert _suggested(result, CONF_BINARY) == mock_spawn_entry.data[CONF_BINARY]


async def test_remote(
    hass: HomeAssistant,
    mock_clients: MockClients,
    mock_pair: AsyncMock,
    mock_generate_identity: MagicMock,
) -> None:
    """A rocko on another host is paired with a new identity, checked, and kept."""
    result = await _start_remote(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == MODE_REMOTE
    assert result["errors"] == {}
    assert _default(result, CONF_PORT) == DEFAULT_PORT
    assert _suggested(result, CONF_HOST) is None
    assert _suggested(result, CONF_PAIRING_CODE) is None

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], REMOTE_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "rocko"
    assert result["data"] == REMOTE_DATA
    assert PAIRING_CODE.rsplit("-", 1)[1] not in str(result["data"])

    session = async_get_clientsession(hass)
    mock_generate_identity.assert_called_once_with(CLIENT_NAME)
    mock_pair.assert_awaited_once_with(
        REMOTE_HOST,
        REMOTE_PORT,
        PairingCode.parse(PAIRING_CODE),
        IDENTITY,
        session=session,
    )
    assert mock_clients.paths == []
    assert mock_clients.remote_calls == [
        call(
            REMOTE_HOST,
            REMOTE_PORT,
            identity=IDENTITY,
            server_certificate=SERVER_CERTIFICATE,
            session=session,
        )
    ]
    mock_clients.last.connect.assert_awaited_once()
    mock_clients.last.close.assert_awaited_once()


@pytest.mark.usefixtures("mock_clients", "mock_generate_identity")
async def test_remote_invalid_code(hass: HomeAssistant, mock_pair: AsyncMock) -> None:
    """A code that is not one is shown on its field before anything is sent."""
    result = await _start_remote(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**REMOTE_INPUT, CONF_PAIRING_CODE: "ROCKO1-nonsense"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PAIRING_CODE: "invalid_pairing_code"}
    mock_pair.assert_not_awaited()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**REMOTE_INPUT, CONF_PAIRING_CODE: f" {PAIRING_CODE}\n"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == REMOTE_DATA


@pytest.mark.parametrize(
    ("error", "errors"),
    [
        (
            ServerPinMismatchError("another certificate"),
            {"base": "server_pin_mismatch"},
        ),
        (ServerCertificateError("no name rocko"), {"base": "server_certificate"}),
        (PairingRejectedError("wrong secret"), {CONF_PAIRING_CODE: "pairing_rejected"}),
        (PairingExpiredError("no code open"), {CONF_PAIRING_CODE: "pairing_expired"}),
        (PairingError("HTTP 500"), {"base": "pairing_failed"}),
        (CannotConnectError("refused"), {"base": "cannot_connect"}),
        (RuntimeError("boom"), {"base": "unknown"}),
    ],
)
@pytest.mark.usefixtures("mock_generate_identity")
async def test_remote_pairing_errors(
    hass: HomeAssistant,
    mock_clients: MockClients,
    mock_pair: AsyncMock,
    error: BaseException,
    errors: dict[str, str],
) -> None:
    """Each way pairing fails shows its error, connects nowhere, and the flow recovers."""
    result = await _start_remote(hass)
    mock_pair.side_effect = error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], REMOTE_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors
    assert _suggested(result, CONF_HOST) == REMOTE_HOST
    assert not mock_clients.clients

    mock_pair.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], REMOTE_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == REMOTE_DATA


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ApiVersionError("0.2.0", "0.1.0"), "api_version"),
        (CannotConnectError("refused"), "cannot_connect"),
        (ConnectionClosedError("closed"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
@pytest.mark.usefixtures("mock_pair", "mock_generate_identity")
async def test_remote_connect_errors(
    hass: HomeAssistant,
    mock_clients: MockClients,
    error: BaseException,
    reason: str,
) -> None:
    """A paired daemon that does not answer as rocko shows an error and is closed."""
    result = await _start_remote(hass)
    mock_clients.connect_errors = [error]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], REMOTE_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}
    mock_clients.last.close.assert_awaited_once()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], REMOTE_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def _hang(*args: Any, **kwargs: Any) -> None:
    """Never answer."""
    await asyncio.Event().wait()


@pytest.mark.parametrize("hanging", ["pair", "connect"])
@pytest.mark.usefixtures("mock_generate_identity")
async def test_remote_timeout(
    hass: HomeAssistant,
    mock_clients: MockClients,
    mock_pair: AsyncMock,
    hanging: str,
) -> None:
    """A daemon that does not answer while pairing or after it times out."""
    result = await _start_remote(hass)
    if hanging == "pair":
        mock_pair.side_effect = _hang
    else:
        mock_clients.configure = [
            lambda client: setattr(client.connect, "side_effect", _hang)
        ]
    with patch("custom_components.rocko.config_flow.PAIRING_TIMEOUT", 0.01):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], REMOTE_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "timeout_connect"}
    if hanging == "connect":
        mock_clients.last.close.assert_awaited_once()


@pytest.mark.parametrize("entry_fixture", ["mock_config_entry", "mock_spawn_entry"])
@pytest.mark.usefixtures("mock_clients", "mock_pair", "mock_generate_identity")
async def test_reconfigure_to_remote(
    hass: HomeAssistant,
    request: pytest.FixtureRequest,
    mock_setup_entry: AsyncMock,
    entry_fixture: str,
) -> None:
    """Reconfiguring can switch from a socket or a binary to a rocko on another host."""
    entry: MockConfigEntry = request.getfixturevalue(entry_fixture)
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": MODE_REMOTE}
    )
    assert result["type"] is FlowResultType.FORM
    assert _suggested(result, CONF_HOST) is None
    assert _default(result, CONF_PORT) == DEFAULT_PORT

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], REMOTE_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == REMOTE_DATA
    mock_setup_entry.assert_awaited_once()


@pytest.mark.usefixtures("mock_clients")
async def test_reconfigure_remote(
    hass: HomeAssistant,
    mock_remote_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
    mock_pair: AsyncMock,
    mock_generate_identity: MagicMock,
) -> None:
    """Reconfiguring a remote entry suggests where it is and pairs a new identity."""
    mock_remote_entry.add_to_hass(hass)
    result = await mock_remote_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": MODE_REMOTE}
    )
    assert _suggested(result, CONF_HOST) == REMOTE_HOST
    assert _suggested(result, CONF_PORT) == REMOTE_PORT
    assert _suggested(result, CONF_PAIRING_CODE) is None

    identity = generate_identity()
    mock_generate_identity.return_value = identity
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "rocko.example", CONF_PORT: 28557, CONF_PAIRING_CODE: PAIRING_CODE},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_remote_entry.data == {
        **REMOTE_DATA,
        CONF_HOST: "rocko.example",
        CONF_PORT: 28557,
        CONF_CERTIFICATE: identity.certificate,
        CONF_PRIVATE_KEY: identity.private_key,
        CONF_SERVER_CERTIFICATE: SERVER_CERTIFICATE,
    }
    assert mock_pair.await_args is not None
    assert mock_pair.await_args.args[:2] == ("rocko.example", 28557)
    mock_setup_entry.assert_awaited_once()


async def test_reconfigure_remote_to_connect(
    hass: HomeAssistant, mock_remote_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """Reconfiguring a remote entry can switch back to a socket, without its identity."""
    mock_remote_entry.add_to_hass(hass)
    result = await mock_remote_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": MODE_CONNECT}
    )
    assert _suggested(result, CONF_SOCKET) == DEFAULT_SOCKET

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOCKET: SOCKET_PATH}
    )
    assert result["type"] is FlowResultType.ABORT
    assert mock_remote_entry.data == {CONF_MODE: MODE_CONNECT, CONF_SOCKET: SOCKET_PATH}
