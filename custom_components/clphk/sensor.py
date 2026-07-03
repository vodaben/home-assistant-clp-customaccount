from __future__ import annotations

import asyncio
import datetime
import json as jsonlib
import logging

import aiohttp
import homeassistant.helpers.config_validation as cv
import pytz
import voluptuous as vol
from dateutil import relativedelta
from homeassistant.components.lock import PLATFORM_SCHEMA
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    CONF_TIMEOUT,
    CONF_TYPE,
    UnitOfEnergy,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import Throttle

from .const import (
    CONF_DOMAIN,
    CONF_RETRY_DELAY,
    is_auth_failure,
    is_transient,
    parse_datetime,
    parse_refresh_tokens,
    safe_float,

    CONF_GET_ACCT,
    CONF_GET_BILL,
    CONF_GET_ESTIMATION,
    CONF_GET_BIMONTHLY,
    CONF_GET_DAILY,
    CONF_GET_HOURLY,
    CONF_GET_HOURLY_DAYS,

    CONF_RES_ENABLE,
    CONF_RES_NAME,
    CONF_RES_TYPE,
    CONF_RES_GET_BILL,
    CONF_RES_GET_DAILY,
    CONF_RES_GET_HOURLY,
    CONF_RES_GET_HOURLY_DAYS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_TIMEOUT, default=30): cv.positive_int,
    vol.Optional(CONF_RETRY_DELAY, default=300): cv.positive_int,
    vol.Optional(CONF_NAME, default='CLP'): cv.string,
    vol.Optional(CONF_TYPE, default=''): cv.string,
    vol.Optional(CONF_GET_ACCT, default=False): cv.boolean,
    vol.Optional(CONF_GET_BILL, default=False): cv.boolean,
    vol.Optional(CONF_GET_ESTIMATION, default=False): cv.boolean,
    vol.Optional(CONF_GET_BIMONTHLY, default=False): cv.boolean,
    vol.Optional(CONF_GET_DAILY, default=False): cv.boolean,
    vol.Optional(CONF_GET_HOURLY, default=False): cv.boolean,
    vol.Optional(CONF_GET_HOURLY_DAYS, default=1): vol.Clamp(min=1, max=2),

    vol.Optional(CONF_RES_ENABLE, default=False): cv.boolean,
    vol.Optional(CONF_RES_NAME, default='CLP Renewable Energy'): cv.string,
    vol.Optional(CONF_RES_TYPE, default=''): cv.string,
    vol.Optional(CONF_RES_GET_BILL, default=False): cv.boolean,
    vol.Optional(CONF_RES_GET_DAILY, default=False): cv.boolean,
    vol.Optional(CONF_RES_GET_HOURLY, default=False): cv.boolean,
    vol.Optional(CONF_RES_GET_HOURLY_DAYS, default=1): vol.Clamp(min=1, max=2),
})

MIN_TIME_BETWEEN_UPDATES = datetime.timedelta(seconds=300)
DAILY_TASK_INTERVAL = datetime.timedelta(hours=12)
HOURLY_TASK_INTERVAL = datetime.timedelta(minutes=30)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"

DOMAIN = CONF_DOMAIN

class FatalAuthError(Exception):
    """Non-recoverable auth error that requires reconfiguration."""


API_DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "en",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Referer": "https://www.clp.com.hk/",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
}


