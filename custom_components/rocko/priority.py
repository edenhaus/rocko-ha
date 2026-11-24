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

    It takes over the providers of the set it replaces. Each of them keeps the
    removal callback core returned when it registered, and that callback removes it
    from the earlier set, so a provider taken over counts as registered only while
    the earlier set still has it.
    """

    def __init__(
        self, first: CameraWebRTCProvider, earlier: set[CameraWebRTCProvider]
    ) -> None:
        """Take over the providers of `earlier`, with `first` to be yielded first."""
        super().__init__(earlier)
        self.first = first
        self._earlier = earlier
        self._taken_over = set(earlier)

    def _registered(self, provider: object) -> bool:
        """Whether `provider` is registered, which a stale taken-over one is not."""
        return super().__contains__(provider) and (
            provider not in self._taken_over or provider in self._earlier
        )

    @override
    def __contains__(self, provider: object) -> bool:
        """Whether `provider` is registered."""
        return self._registered(provider)

    @override
    def __iter__(self) -> Iterator[CameraWebRTCProvider]:
        """Yield `first`, if registered, then the other registered providers."""
        if self._registered(self.first):
            yield self.first
        for provider in super().__iter__():
            if provider is not self.first and self._registered(provider):
                yield provider

    @override
    def add(self, provider: CameraWebRTCProvider) -> None:
        """Register `provider`, also one taken over whose own callback removed it."""
        self._taken_over.discard(provider)
        super().add(provider)


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
