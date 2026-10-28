"""Encryption for stored connection credentials."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretsUnavailableError(RuntimeError):
    """Raised when a credential must be stored or read but no key is configured."""


class SecretBox:
    def __init__(self, key: str | None) -> None:
        self._fernet = Fernet(key.encode()) if key else None

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> bytes:
        if self._fernet is None:
            raise SecretsUnavailableError(
                "Set DECISION_SQL_SECRET_KEY before saving connections with a password."
            )
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        if self._fernet is None:
            raise SecretsUnavailableError("DECISION_SQL_SECRET_KEY is not configured.")
        try:
            return self._fernet.decrypt(token).decode()
        except InvalidToken as error:
            raise SecretsUnavailableError(
                "Stored password cannot be decrypted with the configured key."
            ) from error
