"""Las tres pantallas que ve una persona: consentimiento, error y login de dev.

HTML plano, sin dependencias de plantillas. Todo lo que viene del cliente pasa
por `escape()` antes de entrar al documento: los valores de `client_name` los
escribe quien registre un cliente, y el registro es abierto.
"""

from __future__ import annotations

from html import escape

_STYLE = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0;
       display: flex; min-height: 100vh; align-items: center; justify-content: center;
       background: #f5f5f4; color: #1c1917; }
@media (prefers-color-scheme: dark) { body { background: #1c1917; color: #f5f5f4; } }
.card { max-width: 30rem; width: calc(100% - 2rem); padding: 2rem; border-radius: 0.75rem;
        background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.12); }
@media (prefers-color-scheme: dark) { .card { background: #292524; } }
h1 { font-size: 1.25rem; margin: 0 0 1rem; }
dl { margin: 1.5rem 0; display: grid; grid-template-columns: auto 1fr; gap: .5rem 1rem; }
dt { font-weight: 600; opacity: .7; } dd { margin: 0; word-break: break-all; }
ul { margin: .5rem 0 0; padding-left: 1.25rem; }
.actions { display: flex; gap: .75rem; margin-top: 1.5rem; }
button { flex: 1; padding: .65rem 1rem; border-radius: .5rem; border: 0; font-size: 1rem;
         cursor: pointer; }
button.primary { background: #0f766e; color: #fff; }
button.secondary { background: transparent; border: 1px solid currentColor; color: inherit; }
input { width: 100%; padding: .6rem; border-radius: .5rem; border: 1px solid #a8a29e;
        background: transparent; color: inherit; font-size: 1rem; box-sizing: border-box; }
.error { color: #b91c1c; } code { font-size: .85em; }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main class='card'>{body}</main></body></html>"
    )


def consent_page(
    *,
    client_name: str,
    client_id: str,
    resource: str,
    scopes: list[str],
    scope_descriptions: dict[str, str],
    user_email: str,
    request_id: str,
    csrf_token: str,
) -> str:
    items = "".join(
        f"<li><strong>{escape(scope)}</strong> — "
        f"{escape(scope_descriptions.get(scope, 'permiso solicitado'))}</li>"
        for scope in scopes
    )
    body = f"""
      <h1>Autorizar acceso</h1>
      <p><strong>{escape(client_name or client_id)}</strong> pide acceder al servidor MCP
      en nombre de {escape(user_email)}.</p>
      <dl>
        <dt>Recurso</dt><dd><code>{escape(resource)}</code></dd>
        <dt>client_id</dt><dd><code>{escape(client_id)}</code></dd>
      </dl>
      <p>Permisos solicitados:</p>
      <ul>{items}</ul>
      <form method="post" action="/consent">
        <input type="hidden" name="request_id" value="{escape(request_id)}">
        <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
        <div class="actions">
          <button class="secondary" type="submit" name="decision" value="deny">Rechazar</button>
          <button class="primary" type="submit" name="decision" value="allow">Autorizar</button>
        </div>
      </form>
    """
    return _page("Autorizar acceso · sellside-auth", body)


def error_page(error: str, description: str) -> str:
    body = f"""
      <h1 class="error">No se pudo continuar</h1>
      <p><code>{escape(error)}</code></p>
      <p>{escape(description)}</p>
      <p>Si el problema persiste, elimina el conector en Claude y vuelve a añadirlo.</p>
    """
    return _page("Error · sellside-auth", body)


def dev_login_page(state: str, nonce: str, error: str | None = None) -> str:
    warning = (
        f"<p class='error'>{escape(error)}</p>" if error else ""
    )
    body = f"""
      <h1>Login de desarrollo</h1>
      <p class="error">Este proveedor de identidad no verifica nada. Solo para local y pruebas.</p>
      {warning}
      <form method="post" action="/dev/login">
        <input type="hidden" name="state" value="{escape(state)}">
        <input type="hidden" name="nonce" value="{escape(nonce)}">
        <label for="email">Email</label>
        <input id="email" name="email" type="email" required placeholder="tu@sellside.cl">
        <div class="actions"><button class="primary" type="submit">Entrar</button></div>
      </form>
    """
    return _page("Login · sellside-auth", body)
