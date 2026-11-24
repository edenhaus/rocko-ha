"""Fixtures for the rocko integration tests."""

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
    setup_test_component_platform,
)
from rocko_client import ClientIdentity, RockoClient, Subscription
from rocko_client._commands import (
    MetricsCommands,
    SessionCommands,
    StreamCommands,
    WebrtcCommands,
)
from rocko_client.models import (
    Hello,
    InfoResult,
    Metrics,
    SessionEvent,
    StreamList,
    StreamPutResult,
)

from custom_components.rocko.const import (
    CONF_BINARY,
    CONF_CERTIFICATE,
    CONF_MODE,
    CONF_PRIVATE_KEY,
    CONF_SERVER_CERTIFICATE,
    CONF_SOCKET,
    DOMAIN,
    MODE_CONNECT,
    MODE_REMOTE,
    MODE_SPAWN,
)
from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from . import MockCamera

SOCKET_PATH = "/run/rocko/rocko.sock"
SPAWN_SOCKET_PATH = "/run/rocko-spawn/rocko.sock"
BINARY_PATH = "/usr/bin/rocko"

REMOTE_HOST = "192.0.2.20"
REMOTE_PORT = 18557
PAIRING_CODE = f"ROCKO1-{'e1' * 32}-{'5e' * 16}"
"""A well-formed pairing code; its secret must never be stored."""
CLIENT_CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIIBMzCB2qADAgECAhRP2DJPbCyY05TKaGheMcJxUU4y+zAKBggqhkjOPQQDAjAZ
MRcwFQYDVQQDDA5Ib21lIEFzc2lzdGFudDAgFw0wMDAxMDEwMDAwMDBaGA85OTk5
MTIzMTIzNTk1OVowGTEXMBUGA1UEAwwOSG9tZSBBc3Npc3RhbnQwWTATBgcqhkjO
PQIBBggqhkjOPQMBBwNCAASavxY/dym2PVxRNY7zHfrLonDAAOryMojR/H2FXTda
o55MYnS8Cs4U9PJSbhrKAzu7NCVSC+gzWgmppZbz8/rfMAoGCCqGSM49BAMCA0gA
MEUCIQCm6AwZYw71csl/ObOmSN8V+qj9aqOnoNiYugbBav/61wIgLfa6HFuptWJu
hV5qtZsAtItxsNuYNYzzco/QkM9GWU4=
-----END CERTIFICATE-----
"""
"""A client certificate as `generate_identity` makes it; its pin is `CLIENT_PIN`."""
CLIENT_PIN = "aa0c51900d45c1ad1a18f725d7b9d89f08be4bc96bd37d5819af4370f86af110"
CLIENT_PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----\n"
)
"""Stands in for the key: the client that would load it is mocked."""
IDENTITY = ClientIdentity(
    certificate=CLIENT_CERTIFICATE, private_key=CLIENT_PRIVATE_KEY
)
SERVER_CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIIBIjCByKADAgECAhRCs60Ji4ZmxENgEm08nDF3sV8S9jAKBggqhkjOPQQDAjAQ
MQ4wDAYDVQQDDAVyb2NrbzAgFw0wMDAxMDEwMDAwMDBaGA85OTk5MTIzMTIzNTk1
OVowEDEOMAwGA1UEAwwFcm9ja28wWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAATV
p53Y3hClpOxaxqkE/DxYHqy3kDL7iKQesvD/59MYKlBirP0QhL0QqDfPQ33WIN/M
EZf6UadqxwA++Fha++8RMAoGCCqGSM49BAMCA0kAMEYCIQD7apiYhAS2WSwA1Vmg
X9J+gHolFAVk+dgWEHi7u295awIhAMdyzVrdxA27Vibg6RyWvPwjkS+TrhobfGu5
SYm88pab
-----END CERTIFICATE-----
"""
"""The remote daemon's certificate; its pin is `SERVER_PIN`."""
SERVER_PIN = "e131c2710026f2da21ca7feb8a2fe9b0548af3bd5cf456f53ca49e34b226192a"
REMOTE_DATA = {
    CONF_MODE: MODE_REMOTE,
    CONF_HOST: REMOTE_HOST,
    CONF_PORT: REMOTE_PORT,
    CONF_CERTIFICATE: CLIENT_CERTIFICATE,
    CONF_PRIVATE_KEY: CLIENT_PRIVATE_KEY,
    CONF_SERVER_CERTIFICATE: SERVER_CERTIFICATE,
}
"""A remote entry's data, as pairing `IDENTITY` with the daemon makes it."""

