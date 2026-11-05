"""Configure participant consent and installation credentials."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import API_KEY, AQ, DOMAIN, HUMIDITY, PERMS_TITLE, PRESSURE, SHARE_LOCATION, TEMP


def permissions_schema(options=None, *, initial=False):
    options = options or {}
    # Password selectors hide typed credentials; never put an existing key into a
    # form default. HA stores entries locally, so backups still need protection.
    key = vol.Required(API_KEY) if initial else vol.Optional(API_KEY)
    return vol.Schema(
        {
            key: selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(TEMP, default=options.get(TEMP, True)): bool,
            vol.Optional(HUMIDITY, default=options.get(HUMIDITY, True)): bool,
            vol.Optional(PRESSURE, default=options.get(PRESSURE, True)): bool,
            vol.Optional(AQ, default=options.get(AQ, False)): bool,
            vol.Optional(SHARE_LOCATION, default=options.get(SHARE_LOCATION, True)): bool,
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            user_input = dict(user_input)
            key = user_input.pop(API_KEY, "").strip()
            if key:
                return self.async_create_entry(
                    title=PERMS_TITLE, data={API_KEY: key}, options=user_input
                )
            errors[API_KEY] = "credential_required"
        return self.async_show_form(
            step_id="user", data_schema=permissions_schema(initial=True), errors=errors
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        return await self.async_step_options(user_input)

    async def async_step_options(self, user_input=None):
        if user_input is not None:
            options = dict(user_input)
            key = options.pop(API_KEY, "").strip()
            # Blank means retain, so changing consent never erases a working key.
            if key:
                entry = self.hass.config_entries.async_get_entry(self.handler)
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, API_KEY: key},
                    # Reload must apply new consent together with the credential.
                    options=options,
                )
            return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="options",
            data_schema=permissions_schema(
                self.hass.config_entries.async_get_entry(self.handler).options
            ),
        )
