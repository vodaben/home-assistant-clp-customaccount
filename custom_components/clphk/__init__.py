import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_DOMAIN,
)


async def async_setup(hass: HomeAssistant, config: dict):
    session = async_get_clientsession(hass)
    hass.data[CONF_DOMAIN] = {
        "session": session
    }
    return True


async def async_setup_entry(hass: HomeAssistant, entry):
    if CONF_DOMAIN not in hass.data:
        session = async_get_clientsession(hass)
        hass.data[CONF_DOMAIN] = {
            "session": session,
            "access_token": entry.data.get("access_token"),
            "refresh_token": entry.data.get("refresh_token"),
            "access_token_expiry_time": entry.data.get("access_token_expiry_time"),
            "token_lock": asyncio.Lock(),
        }
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True

async def async_unload_entry(hass: HomeAssistant, entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok and CONF_DOMAIN in hass.data:
        hass.data.pop(CONF_DOMAIN)
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry):
    """Reload config entry when options or data change."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