HELLO = Hello.from_dict(
    {
        "type": "hello",
        "api": "0.1.0",
        "version": "0.1.0",
        "outputs": ["webrtc"],
        "features": ["session_adopt", "turn"],
    }
)
INFO = InfoResult.from_dict(
    {
        "version": "0.1.0",
        "build": {"target": "x86_64-unknown-linux-musl", "rustc": "1.98.0"},
        # A build without the HTTP source: rocko's default build has it too.
        "protocols": ["rtsp", "rtsps", "webrtc"],
        "outputs": ["webrtc"],
        "features": ["session_adopt", "turn"],
        "codecs": {"video": ["h264", "h265"], "audio": ["opus", "pcmu"]},
        "limits": {
            "max_streams": 256,
            "max_sessions": 256,
            "max_sessions_per_stream": 16,
            "max_connections": 8,
            "session_grace_ms": 10000,
        },
        "sandbox": {
            "mode": "on",
            "uid": 65534,
            "gid": 65534,
            "no_new_privs": True,
            "seccomp": "enforced",
            "landlock": {"fs": "enforced", "net": "unavailable", "abi": 3},
        },
    }
)
METRICS = Metrics.from_dict(
    {
        "supervisor": {"pid": 4242, "uptime_ms": 60000},
        "workers": {},
        "worker_restarts": 0,
        "sessions": 1,
        "streams": {"camera.front_door": {"frames_dropped": 0}},
    }
)
STREAMS = StreamList.from_dict(
    {
        "streams": {
            "camera.front_door": {
                "stream_id": "camera.front_door",
                "state": "live",
                "preload": False,
                "since": "2026-11-06T10:00:00Z",
                "sessions": ["session-1"],
                "sources": [
                    {
                        "source": {
                            "protocol": "rtsp",
                            "url": "rtsp://****@192.0.2.10:554/h264",
                        },
                        "state": "live",
                        "reconnects": 0,
                        "connection": {"id": "c1"},
                    }
                ],
                "tracks": [],
                "stats": {},
            },
            "garage": {
                "stream_id": "garage",
                "state": "idle",
                "preload": True,
                "since": "2026-11-06T10:00:00Z",
                "sessions": [],
                "sources": [],
                "tracks": [],
                "stats": {},
            },
        }
    }
)


class MockClients:
    """The patched `RockoClient` class: a new mock client per connection."""

    def __init__(self) -> None:
        """Start with no client."""
        self.clients: list[MagicMock] = []
        """Every client created, of a socket or a remote daemon, in order."""
        self.paths: list[str] = []
        """The socket of each client created with `RockoClient(...)`."""
        self.remote_calls: list[Any] = []
        """The `call` of each client created with `RockoClient.remote(...)`."""
        self.connect_errors: list[BaseException] = []
        """Raised by the next clients' `connect`, one each, in order."""
        self.configure: list[Callable[[MagicMock], object]] = []
        """Applied to the next clients, one each, in order."""

    def __call__(self, socket_path: str, **kwargs: Any) -> MagicMock:
        """Create the next client, of a control socket."""
        self.paths.append(socket_path)
        return self._next()

    def remote(self, host: str, port: int, **kwargs: Any) -> MagicMock:
        """Create the next client, of a remote daemon."""
        self.remote_calls.append(call(host, port, **kwargs))
        return self._next()

    def _next(self) -> MagicMock:
        """A new client, with the next queued error and configuration."""
        client = _mock_client()
        if self.connect_errors:
            client.connect.side_effect = self.connect_errors.pop(0)
        if self.configure:
            self.configure.pop(0)(client)
        self.clients.append(client)
        return client

    @property
    def last(self) -> MagicMock:
        """The newest client."""
        return self.clients[-1]