async def async_setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the sensor platform."""
    if discovery_info is None:
        return

    session = aiohttp_client.async_get_clientsession(hass)

    # Shared token state in hass.data[DOMAIN]
    hass.data[DOMAIN]["session"] = session
    # Set tokens on restart (if not already set)
    for k in ("access_token", "refresh_token", "access_token_expiry_time"):
        if discovery_info.get(k) is not None and discovery_info.get(k) != "":
            hass.data[DOMAIN][k] = discovery_info.get(k)
    hass.data[DOMAIN]["token_lock"] = asyncio.Lock()

    async_add_entities(
        [
            CLPSensor(
                hass=hass,
                sensor_type='main',
                name=discovery_info.get(CONF_NAME, "CLP"),
                timeout=int(discovery_info.get(CONF_TIMEOUT, 30)),
                retry_delay=int(discovery_info.get(CONF_RETRY_DELAY, 300)),
                type=discovery_info.get(CONF_TYPE, ""),
                get_acct=discovery_info.get(CONF_GET_ACCT, False),
                get_bill=discovery_info.get(CONF_GET_BILL, False),
                get_estimation=discovery_info.get(CONF_GET_ESTIMATION, False),
                get_bimonthly=discovery_info.get(CONF_GET_BIMONTHLY, False),
                get_daily=discovery_info.get(CONF_GET_DAILY, False),
                get_hourly=discovery_info.get(CONF_GET_HOURLY, False),
                get_hourly_days=int(discovery_info.get(CONF_GET_HOURLY_DAYS, 1)),
            ),
        ],
        update_before_add=True,
    )

    if discovery_info.get(CONF_RES_ENABLE, False):
        async_add_entities(
            [
                CLPSensor(
                    hass=hass,
                    sensor_type='renewable_energy',
                    name=discovery_info.get(CONF_RES_NAME, "CLP Renewable Energy"),
                    timeout=int(discovery_info.get(CONF_TIMEOUT, 30)),
                    retry_delay=int(discovery_info.get(CONF_RETRY_DELAY, 300)),
                    type=discovery_info.get(CONF_RES_TYPE, ""),
                    get_acct=False,
                    get_bill=discovery_info.get(CONF_RES_GET_BILL, False),
                    get_estimation=False,
                    get_bimonthly=False,
                    get_daily=discovery_info.get(CONF_RES_GET_DAILY, False),
                    get_hourly=discovery_info.get(CONF_RES_GET_HOURLY, False),
                    get_hourly_days=int(discovery_info.get(CONF_RES_GET_HOURLY_DAYS, 1)),
                ),
            ],
            update_before_add=True,
        )


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform from a config entry."""
    # Merge config_entry.data and config_entry.options, options take precedence
    merged = {**config_entry.data, **config_entry.options}
    await async_setup_platform(
        hass,
        {},
        async_add_entities,
        discovery_info=merged,
    )


def get_dates(timezone):
    return {
        "yesterday": datetime.datetime.now(timezone) + datetime.timedelta(days=-1),
        "today": datetime.datetime.now(timezone),
        "tomorrow": datetime.datetime.now(timezone) + datetime.timedelta(days=1),
        "one_year_two_months_ago": (datetime.datetime.now(timezone) - relativedelta.relativedelta(years=1, months=2)).replace(day=datetime.datetime.now(timezone).day),
        "last_month": (datetime.datetime.now(timezone).replace(day=1) + relativedelta.relativedelta(months=-1)),
        "this_month": datetime.datetime.now(timezone).replace(day=1),
        "next_month": (datetime.datetime.now(timezone).replace(day=1) + relativedelta.relativedelta(months=1)),
    }


def handle_errors(func):
    """Record a fetch failure on the entity and swallow it so sibling fetches in
    the same update cycle still run. FatalAuthError is re-raised so async_update
    can stop the cycle. Retries are driven by Home Assistant's own polling,
    rate-limited by the @Throttle on async_update."""
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except FatalAuthError:
            raise
        except Exception as e:
            self._error = str(e)
            _LOGGER.error(f"{self._name} ERROR: {e}", exc_info=True)
            return None

    return wrapper


