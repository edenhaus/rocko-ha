"""Constants for the rocko integration."""

from typing import Final

DOMAIN: Final = "rocko"

CONF_MODE: Final = "mode"
CONF_BINARY: Final = "binary"
CONF_SOCKET: Final = "socket"
CONF_PAIRING_CODE: Final = "pairing_code"
"""The one-time code of `rocko pair`; only entered, never stored."""
CONF_CERTIFICATE: Final = "certificate"
"""Home Assistant's client certificate, PEM, which the remote daemon enrolled."""
CONF_PRIVATE_KEY: Final = "private_key"
"""The private key of `CONF_CERTIFICATE`, PEM; a secret."""
CONF_SERVER_CERTIFICATE: Final = "server_certificate"
"""The remote daemon's certificate, PEM, the only one Home Assistant trusts."""

MODE_SPAWN: Final = "spawn"
"""Home Assistant runs the daemon itself, from the binary in `CONF_BINARY`."""
MODE_CONNECT: Final = "connect"
"""Home Assistant connects to a daemon at the control socket in `CONF_SOCKET`."""
MODE_REMOTE: Final = "remote"
"""Home Assistant connects to a daemon on another host over its mutual-TLS listener.

The entry keeps `CONF_HOST`, `CONF_PORT`, the client identity paired with it
(`CONF_CERTIFICATE`, `CONF_PRIVATE_KEY`) and the daemon's certificate
(`CONF_SERVER_CERTIFICATE`).
"""

BINARY_NAME: Final = "rocko"
DEFAULT_SOCKET: Final = "/run/rocko/rocko.sock"
SOCKET_NAME: Final = "rocko.sock"
DEFAULT_PORT: Final = 18557
"""The port of the daemon's control listener, as rocko's README sets `ROCKO_CONTROL_LISTEN`."""
CLIENT_NAME: Final = "Home Assistant"
"""The common name of Home Assistant's client certificate, which tells it apart on the daemon."""

PAIRING_TIMEOUT: Final = 30
"""Seconds pairing with a remote daemon, or the check after it, may take."""

PING_INTERVAL: Final = 10
"""Seconds between two pings on the control connection."""
PING_TIMEOUT: Final = 5
"""Seconds a pong may take before the daemon counts as unresponsive."""
RECONNECT_MAX_DELAY: Final = 60
"""The longest wait, in seconds, between two reconnect attempts."""

ERROR_UNAVAILABLE: Final = "rocko_unavailable"
"""The code of the WebRTC error sent while the daemon cannot be reached."""
ERROR_NO_STREAM_SOURCE: Final = "no_stream_source"
"""The code of the WebRTC error sent for a camera without a stream source."""
ERROR_UNSUPPORTED_STREAM_SOURCE: Final = "unsupported_stream_source"
"""The code of the WebRTC error sent for a stream source rocko does not read."""
