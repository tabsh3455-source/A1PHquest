from __future__ import annotations

import pyotp


class TOTPService:
    issuer_name = "A1phquest"

    def generate_secret(self) -> str:
        return pyotp.random_base32()

    def build_uri(self, secret: str, account_name: str) -> str:
        return pyotp.totp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=self.issuer_name)

    def verify(self, secret: str, code: str) -> bool:
        return pyotp.TOTP(secret).verify(code, valid_window=1)

