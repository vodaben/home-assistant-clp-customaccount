# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant custom integration (`custom_components/clphk`, HACS-distributable) that polls CLP (HK)'s
consumer API for electricity usage/billing data and exposes it as sensor entities. There is no build step —
it's plain Python loaded by Home Assistant at runtime.

## Commands

- Run the standalone unit tests (pure-Python, no Home Assistant dependency):
  ```
  python tests/test_auth_classification.py
  ```
  This file has no test framework — it's a manual runner. To run a single test, import and call the
  function directly, e.g. `python -c "import sys; sys.path.insert(0,'tests'); from test_auth_classification import test_safe_float; test_safe_float()"`,
  or just add a temporary `print`/filter in the `__main__` block.
- There is no lint config, CI workflow, or package manager config in this repo — do not invent build/lint
  commands.
- To manually exercise changes, this integration must run inside a real Home Assistant instance (add
  `custom_components/clphk` to a HA config dir, or install via HACS custom repository as described in
  README.md).

## Architecture

### Token lifecycle is the core complexity

The integration authenticates with a CLP `access_token` + `refresh_token` pair (opaque UUIDs, base64-wrapped
by CLP's frontend). There is no username/password login flow in this codebase — tokens must be lifted from a
browser session (see README "Get tokens from Chrome") and pasted into the config flow.

- `config_flow.py` normalizes/validates pasted tokens (`_normalize_token`, `_extract_allowed_b64_token`)
  accepting three input shapes (JSON object with `data`, JSON string, or raw base64) and rejects anything
  else (e.g. `Bearer ...` headers). It live-validates the access token against the CLP API before accepting
  it, classifying failures via `_classify_access_token_error` (expired / invalid / Akamai-blocked / etc.)
  into user-facing error keys (see `strings.json`).
- Tokens live in `hass.data[DOMAIN]` (shared dict + `asyncio.Lock` under `"token_lock"`), not on the sensor
  instances, because two `CLPSensor` entities (main + renewable-energy) can both need to refresh the same
  rotating refresh token concurrently. `CLPSensor._access_token` / `_refresh_token` are property
  getters/setters proxying into that shared dict — read `sensor.py`'s `_refresh_access_token` docstring
  before touching refresh logic; it explains why the lock re-checks `stale_access_token` after acquiring.
- `const.py`'s `is_auth_failure` / `is_transient` encode which HTTP status + CLP error code combinations mean
  "credentials are dead, wipe and stop" vs. "temporary, leave tokens alone and let HA's polling retry". This
  distinction is the single most load-bearing piece of logic in the integration — a past bug (issue "I1")
  wiped tokens on rate-limiting. `tests/test_auth_classification.py` exists specifically to pin this
  behavior; extend it rather than removing coverage when touching `const.py`.
- On unrecoverable auth failure, `_handle_auth_failure` (in `sensor.py`) clears tokens from the config entry,
  raises a Home Assistant persistent notification, and unloads the integration entirely — it does not just
  mark the entity unavailable. Any new fatal-error path should follow this same pattern (`FatalAuthError` ->
  `_handle_auth_failure` -> unload), not invent a new one.

### Sensor update flow

- `sensor.py` defines a single `CLPSensor` class instantiated twice per config entry: `sensor_type='main'`
  and `sensor_type='renewable_energy'` (the latter only if `renewable_energy_sensor_enable` is set). Both
  share the same token state and account number but hit different CLP API endpoints
  (`main_get_*` vs `renewable_get_*` methods).
- `async_update` is `@Throttle`d and calls `_fetch_all`, which fetches account details once, then bill/
  estimation/bimonthly/daily data at most every `DAILY_TASK_INTERVAL` (12h) and hourly data at most every
  `HOURLY_TASK_INTERVAL` (30min) — independent of the HA polling interval. There is no independent scheduler;
  retries for failed fetches happen naturally on the next HA poll because `_*_last_fetch_time` is only
  updated on success.
- Each fetch method is wrapped in `@handle_errors`, which swallows non-fatal exceptions (records them on
  `self._error`, logs, returns `None`) so one failing endpoint doesn't prevent sibling fetches in the same
  cycle — but re-raises `FatalAuthError` so `async_update` can stop the cycle.
- The primary sensor `state` reflects whichever data type (`BIMONTHLY`/`DAILY`/`HOURLY`) is configured via
  `type` (or auto-picked if empty); all fetched data is additionally exposed as `extra_state_attributes`
  when the corresponding `get_*` config flag is on.
- All CLP API calls go through `curl_cffi.requests.AsyncSession` with `impersonate="chrome"` (not plain
  `aiohttp`/`requests`) to defeat Akamai bot-blocking on CLP's API — do not swap this for a different HTTP
  client without re-checking that Akamai still accepts it.
- `api_request` (in `sensor.py`) is the single chokepoint for outbound API calls: it merges headers, checks
  for token-expiry response codes (906, 100001) or an unparseable 403 to trigger one retried refresh, and
  routes genuine auth failures into `_handle_auth_failure`. New endpoints should be added as methods that
  call `self.api_request(...)`, not by making raw HTTP calls elsewhere.
- Logging deliberately never includes the raw response body or request query string on error paths (tokens/
  account PII may be in either) — only status, endpoint, and parsed error code. Preserve this when adding
  new error logging.

### Config entities

- `const.py` holds every `CONF_*` key shared between `config_flow.py` and `sensor.py` — add new options
  there, not as ad-hoc string literals.
- Legacy YAML platform setup (`async_setup_platform`) and config-entry setup (`async_setup_entry`) both funnel
  into the same `CLPSensor` construction path; `async_setup_entry` just merges `config_entry.data` +
  `config_entry.options` (options win) and calls the YAML entrypoint with that as `discovery_info`.
