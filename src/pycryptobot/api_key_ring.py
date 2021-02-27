from keyring import get_password
from keyring import set_password

_CB_PRO_SANDBOX_USER = "sandbox' "
_COINBASE_PRO_LIVE_USER = "cbprolive"
_SERVICE_NAME = "pycryptobot"


class ApiKey:
    def __init__(self, api_key_name):
        self.name = api_key_name

    @property
    def is_sandbox(self):
        return self.name == _CB_PRO_SANDBOX_USER

    @property
    def key(self):
        return _get_pw(self._key_user)

    def replace_stored_key(self, new_value):
        _set_pw(self._key_user, new_value)

    @property
    def secret(self):
        return _get_pw(self._secret_user)

    def replace_stored_secret(self, new_value):
        _set_pw(self._secret_user, new_value)

    @property
    def passphrase(self):
        return _get_pw(self._passphrase_user)

    def replace_stored_passphrase(self, new_value):
        _set_pw(self._passphrase_user, new_value)

    @property
    def _key_user(self):
        return f"{self.name}-key"

    @property
    def _secret_user(self):
        return f"{self.name}-secret"

    @property
    def _passphrase_user(self):
        return f"{self.name}-passphrase"


def _get_pw(user):
    pw = get_password(_SERVICE_NAME, user)
    if not pw:
        raise Exception("Missing key.")
    return pw


def _set_pw(user, new_value):
    set_password(_SERVICE_NAME, user, new_value)
    if not get_password(_SERVICE_NAME, user):
        raise Exception("Failure to set keys.")