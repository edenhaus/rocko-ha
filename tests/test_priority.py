"""Tests for making rocko the WebRTC provider that cameras try first."""

import gc
from typing import override
import weakref

from pytest_homeassistant_custom_component.common import MockConfigEntry
from webrtc_models import RTCIceCandidateInit

from custom_components.rocko.priority import FirstProviderSet
from homeassistant.components.camera import (
    DOMAIN as CAMERA_DOMAIN,
    Camera,
    CameraWebRTCProvider,
    WebRTCSendMessage,
    async_register_webrtc_provider,
)
from homeassistant.components.camera.webrtc import DATA_WEBRTC_PROVIDERS
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from . import MockCamera
from .conftest import MockClients, async_setup_camera


class OtherProvider(CameraWebRTCProvider):
    """A provider that takes every camera, as go2rtc takes the RTSP ones."""

    @property
    @override
    def domain(self) -> str:
        """The provider's integration."""
        return "other"

    @override
    def async_is_supported(self, stream_source: str) -> bool:
        """Every stream source."""
        return True

    @override
    async def async_handle_async_webrtc_offer(
        self,
        camera: Camera,
        offer_sdp: str,
        session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        """Never called: these tests only look at which provider a camera gets."""

    @override
    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Never called: these tests only look at which provider a camera gets."""


def _providers(hass: HomeAssistant) -> list[CameraWebRTCProvider]:
    """The registered providers in the order a camera tries them."""
    return list(hass.data[DATA_WEBRTC_PROVIDERS])


def _rocko(entry: MockConfigEntry) -> CameraWebRTCProvider:
    """The loaded entry's provider."""
    return entry.runtime_data.provider


async def test_first_over_a_provider_registered_before(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """A camera another provider took before rocko loaded becomes rocko's."""
    assert await async_setup_component(hass, CAMERA_DOMAIN, {})
    other = OtherProvider()
    async_register_webrtc_provider(hass, other)
    camera = MockCamera("camera_unique_id")
    await async_setup_camera(hass, camera)
    assert camera.webrtc_provider is other

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    rocko = _rocko(mock_config_entry)
    assert isinstance(hass.data[DATA_WEBRTC_PROVIDERS], FirstProviderSet)
    assert _providers(hass) == [rocko, other]
    assert camera.webrtc_provider is rocko


async def test_first_over_a_provider_registered_after(
    hass: HomeAssistant, init_integration: MockConfigEntry, camera: MockCamera
) -> None:
    """A provider registered after rocko comes after it."""
    other = OtherProvider()
    async_register_webrtc_provider(hass, other)
    await hass.async_block_till_done()

    rocko = _rocko(init_integration)
    assert _providers(hass) == [rocko, other]
    assert camera.webrtc_provider is rocko


async def test_other_provider_for_a_source_rocko_does_not_read(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A camera whose source rocko does not read goes to the next provider."""
    other = OtherProvider()
    async_register_webrtc_provider(hass, other)
    camera = MockCamera("camera_unique_id")
    camera.set_stream_source("rtmp://192.0.2.10/live")
    await async_setup_camera(hass, camera)

    assert camera.webrtc_provider is other


async def test_provider_registered_before_removes_itself(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_clients: MockClients
) -> None:
    """A provider taken over is gone once its own callback removes it, and can return."""
    assert await async_setup_component(hass, CAMERA_DOMAIN, {})
    other = OtherProvider()
    remove_other = async_register_webrtc_provider(hass, other)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    rocko = _rocko(mock_config_entry)

    # Core's callback removes it from the set it registered in, the one replaced.
    remove_other()
    await hass.async_block_till_done()
    assert other not in hass.data[DATA_WEBRTC_PROVIDERS]
    assert _providers(hass) == [rocko]

    # Registering the same provider again is no "already registered" error.
    remove_other = async_register_webrtc_provider(hass, other)
    await hass.async_block_till_done()
    assert other in hass.data[DATA_WEBRTC_PROVIDERS]
    assert _providers(hass) == [rocko, other]

    remove_other()
    await hass.async_block_till_done()
    assert _providers(hass) == [rocko]


async def test_unload_and_reload(
    hass: HomeAssistant, init_integration: MockConfigEntry, camera: MockCamera
) -> None:
    """Without rocko a camera goes to the other provider; a new entry is first again."""
    other = OtherProvider()
    async_register_webrtc_provider(hass, other)
    await hass.async_block_till_done()
    providers = hass.data[DATA_WEBRTC_PROVIDERS]
    old_rocko = _rocko(init_integration)

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert _providers(hass) == [other]
    assert providers.first is None
    assert camera.webrtc_provider is other

    assert await hass.config_entries.async_setup(init_integration.entry_id)
    await hass.async_block_till_done()
    rocko = _rocko(init_integration)
    assert rocko is not old_rocko
    assert hass.data[DATA_WEBRTC_PROVIDERS] is providers
    assert _providers(hass) == [rocko, other]
    assert camera.webrtc_provider is rocko


def test_removed_providers_are_not_kept() -> None:
    """No provider is kept once removed, from the earlier set or from this one."""
    before = OtherProvider()
    earlier = {before}
    first = OtherProvider()
    providers = FirstProviderSet(first, earlier)
    assert list(providers) == [before]
    providers.add(first)
    later = OtherProvider()
    providers.add(later)
    assert list(providers) == [first, before, later]
    assert len(providers) == 3
    refs = [weakref.ref(provider) for provider in (before, first, later)]

    # Core's callback of a provider registered before the swap removes it from
    # the earlier set; those of the providers registered since, from this one.
    earlier.remove(before)
    providers.remove(first)
    providers.remove(later)
    assert providers.first is None
    assert not providers
    assert list(providers) == []

    del before, first, later
    gc.collect()
    assert [ref() for ref in refs] == [None, None, None]