def _mock_client() -> MagicMock:
    """A connected client whose commands succeed."""
    client = MagicMock(spec=RockoClient)
    client.hello = HELLO
    client.connect = AsyncMock(return_value=HELLO)
    client.close = AsyncMock()
    client.info = AsyncMock(return_value=INFO)
    client.ping = AsyncMock()
    closed: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    client.closed = closed
    """Set its result to drop the connection."""

    async def _wait_closed() -> Any:
        return await asyncio.shield(closed)

    client.wait_closed = AsyncMock(side_effect=_wait_closed)
    client.unsubscribe_id = AsyncMock()
    client.subscriptions = []
    """Every session subscription opened or adopted, in order."""

    def _subscribe(**kwargs: Any) -> Subscription[SessionEvent]:
        subscription: Subscription[SessionEvent] = Subscription(client.unsubscribe_id)
        subscription.id = len(client.subscriptions) + 1
        subscription.result = None
        client.subscriptions.append(subscription)
        return subscription

    client.stream = MagicMock(spec=StreamCommands)
    client.stream.put.return_value = StreamPutResult(created=True)
    client.stream.delete.return_value = None
    client.stream.list.return_value = STREAMS
    client.webrtc = MagicMock(spec=WebrtcCommands)
    client.webrtc.offer.side_effect = _subscribe
    client.webrtc.candidate.return_value = None
    client.session = MagicMock(spec=SessionCommands)
    client.session.adopt.side_effect = _subscribe
    client.metrics = MagicMock(spec=MetricsCommands)
    client.metrics.get.return_value = METRICS
    return client


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@pytest.fixture
def mock_clients() -> Generator[MockClients]:
    """Patch `RockoClient` wherever the integration creates one."""
    clients = MockClients()
    client_class = MagicMock(side_effect=clients)
    client_class.remote.side_effect = clients.remote
    with (
        patch("custom_components.rocko.RockoClient", client_class),
        patch("custom_components.rocko.connection.RockoClient", client_class),
        patch("custom_components.rocko.server.RockoClient", client_class),
        patch("custom_components.rocko.config_flow.RockoClient", client_class),
    ):
        yield clients


@pytest.fixture
async def mock_server() -> AsyncGenerator[MagicMock]:
    """Patch the daemon Home Assistant runs."""
    with patch("custom_components.rocko.RockoServer", autospec=True) as server_class:
        server = server_class.return_value
        loop = asyncio.get_running_loop()
        exited: list[asyncio.Future[int]] = [loop.create_future()]

        def _exit(code: int) -> None:
            """Make the running daemon exit with `code`; a restarted one runs on."""
            future = exited[0]
            exited[0] = loop.create_future()
            future.set_result(code)

        server.exit = _exit

        async def _wait() -> int:
            return await asyncio.shield(exited[0])

        server.async_wait.side_effect = _wait
        server.async_ensure_running.return_value = SPAWN_SOCKET_PATH
        server.pid = 4242
        yield server


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Patch setting up an entry."""
    with patch(
        "custom_components.rocko.async_setup_entry", return_value=True
    ) as setup_entry:
        yield setup_entry


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """An entry that connects to a running daemon."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="rocko",
        data={CONF_MODE: MODE_CONNECT, CONF_SOCKET: SOCKET_PATH},
    )


@pytest.fixture
def mock_spawn_entry() -> MockConfigEntry:
    """An entry that runs the daemon."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="rocko",
        data={CONF_MODE: MODE_SPAWN, CONF_BINARY: BINARY_PATH},
    )


@pytest.fixture
def mock_remote_entry() -> MockConfigEntry:
    """An entry that connects to a daemon on another host."""
    return MockConfigEntry(domain=DOMAIN, title="rocko", data=REMOTE_DATA)


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_clients: MockClients
) -> MockConfigEntry:
    """Set up the integration, connected to a mocked daemon."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


TEST_DOMAIN = "test"


async def async_setup_camera(hass: HomeAssistant, camera: MockCamera) -> None:
    """Set up a camera of another integration."""

    async def async_setup_entry_init(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.CAMERA])
        return True

    async def async_unload_entry_init(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        return await hass.config_entries.async_forward_entry_unload(
            entry, Platform.CAMERA
        )

    mock_integration(
        hass,
        MockModule(
            TEST_DOMAIN,
            async_setup_entry=async_setup_entry_init,
            async_unload_entry=async_unload_entry_init,
        ),
    )
    setup_test_component_platform(hass, CAMERA_DOMAIN, [camera], from_config_entry=True)
    mock_platform(hass, f"{TEST_DOMAIN}.config_flow", Mock())
    entry = MockConfigEntry(domain=TEST_DOMAIN)
    entry.add_to_hass(hass)
    with mock_config_flow(TEST_DOMAIN, ConfigFlow):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.fixture
async def camera(hass: HomeAssistant, init_integration: MockConfigEntry) -> MockCamera:
    """A camera of another integration, set up after rocko."""
    camera = MockCamera("camera_unique_id")
    await async_setup_camera(hass, camera)
    return camera
