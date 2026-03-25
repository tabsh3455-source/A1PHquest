from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

settings = get_settings()


class KmsProvider(Protocol):
    def encrypt(self, plaintext: str) -> str:
        ...

    def decrypt(self, ciphertext: str) -> str:
        ...


@dataclass(slots=True)
class LocalAesKmsProvider:
    """Local AES-GCM provider backed by AES_MASTER_KEY."""

    key_material: bytes

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        aes = AESGCM(self.key_material)
        cipher = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        payload = {"nonce": base64.b64encode(nonce).decode(), "cipher": base64.b64encode(cipher).decode()}
        return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        payload = json.loads(base64.b64decode(ciphertext).decode("utf-8"))
        nonce = base64.b64decode(payload["nonce"])
        cipher = base64.b64decode(payload["cipher"])
        aes = AESGCM(self.key_material)
        plain = aes.decrypt(nonce, cipher, None)
        return plain.decode("utf-8")


def build_kms_provider() -> KmsProvider:
    if settings.kms_mode.lower() != "local_aes":
        raise ValueError("Only KMS_MODE=local_aes is supported")

    key = settings.aes_master_key.encode("utf-8")
    if len(key) != 32:
        raise ValueError("AES_MASTER_KEY must be exactly 32 bytes for local_aes mode")
    return LocalAesKmsProvider(key_material=key)