class CLPSensor(SensorEntity):
    _timezone = pytz.timezone('Asia/Hong_Kong')

    def __init__(
            self,
            hass,
            sensor_type: str,
            name: str,
            timeout: int,
            retry_delay: int,
            type: str = None,
            get_acct: bool = False,
            get_bill: bool = False,
            get_estimation: bool = False,
            get_bimonthly: bool = False,
            get_daily: bool = False,
            get_hourly: bool = False,
            get_hourly_days: int = 1,
    ) -> None:
        _LOGGER.debug(f"[SENSOR INIT] type={sensor_type}, name={name}")
        self.hass = hass
        self._sensor_type = sensor_type
        self._name = name
        self._timeout = timeout
        self._retry_delay = retry_delay
        self._type = type
        self._get_acct = get_acct
        self._get_bill = get_bill
        self._get_estimation = get_estimation
        self._get_bimonthly = get_bimonthly
        self._get_daily = get_daily
        self._get_hourly = get_hourly
        self._get_hourly_days = get_hourly_days
        self._account_number = None
        self._state_data_type = None
        self._error = None
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_name = name
        self._attr_unique_id = f"clphk_{sensor_type}_{name.replace(' ', '_').lower()}"

        self._account = None
        self._bills = None
        self._estimation = None
        self._bimonthly = None
        self._daily = None
        self._hourly = None

        self._single_task_last_fetch_time = None
        self._hourly_task_last_fetch_time = None
        self._daily_task_last_fetch_time = None
        self._no_account_warned = False

    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def name(self):
        return self._attr_name

    @property
    def state(self):
        return self._attr_native_value

    @property
    def _token_state(self):
        return self.hass.data[DOMAIN]

    @property
    def _access_token(self):
        return self._token_state.get("access_token")

    @property
    def _refresh_token(self):
        return self._token_state.get("refresh_token")

    @property
    def _access_token_expiry_time(self):
        return self._token_state.get("access_token_expiry_time")

    @_access_token.setter
    def _access_token(self, value):
        self._token_state["access_token"] = value

    @_refresh_token.setter
    def _refresh_token(self, value):
        self._token_state["refresh_token"] = value

    @_access_token_expiry_time.setter
    def _access_token_expiry_time(self, value):
        self._token_state["access_token_expiry_time"] = value

    @property
    def _session(self):
        return self._token_state["session"]

    @property
    def extra_state_attributes(self) -> dict:
        attr = {
            "state_data_type": self._state_data_type,
            "error": self._error,
        }

        if self._get_acct and hasattr(self, '_account'):
            attr["account"] = self._account

        if self._get_bill and hasattr(self, '_bills'):
            attr["bills"] = self._bills

        if self._get_estimation and hasattr(self, '_estimation'):
            attr["estimation"] = self._estimation

        if self._get_bimonthly and hasattr(self, '_bimonthly'):
            attr["bimonthly"] = self._bimonthly

        if self._get_daily and hasattr(self, '_daily'):
            attr["daily"] = self._daily

        if self._get_hourly and hasattr(self, '_hourly'):
            attr["hourly"] = self._hourly

        return attr


    async def api_request(
            self,
            method: str,
            url: str,
            headers: dict = None,
            json: dict = None,
            params: dict = None,
            retry_on_expired: bool = True,
    ):
        if not self._access_token and 'eligibilityCheckAndLogin' not in url and 'refresh_token' not in url:
            raise Exception("Problematic authorization. Please configure again, or change your IP address.")

        if json:
            _LOGGER.debug(f"REQUEST {method} {headers} {url} {params} {json}")

        merged_headers = dict(API_DEFAULT_HEADERS)
        if json is not None:
            merged_headers["Content-Type"] = "application/json"
        if headers:
            merged_headers.update(headers)

        async with asyncio.timeout(self._timeout):
            response = await self._session.request(
                method,
                url,
                headers=merged_headers,
                params=params,
                json=json,
            )

            try:
                response.raise_for_status()
            except aiohttp.ClientResponseError as e:
                error_message = f"{e.status} {e.request_info.url}"
                error_data = None
                error_content = ""

                try:
                    # Try to read the response content only once and store it
                    error_content = await response.text()
                    try:
                        error_data = jsonlib.loads(error_content)
                        error_message += f" : {error_data}"
                    except jsonlib.JSONDecodeError:
                        error_message += f" : {error_content}"
                except Exception as read_error:
                    error_message += f" (Failed to read error response: {read_error})"
                
                _LOGGER.error(error_message)

                if 400 <= e.status < 500:
                    # Attempt token refresh on:
                    # - Known expiry codes: 906 (token expired), 100001 (LR access_token error)
                    # - 403 with unreadable body (connection closed before response could be read)
                    error_code = error_data.get("code") if isinstance(error_data, dict) else None
                    should_refresh = (
                        retry_on_expired
                        and "refresh_token" not in url
                        and self._refresh_token
                        # Only refresh when the failed request actually carried an
                        # access token. Login/refresh calls have no Authorization, so
                        # they can never re-enter _refresh_access_token (which would
                        # deadlock on the non-reentrant token_lock).
                        and (headers or {}).get("Authorization")
                        and (
                            error_code in (906, 100001)
                            or (e.status == 403 and error_data is None)
                        )
                    )
                    if should_refresh:
                        _LOGGER.debug("Access token likely expired (status=%s, code=%s, body_readable=%s). Refreshing and retrying once.", e.status, error_code, error_data is not None)
                        stale_token = (headers or {}).get("Authorization")
                        await self._refresh_access_token(stale_access_token=stale_token)
                        retry_headers = dict(headers or {})
                        if "Authorization" in retry_headers:
                            retry_headers["Authorization"] = self._access_token
                        return await self.api_request(
                            method=method,
                            url=url,
                            headers=retry_headers,
                            json=json,
                            params=params,
                            retry_on_expired=False,
                        )

                    # Only clear credentials on a genuine, unrecoverable auth failure.
                    # Transient/non-auth 4xx (400/404/408/409/422/429/...) must NOT
                    # wipe tokens - that would kill the integration on a rate limit
                    # or a single bad request.
                    if is_auth_failure(e.status, error_code):
                        await self._handle_auth_failure(e.status, error_content or "")
                        raise FatalAuthError(
                            f"CLPHK authentication failed (HTTP {e.status}, code {error_code}); "
                            "tokens cleared, integration stopped."
                        )

                    raise e

                raise e

            try:
                response_data = await response.json()

                if not response_data or 'data' not in response_data:
                    _LOGGER.error(f"RESPONSE {response.status} {response.url} : {response_data}")
                    raise ValueError('Invalid response data')

                _LOGGER.debug(f"RESPONSE {response.status} {response.url} : {response_data}")

                return response_data
            except Exception as _:
                response_text = await response.text()
                _LOGGER.error(f"{response.status} {response.url} : {response_text}")
                raise

    async def _refresh_access_token(self, stale_access_token=None):
        """Refresh access token using stored refresh token and persist it.

        Serialized via the shared token_lock so the two sensors cannot both POST
        the (rotating) refresh_token concurrently. stale_access_token is the token
        the failed request carried; if it no longer matches once we hold the lock,
        another task already refreshed (or auth was cleared) and we must not fire a
        second refresh with a possibly-rotated refresh_token.
        """
        token_state = self.hass.data.get(DOMAIN)
        token_lock = token_state.get("token_lock") if token_state else None
        if token_lock is None:
            raise FatalAuthError(
                "CLPHK token state/lock unavailable; integration is unloading or not initialized."
            )

        async with token_lock:
            # Unload-then-reload can replace hass.data[DOMAIN] with a fresh dict/lock
            # while we waited; our lock would then guard nothing.
            if self.hass.data.get(DOMAIN) is not token_state:
                raise FatalAuthError(
                    "CLPHK state replaced (reload) while awaiting refresh lock; aborting."
                )

            if stale_access_token is not None and self._access_token != stale_access_token:
                if self._access_token is None:
                    raise FatalAuthError(
                        "CLPHK auth cleared by a concurrent failure; aborting refresh."
                    )
                _LOGGER.debug("Access token already refreshed by another task; skipping refresh.")
                return

            if not self._refresh_token:
                raise Exception("No refresh token available")

            refresh_headers = dict(API_DEFAULT_HEADERS)
            refresh_headers["Content-Type"] = "application/json"

            async with asyncio.timeout(self._timeout):
                response = await self._session.request(
                    "POST",
                    "https://api.clp.com.hk/ts1/ms/profile/identity/manage/account/refresh_token",
                    headers=refresh_headers,
                    json={"refreshToken": self._refresh_token},
                )
                response_text = await response.text()

            if is_transient(response.status):
                # Rate limit / timeout on the refresh endpoint is temporary; keep the
                # tokens and let the caller back off instead of forcing reconfigure.
                raise Exception(
                    f"Refresh token request temporarily failed with {response.status}: {response_text[:200]}"
                )
            if 400 <= response.status < 500:
                await self._handle_auth_failure(response.status, response_text)
                raise FatalAuthError(
                    "Refresh token invalid/expired. CLPHK integration stopped; please reconfigure tokens."
                )
            if response.status >= 500:
                raise Exception(
                    f"Refresh token request failed with {response.status}: {response_text[:200]}"
                )

            try:
                response_json = jsonlib.loads(response_text)
            except jsonlib.JSONDecodeError as ex:
                raise Exception(f"Refresh token response is not JSON: {response_text[:200]}") from ex

            tokens = parse_refresh_tokens(response_json)
            if tokens is None:
                # Well-formed 200 without usable tokens is unrecoverable: the
                # refresh token may already be rotated/consumed, so retrying is
                # futile. Stop cleanly instead of KeyError -> retry loop.
                await self._handle_auth_failure(
                    response.status,
                    response_text,
                    detail="refresh response was missing token fields",
                )
                raise FatalAuthError(
                    "Refresh response missing access_token/refresh_token; "
                    "tokens cleared, integration stopped."
                )
            self._access_token, self._refresh_token, self._access_token_expiry_time = tokens

            _LOGGER.debug(f"[SENSOR UPDATE] Persisting refreshed tokens to config entry.")
            config_entries = self.hass.config_entries.async_entries(DOMAIN)
            if config_entries:
                entry = config_entries[0]
                data = dict(entry.data)
                data["access_token"] = self._access_token
                data["refresh_token"] = self._refresh_token
                data["access_token_expiry_time"] = self._access_token_expiry_time
                self.hass.config_entries.async_update_entry(entry, data=data)

    async def _handle_auth_failure(self, status: int, body: str, detail: str = None):
        """Clear tokens, notify frontend, and stop integration on an auth failure."""
        self._account_number = None
        self._access_token = None
        self._refresh_token = None
        self._access_token_expiry_time = None

        config_entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in config_entries:
            data = dict(entry.data)
            data["access_token"] = ""
            data["refresh_token"] = ""
            data["access_token_expiry_time"] = ""
            self.hass.config_entries.async_update_entry(entry, data=data)

        reason = f" ({detail})" if detail else ""
        message = (
            "CLPHK authentication failed"
            f"{reason} with HTTP {status}. Tokens were cleared and the integration "
            "was stopped. Please reconfigure Access Token and Refresh Token.\n\n"
            f"Response: {body[:300]}"
        )
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "CLPHK Authentication Failed",
                    "message": message,
                    "notification_id": "clphk_auth_failed",
                },
                blocking=False,
            )
        except Exception:
            _LOGGER.warning("Failed to create persistent notification for CLPHK auth failure.")

        for entry in config_entries:
            self.hass.async_create_task(self.hass.config_entries.async_unload(entry.entry_id))


    @handle_errors
    async def main_get_account_detail(self):
        response = await self.api_request(
            method="GET",
            url="https://api.clp.com.hk/ts1/ms/profile/accountdetails/myServicesCA",
            headers={
                "Authorization": self._access_token,
            },
        )
        # Find the first entry with status 'Active' that has a usable account number.
        active_data = next((item for item in (response['data'] or []) if item.get('status') == 'Active'), None)
        ca_no = active_data.get('caNo') if active_data else None
        if not ca_no:
            self._account_number = None
            self._account = None
            if not self._no_account_warned:
                _LOGGER.warning("%s: no active CLP account with a usable account number; will keep retrying.", self._name)
                self._no_account_warned = True
            # Leave _single_task_last_fetch_time unset so account detail is retried
            # next cycle (recover from a transient empty/incomplete response).
            return

        self._account_number = ca_no
        self._account = {
            'number': ca_no,
            'outstanding': safe_float(active_data.get('outstandingAmount')),
            'due_date': parse_datetime(active_data.get('dueDate'), '%Y%m%d%H%M%S'),
        }
        self._no_account_warned = False
        self._single_task_last_fetch_time = datetime.datetime.now(self._timezone)


    @handle_errors
    async def main_get_bill(self):
        response = await self.api_request(
            method="POST",
            url="https://api.clp.com.hk/ts1/ms/billing/transaction/historyBilling",
            headers={
                "Authorization": self._access_token,
            },
            json={
                "caList": [
                    {
                        "ca": self._account_number,
                    },
                ],
            },
        )

        transactions = (response['data'] or {}).get('transactions') or []
        if transactions:
            bills = {
                'bill': [],
                'payment': [],
            }
            for row in transactions:
                if row['type'] != 'bill' and row['type'] != 'payment':
                    continue

                record = {
                    'total': float(row['total']),
                    'transaction_date': datetime.datetime.strptime(row['tranDate'], '%Y%m%d%H%M%S'),
                }

                if row['type'] == 'bill':
                    record['from_date'] = datetime.datetime.strptime(row['fromDate'], '%Y%m%d%H%M%S')
                    record['to_date'] = datetime.datetime.strptime(row['toDate'], '%Y%m%d%H%M%S')

                bills[row['type']].append(record)

            bills['bill'] = sorted(bills['bill'], key=lambda x: x['transaction_date'], reverse=True)
            bills['payment'] = sorted(bills['payment'], key=lambda x: x['transaction_date'], reverse=True)
            self._bills = bills
            self._daily_task_last_fetch_time = datetime.datetime.now(self._timezone)


    @handle_errors
    async def main_get_estimation(self):
        response = await self.api_request(
            method="GET",
            url="https://api.clp.com.hk/ts1/ms/consumption/info",
            headers={
                "Authorization": self._access_token,
            },
            params={
                "ca": self._account_number,
            },
        )

        data = response['data']
        if data:
            self._estimation = {
                "current_consumption": safe_float(data.get('currentConsumption')),
                "current_cost": safe_float(data.get('currentCost')),
                "current_end_date": parse_datetime(data.get('currentEndDate'), '%Y%m%d%H%M%S'),
                "current_start_date": parse_datetime(data.get('currentStartDate'), '%Y%m%d%H%M%S'),
                "deviation_percent": safe_float(data.get('deviationPercent')),
                "estimation_consumption": safe_float(data.get('projectedConsumption')),
                "estimation_cost": safe_float(data.get('projectedCost')),
                "estimation_end_date": parse_datetime(data.get('projectedEndDate'), '%Y%m%d%H%M%S'),
                "estimation_start_date": parse_datetime(data.get('projectedStartDate'), '%Y%m%d%H%M%S'),
            }
            self._daily_task_last_fetch_time = datetime.datetime.now(self._timezone)


    @handle_errors
    async def main_get_bimonthly(self):
        dates = get_dates(self._timezone)

        response = await self.api_request(
            method="POST",
            url="https://api.clp.com.hk/ts1/ms/consumption/history",
            headers={
                "Authorization": self._access_token,
            },
            json={
                "ca": self._account_number,
                "fromDate": dates["one_year_two_months_ago"].strftime('%Y%m%d000000'),
                "mode": "Bill",
                "toDate": dates["today"].strftime('%Y%m%d000000'),
                "type": "Unit",
            },
        )

        if response['data']:
            results = response['data'].get('results') or []
            if results and (self._type == '' or self._type.upper() == 'BIMONTHLY'):
                self._state_data_type = 'BIMONTHLY'
                self._attr_native_value = results[0]['totKwh']
                self._attr_last_reset = datetime.datetime.strptime(results[0]['endabrpe'], '%Y%m%d')

            if self._get_bimonthly:
                bimonthly = []
                for row in results:
                    bimonthly.append({
                        'end': datetime.datetime.strptime(row['endabrpe'], '%Y%m%d'),
                        'kwh': row['totKwh'],
                    })
                self._bimonthly = sorted(bimonthly, key=lambda x: x['end'], reverse=True)
            self._daily_task_last_fetch_time = datetime.datetime.now(self._timezone)


    @handle_errors
    async def main_get_daily(self):
        dates = get_dates(self._timezone)

        response = await self.api_request(
            method="POST",
            url="https://api.clp.com.hk/ts1/ms/consumption/history",
            headers={
                "Authorization": self._access_token,
            },
            json={
                "ca": self._account_number,
                "fromDate": dates["this_month"].strftime("%Y%m%d000000"),
                "mode": "Daily",
                "toDate": dates["next_month"].strftime("%Y%m%d000000"),
                "type": "Unit",
            },
        )

        if response['data']:
            results = response['data'].get('results') or []
            if results and (self._type == '' or self._type.upper() == 'DAILY'):
                self._state_data_type = 'DAILY'
                self._attr_native_value = results[-1]['kwhTotal']
                self._attr_last_reset = datetime.datetime.strptime(
                    results[-1]['expireDate'], '%Y%m%d%H%M%S')

            if self._get_daily:
                daily = []
                for row in results:
                    start = None
                    if row['startDate']:
                        start = datetime.datetime.strptime(row['startDate'], '%Y%m%d%H%M%S')

                    end = None
                    if row['expireDate']:
                        end = datetime.datetime.strptime(row['expireDate'], '%Y%m%d%H%M%S')

                    daily.append({
                        'start': start,
                        'end': end,
                        'kwh': row['kwhTotal'],
                    })
                self._daily = sorted(daily, key=lambda x: x['start'], reverse=True)

            self._daily_task_last_fetch_time = datetime.datetime.now(self._timezone)


    @handle_errors
    async def main_get_hourly(self):
        hourly = []
        for i in range(1, self._get_hourly_days + 1):
            from_date = datetime.datetime.now(self._timezone) + datetime.timedelta(days=-(self._get_hourly_days - i))
            to_date = datetime.datetime.now(self._timezone) + datetime.timedelta(days=-(self._get_hourly_days - i - 1))

            if datetime.time(0, 0) <= datetime.datetime.now(self._timezone).time() < datetime.time(4, 0):
                from_date = from_date + datetime.timedelta(days=-1)
                to_date = to_date + datetime.timedelta(days=-1)

            response = await self.api_request(
                method="POST",
                url="https://api.clp.com.hk/ts1/ms/consumption/history",
                headers={
                    "Authorization": self._access_token,
                },
                json={
                    "ca": self._account_number,
                    "fromDate": from_date.strftime("%Y%m%d000000"),
                    "mode": "Hourly",
                    "toDate": to_date.strftime("%Y%m%d000000"),
                    "type": "Unit",
                },
            )

            results = (response['data'] or {}).get('results') or []
            if results:
                if i == self._get_hourly_days and (self._type == '' or self._type.upper() == 'HOURLY'):
                    self._state_data_type = 'HOURLY'
                    self._attr_native_value = results[-1]['kwhTotal']
                    self._attr_last_reset = datetime.datetime.strptime(
                        results[-1]['expireDate'], '%Y%m%d%H%M%S')

                if self._get_hourly:
                    for row in results:
                        hourly.append({
                            'start': datetime.datetime.strptime(row['startDate'], '%Y%m%d%H%M%S'),
                            'kwh': row['kwhTotal'],
                        })

                self._hourly_task_last_fetch_time = datetime.datetime.now(self._timezone)

        if self._get_hourly:
            self._hourly = sorted(hourly, key=lambda x: x['start'], reverse=True)


    @handle_errors
    async def renewable_get_bimonthly(self):
        dates = get_dates(self._timezone)

        response = await self.api_request(
            method="POST",
            url="https://api.clp.com.hk/ts1/ms/renew/fit/dashboard",
            headers={
                "Authorization": self._access_token,
            },
            json={
                "caNo": self._account_number,
                "mode": "B",
                "startDate": dates["today"].strftime("%m/%d/%Y"),
            },
        )

        consumption_data = (response['data'] or {}).get('consumptionData') or []
        if consumption_data:
            if self._type == '' or self._type.upper() == 'BIMONTHLY':
                self._state_data_type = 'BIMONTHLY'
                self._attr_native_value = float(consumption_data[-1]['kwhtotal'])
                self._attr_last_reset = datetime.datetime.strptime(consumption_data[-1]['enddate'], '%Y%m%d%H%M%S')

            if self._get_bill:
                bills = []
                for row in consumption_data:
                    bills.append({
                        'start': datetime.datetime.strptime(row['startdate'], '%Y%m%d%H%M%S'),
                        'end': datetime.datetime.strptime(row['enddate'], '%Y%m%d%H%M%S'),
                        'kwh': float(row['kwhtotal']),
                    })
                self._bills = sorted(bills, key=lambda x: x['start'], reverse=True)

            self._daily_task_last_fetch_time = datetime.datetime.now(self._timezone)


    @handle_errors
    async def renewable_get_daily(self):
        dates = get_dates(self._timezone)

        response = await self.api_request(
            method="POST",
            url="https://api.clp.com.hk/ts1/ms/renew/fit/dashboard",
            headers={
                "Authorization": self._access_token,
            },
            json={
                "caNo": self._account_number,
                "mode": "D",
                "startDate": dates["today"].strftime("%m/%d/%Y"),
            },
        )

        consumption_data = (response['data'] or {}).get('consumptionData') or []
        if consumption_data:
            if self._type == '' or self._type.upper() == 'DAILY':
                for row in sorted(consumption_data, key=lambda x: x.get('startdate') or '', reverse=True):
                    if row.get('validateStatus') == 'Y':
                        self._state_data_type = 'DAILY'
                        self._attr_native_value = safe_float(row.get('kwhtotal'))
                        self._attr_last_reset = parse_datetime(row.get('startdate'), '%Y%m%d%H%M%S')
                        break

            if self._get_daily:
                daily = []

                for row in consumption_data:
                    daily.append({
                        'start': parse_datetime(row.get('startdate'), '%Y%m%d%H%M%S'),
                        'kwh': safe_float(row.get('kwhtotal')),
                    })

                self._daily = sorted(daily, key=lambda x: x['start'] or datetime.datetime.min, reverse=True)

            self._daily_task_last_fetch_time = datetime.datetime.now(self._timezone)


    @handle_errors
    async def renewable_get_hourly(self):
        hourly = []
        for i in range(1, self._get_hourly_days + 1):
            start_date = datetime.datetime.now(self._timezone) + datetime.timedelta(days=-(self._get_hourly_days - i))

            if datetime.time(0, 0) <= datetime.datetime.now(self._timezone).time() < datetime.time(4, 0):
                start_date = start_date + datetime.timedelta(days=-1)

            response = await self.api_request(
                method="POST",
                url="https://api.clp.com.hk/ts1/ms/renew/fit/dashboard",
                headers={
                    "Authorization": self._access_token,
                },
                json={
                    "caNo": self._account_number,
                    "mode": "H",
                    "startDate": start_date.strftime("%m/%d/%Y"),
                },
            )

            consumption_data = (response['data'] or {}).get('consumptionData') or []
            if consumption_data:
                if i == 1 and (self._type == '' or self._type.upper() == 'HOURLY'):
                    for row in sorted(consumption_data, key=lambda x: x['startdate'], reverse=True):
                        if row['validateStatus'] == 'Y':
                            self._state_data_type = 'HOURLY'
                            self._attr_native_value = float(row['kwhtotal'])
                            self._attr_last_reset = datetime.datetime.strptime(row['startdate'], '%Y%m%d%H%M%S')
                            break

                if self._get_hourly:
                    for row in consumption_data:
                        if row['validateStatus'] == 'N':
                            continue

                        hourly.append({
                            'start': datetime.datetime.strptime(row['startdate'], '%Y%m%d%H%M%S'),
                            'kwh': float(row['kwhtotal']),
                        })

                self._hourly_task_last_fetch_time = datetime.datetime.now(self._timezone)

        if self._get_hourly:
            self._hourly = sorted(hourly, key=lambda x: x['start'], reverse=True)


    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def async_update(self) -> None:
        _LOGGER.debug(f"[SENSOR UPDATE] Starting update for {self._sensor_type}, access_token_expiry_time={self._access_token_expiry_time}")
        self._error = None
        try:
            await self._fetch_all()
        except FatalAuthError as e:
            # Credentials are gone and the integration is being unloaded; surface
            # the reason on the entity and stop. Home Assistant's own polling
            # (rate-limited by @Throttle) drives retries for recoverable errors.
            self._error = str(e)
            _LOGGER.error("%s: fatal auth error; integration stopped.", self._name)

    async def _fetch_all(self) -> None:
        if not self._access_token:
            _LOGGER.debug(f"[SENSOR UPDATE] No access token, skipping data fetch.")
            return

        if self._sensor_type == 'main':
            if not self._single_task_last_fetch_time:
                if not self._account_number or self._get_acct:
                    _LOGGER.debug(f"[SENSOR UPDATE] Fetching account detail.")
                    await self.main_get_account_detail()

            if not self._account_number:
                _LOGGER.debug("%s: no account number; skipping data fetch this cycle.", self._name)
                return

            if not self._daily_task_last_fetch_time or datetime.datetime.now(self._timezone) > self._daily_task_last_fetch_time + DAILY_TASK_INTERVAL:
                if self._get_bill:
                    _LOGGER.debug(f"[SENSOR UPDATE] Fetching bill.")
                    await self.main_get_bill()

                if self._get_estimation:
                    _LOGGER.debug(f"[SENSOR UPDATE] Fetching estimation.")
                    await self.main_get_estimation()

                if self._get_bimonthly or self._type == '' or self._type.upper() == 'BIMONTHLY':
                    _LOGGER.debug(f"[SENSOR UPDATE] Fetching bimonthly.")
                    await self.main_get_bimonthly()

                if self._get_daily or self._type == '' or self._type.upper() == 'DAILY':
                    _LOGGER.debug(f"[SENSOR UPDATE] Fetching daily.")
                    await self.main_get_daily()

            if not self._hourly_task_last_fetch_time or datetime.datetime.now(self._timezone) > self._hourly_task_last_fetch_time + HOURLY_TASK_INTERVAL:
                if self._get_hourly or self._type == '' or self._type.upper() == 'HOURLY':
                    _LOGGER.debug(f"[SENSOR UPDATE] Fetching hourly.")
                    await self.main_get_hourly()

        elif self._sensor_type == 'renewable_energy':
            if not self._single_task_last_fetch_time:
                if not self._account_number:
                    _LOGGER.debug(f"[SENSOR UPDATE] Fetching renewable account detail.")
                    await self.main_get_account_detail()

            if not self._account_number:
                _LOGGER.debug("%s: no account number; skipping data fetch this cycle.", self._name)
                return

            if not self._daily_task_last_fetch_time or datetime.datetime.now(self._timezone) > self._daily_task_last_fetch_time + DAILY_TASK_INTERVAL:
                if self._get_bill or self._type == '' or self._type.upper() == 'BIMONTHLY':
                    _LOGGER.debug(f"[SENSOR UPDATE] Fetching renewable bimonthly.")
                    await self.renewable_get_bimonthly()

                if self._get_daily or self._type == '' or self._type.upper() == 'DAILY':
                    _LOGGER.debug(f"[SENSOR UPDATE] Fetching renewable daily.")
                    await self.renewable_get_daily()

            if not self._hourly_task_last_fetch_time or datetime.datetime.now(self._timezone) > self._hourly_task_last_fetch_time + HOURLY_TASK_INTERVAL:
                if self._get_hourly or self._type == '' or self._type.upper() == 'HOURLY':
                    _LOGGER.debug(f"[SENSOR UPDATE] Fetching renewable hourly.")
                    await self.renewable_get_hourly()

        if self._type == '' and self._state_data_type is not None:
            self._type = self._state_data_type
