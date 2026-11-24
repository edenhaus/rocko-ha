"""Make rocko the WebRTC provider that cameras try first.

Home Assistant keeps the registered WebRTC providers in a set,
`hass.data[DATA_WEBRTC_PROVIDERS]`, and gives a camera the first of them whose
`async_is_supported` accepts its stream source; registering takes no priority.
A set has no order, so when go2rtc is registered too, which of the two serves an
RTSP camera follows the providers' hashes, not the order they registered in
(observed in Home Assistant 2026.10.4, 2026.11.0 and dev on 2026-11-24).

Before rocko registers, the set is replaced by one that yields rocko's provider
first and every other provider after it. This relies on core internals, the data
key and its set type, and is to go once core can order providers.
"""

from collections.abc import Callable, Iterator
import logging
from typing import override

from homeassistant.components.camera import (
    CameraWebRTCProvider,
    async_register_webrtc_provider,
)
from homeassistant.components.camera.webrtc import DATA_WEBRTC_PROVIDERS
from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)


class FirstProviderSet(set[CameraWebRTCProvider]):
    """The registered providers, iterated with `first` before the others.

    The providers registered before it replaced core's set stay in that set,
    `earlier`, which it reads but never copies: core's removal callbacks of those
    providers remove them from there, so none is kept here once it is gone. The
    providers registered since are its own members. The two never overlap, as core
    only adds a provider that is in neither. `first` is cleared when it is removed,
    so an unloaded entry's provider is not kept either.
    """

    def __init__(
        self, first: CameraWebRTCProvider, earlier: set[CameraWebRTCProvider]
    ) -> None:
        """Read the providers of `earlier`, with `first` to be yielded first."""
        super().__init__()
        self.first: CameraWebRTCProvider | None = first
        self._earlier = earlier

    @override
    def __contains__(self, provider: object) -> bool:
        """Whether `provider` is registered, here or in the earlier set."""
        return super().__contains__(provider) or provider in self._earlier

    @override
    def __len__(self) -> int:
        """The number of registered providers, which core tests for being none."""
        return super().__len__() + len(self._earlier)

    @override
    def __iter__(self) -> Iterator[CameraWebRTCProvider]:
        """Yield `first`, if registered, then the other registered providers."""
        first = self.first
        if first is not None and first in self:
            yield first
        for provider in (*self._earlier, *super().__iter__()):
            if provider is not first:
                yield provider

    @override
    def remove(self, provider: CameraWebRTCProvider) -> None:
        """Remove a provider registered since, as core's removal callback does."""
        super().remove(provider)
        if provider is self.first:
            self.first = None


@callback
def async_register_first_webrtc_provider(
    hass: HomeAssistant, provider: CameraWebRTCProvider
) -> Callable[[], None]:
    """Register `provider` as the WebRTC provider cameras try first.

    Returns core's callback that removes it again.
    """
    providers = hass.data.get(DATA_WEBRTC_PROVIDERS)
    if isinstance(providers, FirstProviderSet):
        # An earlier entry of rocko replaced the set already.
        providers.first = provider
    else:
        earlier = providers if providers is not None else set()
        _LOGGER.debug(
            "Ordering the WebRTC providers with rocko first, %d registered before",
            len(earlier),
        )
        hass.data[DATA_WEBRTC_PROVIDERS] = FirstProviderSet(provider, earlier)
    return async_register_webrtc_provider(hass, provider)
