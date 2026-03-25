from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class AesGcmEncryptor:
    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ValueError("AES-GCM key must be 16/24/32 bytes")
        self._key = key

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        aes = AESGCM(self._key)
        ciphertext = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("utf-8")

    def decrypt(self, token: str) -> str:
        blob = base64.b64decode(token)
        nonce, ciphertext = blob[:12], blob[12:]
        aes = AESGCM(self._key)
        plaintext = aes.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")

