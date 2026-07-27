"""Validación de redirect URIs.

Dos reglas distintas y ambas necesarias:

* En **registro** se filtra la forma (https o loopback, sin fragmento) y el host
  contra la lista permitida.
* En **/authorize** la comparación contra lo registrado es de string exacto.
  Nada de prefijos ni de "empieza con": ahí es donde se cuelan los robos de
  código de autorización.
"""

from __future__ import annotations

from urllib.parse import urlsplit

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class RedirectUriError(ValueError):
    pass


def validate_registration_redirect_uri(uri: str, allowed_hosts: tuple[str, ...]) -> str:
    parts = urlsplit(uri)
    if parts.fragment:
        raise RedirectUriError(f"redirect_uri no puede llevar fragmento: {uri}")
    if not parts.netloc or not parts.hostname:
        raise RedirectUriError(f"redirect_uri sin host: {uri}")

    host = parts.hostname.lower()
    is_loopback = host in LOOPBACK_HOSTS
    if parts.scheme == "http":
        if not is_loopback:
            raise RedirectUriError(f"http solo se acepta en loopback: {uri}")
    elif parts.scheme != "https":
        raise RedirectUriError(f"redirect_uri debe ser https o http de loopback: {uri}")

    if allowed_hosts and not _host_allowed(host, allowed_hosts):
        raise RedirectUriError(
            f"el host {host} no está en ALLOWED_REDIRECT_HOSTS"
        )
    return uri


def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    for allowed in allowed_hosts:
        allowed = allowed.lower().lstrip(".")
        if host == allowed or host.endswith(f".{allowed}"):
            return True
    return False
