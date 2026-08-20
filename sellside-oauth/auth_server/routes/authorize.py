"""Endpoint /authorize, login delegado y pantalla de consentimiento.

Regla que ordena todo el archivo: **antes** de validar el `redirect_uri` los
errores se muestran en pantalla; **después** se devuelven redirigiendo al
cliente. Redirigir a una URI no validada es exactamente el agujero que el
registro exacto intenta cerrar.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..config import canonical_resource_uri
from ..context import SCOPE_DESCRIPTIONS, ctx
from ..identity import DevIdentityProvider, IdentityError, new_nonce
from ..models import AuthorizationCode, AuthorizationRequest, hash_secret, now, random_token
from ..pkce import is_valid_challenge
from ..sessions import COOKIE_NAME, SESSION_TTL
from ..templates import consent_page, dev_login_page, error_page

router = APIRouter()


def _error_page(error: str, description: str, status: int = 400) -> HTMLResponse:
    return HTMLResponse(error_page(error, description), status_code=status)


def _redirect_error(
    redirect_uri: str, error: str, description: str, state: str | None, issuer: str
) -> RedirectResponse:
    params = {"error": error, "error_description": description, "iss": issuer}
    if state is not None:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{separator}{urlencode(params)}", status_code=302)


@router.get("/authorize")
async def authorize(request: Request) -> Response:
    context = ctx(request)
    settings = context.settings
    params = request.query_params

    client_id = params.get("client_id")
    if not client_id:
        return _error_page("invalid_request", "Falta client_id")
    client = await context.storage.get_client(client_id)
    if client is None:
        return _error_page("invalid_client", "client_id desconocido. Vuelve a registrar el conector.")

    redirect_uri = params.get("redirect_uri")
    if redirect_uri is None:
        if len(client.redirect_uris) != 1:
            return _error_page(
                "invalid_request",
                "redirect_uri es obligatorio cuando el cliente registró más de uno",
            )
        redirect_uri = client.redirect_uris[0]
    elif not client.allows_redirect_uri(redirect_uri):
        # No se redirige: la URI no es de confianza.
        return _error_page(
            "invalid_request",
            "redirect_uri no coincide exactamente con ninguna registrada",
        )

    state = params.get("state")
    issuer = settings.issuer

    if params.get("response_type") != "code":
        return _redirect_error(
            redirect_uri, "unsupported_response_type",
            "Solo se soporta response_type=code", state, issuer,
        )

    code_challenge = params.get("code_challenge")
    if not code_challenge:
        return _redirect_error(
            redirect_uri, "invalid_request", "PKCE es obligatorio: falta code_challenge",
            state, issuer,
        )
    if params.get("code_challenge_method") != "S256":
        return _redirect_error(
            redirect_uri, "invalid_request",
            "code_challenge_method debe ser S256", state, issuer,
        )
    if not is_valid_challenge(code_challenge):
        return _redirect_error(
            redirect_uri, "invalid_request", "code_challenge mal formado", state, issuer,
        )

    resources = params.getlist("resource")
    if len(resources) > 1:
        return _redirect_error(
            redirect_uri, "invalid_target",
            "Se admite un solo `resource` por autorización: el token lleva una sola audiencia",
            state, issuer,
        )
    if resources:
        try:
            resource = canonical_resource_uri(resources[0])
        except ValueError as exc:
            return _redirect_error(redirect_uri, "invalid_target", str(exc), state, issuer)
    elif settings.require_resource_param:
        return _redirect_error(
            redirect_uri, "invalid_target",
            "Falta el parámetro `resource` (RFC 8707) con la URI canónica del MCP",
            state, issuer,
        )
    else:
        resource = settings.protected_resources[0]

    if not settings.is_known_resource(resource):
        return _redirect_error(
            redirect_uri, "invalid_target",
            f"Este servidor no emite tokens para {resource}", state, issuer,
        )

    requested_scope = params.get("scope") or client.scope or " ".join(settings.scopes_supported)
    scopes = requested_scope.split()
    unknown = set(scopes) - set(settings.scopes_supported)
    if unknown:
        return _redirect_error(
            redirect_uri, "invalid_scope", f"scopes desconocidos: {sorted(unknown)}",
            state, issuer,
        )
    allowed_for_client = set((client.scope or " ".join(settings.scopes_supported)).split())
    if not set(scopes) <= allowed_for_client:
        return _redirect_error(
            redirect_uri, "invalid_scope",
            "El cliente pide más scopes de los que registró", state, issuer,
        )

    auth_request = AuthorizationRequest(
        request_id=random_token(24),
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        resource=resource,
        scope=" ".join(scopes),
        state=state,
        expires_at=now() + settings.auth_request_ttl,
    )

    session = context.sessions.read(request.cookies.get(COOKIE_NAME))
    if session:
        auth_request.subject = session["sub"]
        auth_request.subject_email = session.get("email", "")
        await context.storage.save_auth_request(auth_request)
        return await _render_consent(context, auth_request, session["sid"])

    auth_request.login_nonce = new_nonce()
    await context.storage.save_auth_request(auth_request)
    return RedirectResponse(
        context.idp.authorization_url(auth_request.request_id, auth_request.login_nonce),
        status_code=302,
    )


async def _render_consent(context, auth_request: AuthorizationRequest, sid: str) -> HTMLResponse:
    client = await context.storage.get_client(auth_request.client_id)
    return HTMLResponse(
        consent_page(
            client_name=client.client_name if client else "",
            client_id=auth_request.client_id,
            resource=auth_request.resource,
            scopes=auth_request.scope.split(),
            scope_descriptions=SCOPE_DESCRIPTIONS,
            user_email=auth_request.subject_email or "",
            request_id=auth_request.request_id,
            csrf_token=context.sessions.csrf_token(sid, auth_request.request_id),
        )
    )


@router.get("/callback/google")
async def google_callback(request: Request) -> Response:
    """Vuelta del IdP upstream. Aquí se autentica a la persona, no al cliente."""

    context = ctx(request)
    if context.dev_login_enabled:
        return _error_page("invalid_request", "El proveedor Google no está activo", 404)

    request_id = request.query_params.get("state")
    code = request.query_params.get("code")
    if not request_id or not code:
        return _error_page("invalid_request", "Respuesta de Google incompleta")

    auth_request = await context.storage.get_auth_request(request_id)
    if auth_request is None:
        return _error_page("invalid_request", "La autorización expiró. Vuelve a intentarlo.")

    try:
        identity = await context.idp.complete(code, auth_request.login_nonce or "")
    except IdentityError as exc:
        return _error_page("access_denied", str(exc), 403)

    auth_request.subject = identity.subject
    auth_request.subject_email = identity.email
    await context.storage.save_auth_request(auth_request)

    cookie = context.sessions.issue(identity)
    response = RedirectResponse(f"/authorize/continue?request_id={request_id}", status_code=302)
    _set_session_cookie(response, cookie, secure=context.settings.issuer.startswith("https"))
    return response


@router.get("/dev/login")
async def dev_login_form(request: Request) -> Response:
    context = ctx(request)
    if not context.dev_login_enabled:
        return _error_page("invalid_request", "Login de desarrollo deshabilitado", 404)
    return HTMLResponse(
        dev_login_page(request.query_params.get("state", ""), request.query_params.get("nonce", ""))
    )


@router.post("/dev/login")
async def dev_login_submit(request: Request) -> Response:
    context = ctx(request)
    if not context.dev_login_enabled:
        return _error_page("invalid_request", "Login de desarrollo deshabilitado", 404)

    form = await request.form()
    request_id = str(form.get("state") or "")
    auth_request = await context.storage.get_auth_request(request_id)
    if auth_request is None:
        return _error_page("invalid_request", "La autorización expiró. Vuelve a intentarlo.")

    assert isinstance(context.idp, DevIdentityProvider)
    try:
        identity = context.idp.identity_for(str(form.get("email") or ""))
    except IdentityError as exc:
        return HTMLResponse(
            dev_login_page(request_id, str(form.get("nonce") or ""), error=str(exc)),
            status_code=400,
        )

    auth_request.subject = identity.subject
    auth_request.subject_email = identity.email
    await context.storage.save_auth_request(auth_request)

    response = RedirectResponse(f"/authorize/continue?request_id={request_id}", status_code=302)
    _set_session_cookie(
        response, context.sessions.issue(identity),
        secure=context.settings.issuer.startswith("https"),
    )
    return response


@router.get("/authorize/continue")
async def authorize_continue(request: Request) -> Response:
    context = ctx(request)
    session = context.sessions.read(request.cookies.get(COOKIE_NAME))
    if not session:
        return _error_page("access_denied", "Sesión no válida o expirada", 401)

    request_id = request.query_params.get("request_id", "")
    auth_request = await context.storage.get_auth_request(request_id)
    if auth_request is None:
        return _error_page("invalid_request", "La autorización expiró. Vuelve a intentarlo.")
    if auth_request.subject != session["sub"]:
        return _error_page("access_denied", "La sesión no corresponde a esta autorización", 403)

    return await _render_consent(context, auth_request, session["sid"])


@router.post("/consent")
async def consent(request: Request) -> Response:
    context = ctx(request)
    settings = context.settings
    session = context.sessions.read(request.cookies.get(COOKIE_NAME))
    if not session:
        return _error_page("access_denied", "Sesión no válida o expirada", 401)

    form = await request.form()
    request_id = str(form.get("request_id") or "")
    auth_request = await context.storage.get_auth_request(request_id)
    if auth_request is None:
        return _error_page("invalid_request", "La autorización expiró. Vuelve a intentarlo.")
    if auth_request.subject != session["sub"]:
        return _error_page("access_denied", "La sesión no corresponde a esta autorización", 403)
    if not context.sessions.check_csrf(session["sid"], request_id, str(form.get("csrf_token") or "")):
        return _error_page("invalid_request", "Token CSRF inválido", 403)

    await context.storage.delete_auth_request(request_id)

    if str(form.get("decision")) != "allow":
        return _redirect_error(
            auth_request.redirect_uri, "access_denied",
            "El usuario rechazó la solicitud", auth_request.state, settings.issuer,
        )

    code = random_token(32)
    await context.storage.save_authorization_code(
        AuthorizationCode(
            code_hash=hash_secret(code),
            client_id=auth_request.client_id,
            redirect_uri=auth_request.redirect_uri,
            code_challenge=auth_request.code_challenge,
            resource=auth_request.resource,
            scope=auth_request.scope,
            subject=auth_request.subject or "",
            subject_email=auth_request.subject_email or "",
            expires_at=now() + settings.authorization_code_ttl,
            session_id=session["sid"],
        )
    )

    params = {"code": code, "iss": settings.issuer}
    if auth_request.state is not None:
        params["state"] = auth_request.state
    separator = "&" if "?" in auth_request.redirect_uri else "?"
    return RedirectResponse(
        f"{auth_request.redirect_uri}{separator}{urlencode(params)}",
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )


def _set_session_cookie(response: Response, value: str, *, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        value,
        max_age=SESSION_TTL,
        httponly=True,
        secure=secure,
        # `lax` deja pasar la cookie en la vuelta por GET desde Google y desde
        # el POST del propio formulario (mismo sitio).
        samesite="lax",
        path="/",
    )
