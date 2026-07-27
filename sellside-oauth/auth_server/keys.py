"""Llave de firma RSA y publicación del JWKS."""

from __future__ import annotations

import base64
import hashlib
import json
from functools import cached_property

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_to_b64u(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return _b64u(value.to_bytes(length, "big"))


class SigningKey:
    """Par RSA cargado desde PEM, con `kid` derivado del thumbprint (RFC 7638).

    Derivar el `kid` en vez de inventarlo permite rotar la llave sin coordinar
    identificadores: la nueva llave trae otro thumbprint y los resource servers
    la descubren solo al ver un `kid` desconocido en el header.
    """

    def __init__(self, pem: str) -> None:
        private = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        if not isinstance(private, rsa.RSAPrivateKey):
            raise ValueError("La llave de firma debe ser RSA")
        if private.key_size < 2048:
            raise ValueError("La llave RSA debe ser de al menos 2048 bits")
        self._private = private

    @property
    def private_key(self) -> rsa.RSAPrivateKey:
        return self._private

    @cached_property
    def public_jwk(self) -> dict[str, str]:
        numbers = self._private.public_key().public_numbers()
        jwk = {
            "kty": "RSA",
            "n": _int_to_b64u(numbers.n),
            "e": _int_to_b64u(numbers.e),
            "alg": "RS256",
            "use": "sig",
        }
        jwk["kid"] = self._thumbprint(jwk)
        return jwk

    @property
    def kid(self) -> str:
        return self.public_jwk["kid"]

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [self.public_jwk]}

    @staticmethod
    def _thumbprint(jwk: dict[str, str]) -> str:
        canonical = json.dumps(
            {"e": jwk["e"], "kty": jwk["kty"], "n": jwk["n"]},
            separators=(",", ":"),
            sort_keys=True,
        )
        return _b64u(hashlib.sha256(canonical.encode("ascii")).digest())


def generate_key_pem(bits: int = 2048) -> str:
    """Genera un PEM nuevo. Se usa en tests; en producción lo hace `openssl`."""

    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
