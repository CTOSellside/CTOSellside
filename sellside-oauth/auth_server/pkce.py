"""PKCE (RFC 7636). Solo S256; `plain` está prohibido en OAuth 2.1."""

from __future__ import annotations

import base64
import hashlib
import re

_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")


def is_valid_challenge(challenge: str) -> bool:
    return bool(_CHALLENGE_RE.match(challenge))


def verify_s256(verifier: str, challenge: str) -> bool:
    """Compara BASE64URL(SHA256(verifier)) contra el challenge registrado."""

    if not _VERIFIER_RE.match(verifier or ""):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    # El challenge no es secreto, pero comparar en tiempo constante no cuesta nada.
    return _constant_time_equals(computed, challenge)


def _constant_time_equals(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode("ascii"), (right or "").encode("ascii"))
