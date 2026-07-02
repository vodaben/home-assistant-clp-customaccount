CONF_DOMAIN = 'clphk'

CONF_RETRY_DELAY = 'retry_delay'

# HTTP status / CLP error-code classification for token handling.
# Only genuine auth failures clear stored tokens; transient statuses never do.
AUTH_FAILURE_STATUSES = (401, 403)
TRANSIENT_STATUSES = (408, 429)
TOKEN_EXPIRED_CODES = (906, 100001)  # 906: access token expired; 100001: LR access_token error


def is_auth_failure(status, error_code):
    """True when a 4xx response means the credentials are unusable (wipe + stop).

    A bare 401/403 is auth denial. TOKEN_EXPIRED_CODES only reach the wipe path
    after a refresh has already been attempted (or when no refresh token exists),
    i.e. the token is dead and cannot be recovered.
    """
    return status in AUTH_FAILURE_STATUSES or error_code in TOKEN_EXPIRED_CODES


def is_transient(status):
    """True for 4xx statuses that are temporary and must not clear tokens."""
    return status in TRANSIENT_STATUSES


def parse_refresh_tokens(response_json):
    """Extract (access_token, refresh_token, expires_in) from a refresh response.

    Returns None when the body is unusable (not a dict, no "data" dict, or the
    access/refresh token is missing/empty). A well-formed refresh that lacks a
    token is unrecoverable, so the caller treats None as a fatal auth failure
    rather than retrying with a possibly-consumed refresh token. expires_in is
    optional (not used for control flow) and returned as-is, defaulting to None.
    """
    if not isinstance(response_json, dict):
        return None
    data = response_json.get("data")
    if not isinstance(data, dict):
        return None
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access or not refresh:
        return None
    return access, refresh, data.get("expires_in")

CONF_GET_ACCT = 'get_account'
CONF_GET_BILL = 'get_bill'
CONF_GET_ESTIMATION = 'get_estimation'
CONF_GET_BIMONTHLY = 'get_bimonthly'
CONF_GET_DAILY = 'get_daily'
CONF_GET_HOURLY = 'get_hourly'
CONF_GET_HOURLY_DAYS = 'get_hourly_days'

CONF_RES_ENABLE = 'renewable_energy_sensor_enable'
CONF_RES_NAME = 'renewable_energy_sensor_name'
CONF_RES_TYPE = 'renewable_energy_sensor_type'
CONF_RES_GET_BILL = 'renewable_energy_sensor_get_bill'
CONF_RES_GET_DAILY = 'renewable_energy_sensor_get_daily'
CONF_RES_GET_HOURLY = 'renewable_energy_sensor_get_hourly'
CONF_RES_GET_HOURLY_DAYS = 'renewable_energy_sensor_get_hourly_days'

CONF_CLP_PUBLIC_KEY = '''
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA29ARH6tyDKbYAeBZ/sTOaxGP4T7+E4TR54CXo++3Oti5urT9DWMpgQREFxNO3fcP+phW/IeCwlcgdoOGfO/gHKJ7Q+ia8GDl434FCVATxEwxtUxSxY7C/nm82I7xpdCptzw4BLxWx8O4dQ4H0VzbqCWJUSTaj+gO9cedPnN8wk5o19mr6/o+g4kKzR1x0m7+2q6PUEzpyaLTcb9jCcTLh9EJSGxIJ+lUzAMZaM086qSNAe/FFMV9VpUXiENSBeqmWmS9/lyPsi0pWwfzF9oUnalqNIKOJjDf6vn1UtYz/RJceccjbQl3RoQWYdeuTO90ylwJDRcj8ACJ5tJi+EqRmQIDAQAB
-----END PUBLIC KEY-----
'''