"""Config flow for the rocko integration.

rocko is either run by Home Assistant, from a binary the user names, already
running and reached through its control socket, or running on another host
and reached through its mutual-TLS control listener. Each is checked before
an entry is made: the binary must be rocko (`rocko --version`), the socket
must answer with a `hello` this integration understands, and a remote daemon
must pair with a new client identity, through the one-time code of `rocko
pair`, and then answer with such a `hello` over TLS.
"""

import asyncio
import logging
import os
from pathlib import Path
import shutil
from typing import Any, override

import probatio
from rocko_client import (
    ApiVersionError,
    InvalidPairingCodeError,
    PairingCode,
    PairingError,
    PairingExpiredError,
    PairingRejectedError,
    RockoClient,
    RockoError,
    ServerCertificateError,
    ServerPinMismatchError,
    certificate_pin,
    generate_identity,
    pair,
)

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .connection import RemoteDaemon
from .const import (
    BINARY_NAME,
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
    PAIRING_TIMEOUT,
)
from .server import RockoServerError, async_get_version

_LOGGER = logging.getLogger(__name__)

TITLE = "rocko"
MENU_OPTIONS = [MODE_SPAWN, MODE_CONNECT, MODE_REMOTE]

REMOTE_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): str,
        probatio.Required(CONF_PORT, default=DEFAULT_PORT): probatio.Port(),
        probatio.Required(CONF_PAIRING_CODE): str,
    }
)


def _check_binary(path: str) -> str | None:
    """The error key for a path that is not an executable file."""
    if not Path(path).is_file():
        return "binary_not_found"
    if not os.access(path, os.X_OK):
        return "binary_not_executable"
    return None


async def _async_check_socket(path: str) -> str | None:
    """Connect to a control socket; the error key if that fails."""
    client = RockoClient(path)
    try:
        hello = await client.connect()
    except ApiVersionError as err:
        _LOGGER.debug("rocko at %s speaks an unsupported API: %s", path, err)
        return "api_version"
    except RockoError as err:
        _LOGGER.debug("Cannot connect to rocko at %s: %s", path, err)
        return "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected error connecting to rocko at %s", path)
        return "unknown"
    _LOGGER.debug("rocko %s answers at %s", hello.version, path)
    await client.close()
    return None


async def _async_pair(
    hass: HomeAssistant, host: str, port: int, code: str
) -> dict[str, Any]:
    """Pair a new identity with the daemon at `host`:`port`, then connect once.

    Returns the entry's data: the identity, the daemon's certificate and
    where it listens; the pairing code is used up and not kept. Raises what
    `PairingCode.parse`, `pair` and `RockoClient.connect` raise, and
    `TimeoutError` when the daemon takes longer than `PAIRING_TIMEOUT`.
    """
    pairing_code = PairingCode.parse(code)
    identity = await hass.async_add_executor_job(generate_identity, CLIENT_NAME)
    session = async_get_clientsession(hass)
    async with asyncio.timeout(PAIRING_TIMEOUT):
        server_certificate = await pair(
            host, port, pairing_code, identity, session=session
        )
    _LOGGER.debug(
        "Paired client %s with rocko %s at %s:%s",
        identity.pin,
        certificate_pin(server_certificate),
        host,
        port,
    )
    daemon = RemoteDaemon(host, port, identity, server_certificate, session)
    client = daemon.client()
    try:
        async with asyncio.timeout(PAIRING_TIMEOUT):
            hello = await client.connect()
    finally:
        await client.close()
    _LOGGER.debug("rocko %s answers at %s:%s", hello.version, host, port)
    return {
        CONF_MODE: MODE_REMOTE,
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_CERTIFICATE: identity.certificate,
        CONF_PRIVATE_KEY: identity.private_key,
        CONF_SERVER_CERTIFICATE: server_certificate,
    }


class RockoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up rocko: run it, connect to a running one, or pair with one elsewhere."""

    VERSION = 1

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose how to reach rocko."""
        return self.async_show_menu(step_id="user", menu_options=MENU_OPTIONS)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose again how to reach rocko."""
        return self.async_show_menu(step_id="reconfigure", menu_options=MENU_OPTIONS)

    async def async_step_spawn(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run rocko from a binary."""
        errors: dict[str, str] = {}
        if user_input is not None:
            binary = user_input[CONF_BINARY]
            if error := await self.hass.async_add_executor_job(_check_binary, binary):
                errors[CONF_BINARY] = error
            else:
                try:
                    version = await async_get_version(binary)
                except RockoServerError as err:
                    _LOGGER.debug("%s is not usable: %s", binary, err)
                    errors[CONF_BINARY] = "invalid_binary"
                else:
                    _LOGGER.debug("%s is rocko %s", binary, version)
                    return self._async_finish({CONF_MODE: MODE_SPAWN, **user_input})
        elif (user_input := self._suggested(MODE_SPAWN)) is None and (
            found := await self.hass.async_add_executor_job(shutil.which, BINARY_NAME)
        ):
            user_input = {CONF_BINARY: found}
        return self.async_show_form(
            step_id=MODE_SPAWN,
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema({probatio.Required(CONF_BINARY): str}), user_input
            ),
            errors=errors,
        )

    async def async_step_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connect to a running rocko through its control socket."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await _async_check_socket(user_input[CONF_SOCKET]):
                errors["base"] = error
            else:
                return self._async_finish({CONF_MODE: MODE_CONNECT, **user_input})
        else:
            user_input = self._suggested(MODE_CONNECT) or {CONF_SOCKET: DEFAULT_SOCKET}
        return self.async_show_form(
            step_id=MODE_CONNECT,
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema({probatio.Required(CONF_SOCKET): str}), user_input
            ),
            errors=errors,
        )

    async def async_step_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connect to rocko on another host, paired through a one-time code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST], user_input[CONF_PORT]
            try:
                data = await _async_pair(
                    self.hass, host, port, user_input[CONF_PAIRING_CODE]
                )
            except InvalidPairingCodeError as err:
                _LOGGER.debug("Not a pairing code: %s", err)
                errors[CONF_PAIRING_CODE] = "invalid_pairing_code"
            except ServerPinMismatchError as err:
                _LOGGER.debug("Cannot pair with rocko at %s:%s: %s", host, port, err)
                errors["base"] = "server_pin_mismatch"
            except ServerCertificateError as err:
                _LOGGER.debug("Cannot pair with rocko at %s:%s: %s", host, port, err)
                errors["base"] = "server_certificate"
            except PairingRejectedError as err:
                _LOGGER.debug("Cannot pair with rocko at %s:%s: %s", host, port, err)
                errors[CONF_PAIRING_CODE] = "pairing_rejected"
            except PairingExpiredError as err:
                _LOGGER.debug("Cannot pair with rocko at %s:%s: %s", host, port, err)
                errors[CONF_PAIRING_CODE] = "pairing_expired"
            except PairingError as err:
                _LOGGER.debug("Cannot pair with rocko at %s:%s: %s", host, port, err)
                errors["base"] = "pairing_failed"
            except ApiVersionError as err:
                _LOGGER.debug(
                    "rocko at %s:%s speaks an unsupported API: %s", host, port, err
                )
                errors["base"] = "api_version"
            except TimeoutError:
                _LOGGER.debug(
                    "rocko at %s:%s did not answer within %s s",
                    host,
                    port,
                    PAIRING_TIMEOUT,
                )
                errors["base"] = "timeout_connect"
            except RockoError as err:
                _LOGGER.debug("Cannot connect to rocko at %s:%s: %s", host, port, err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception(
                    "Unexpected error pairing with rocko at %s:%s", host, port
                )
                errors["base"] = "unknown"
            else:
                return self._async_finish(data)
        elif (suggested := self._suggested(MODE_REMOTE)) is not None:
            # The identity is not offered again: a new code pairs a new one.
            user_input = {
                CONF_HOST: suggested[CONF_HOST],
                CONF_PORT: suggested[CONF_PORT],
            }
        return self.async_show_form(
            step_id=MODE_REMOTE,
            data_schema=self.add_suggested_values_to_schema(REMOTE_SCHEMA, user_input),
            errors=errors,
        )

    def _suggested(self, mode: str) -> dict[str, Any] | None:
        """The values of the entry being reconfigured, if it uses `mode`."""
        if self.source != SOURCE_RECONFIGURE:
            return None
        data = self._get_reconfigure_entry().data
        return dict(data) if data[CONF_MODE] == mode else None

    def _async_finish(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create the entry, or update the one being reconfigured."""
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data=data
            )
        return self.async_create_entry(title=TITLE, data=data)
