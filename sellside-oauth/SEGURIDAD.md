# Checklist de seguridad antes de abrir `allUsers`

La sección 9 del runbook, con el estado de cada punto en este código y lo que
queda del lado de la operación.

## Cubierto por el código (con prueba que lo respalda)

- [x] **El MCP responde 401 sin token**
      `mcp_resource_server/middleware.py` · `test_sin_token_devuelve_401_con_www_authenticate`
- [x] **El `WWW-Authenticate` incluye `resource_metadata`**
      Sin ese parámetro el cliente no sabe dónde autenticarse. Mismo test.
- [x] **El MCP rechaza un token con `aud` de otro servicio**
      `verifier.py` compara contra la URI canónica · `test_token_de_otro_recurso_se_rechaza`
- [x] **El MCP rechaza `alg=none` y HS256**
      Lista blanca de algoritmos · `test_alg_none_se_rechaza`, `test_hs256_con_la_clave_publica_se_rechaza`
- [x] **El MCP no reenvía el token de Claude hacia Odoo**
      `AccessToken` no guarda el JWT; `OdooCredentials` solo se construye desde el
      entorno · `test_el_token_crudo_no_queda_disponible_para_reenviarlo`
- [x] **Redirect URIs validadas por coincidencia exacta**
      `Client.allows_redirect_uri` · `test_redirect_uri_no_registrada_no_redirige`
- [x] **PKCE S256 obligatorio, `plain` inexistente**
      `test_authorize_exige_pkce`, `test_authorize_rechaza_plain`
- [x] **Access tokens de vida corta y refresh rotativos**
      15 min por defecto; cada refresh emite uno nuevo · `test_refresh_rota_y_detecta_reuso`
- [x] **Reuso de código o de refresh revoca la cadena completa**
      `test_codigo_es_de_un_solo_uso_y_el_replay_revoca`
- [x] **Un refresh no puede ampliar scope**
      `test_refresh_no_puede_ampliar_scope`
- [x] **Un código no se canjea con otro cliente**
      `test_codigo_no_se_canjea_con_otro_cliente`
- [x] **Logs de auditoría: qué cliente, qué herramienta, qué sujeto**
      El middleware registra `sub`, `client_id` y scopes; el ejemplo añade la
      herramienta y el `jti` en cada `tools/call`.
- [x] **Procedimiento de revocación probado**
      `/revoke` con test (`test_revocacion_de_refresh_token`,
      `test_revocar_con_access_token_corta_la_sesion`) y `tools/revocar.py` para
      cortar desde fuera.

## Queda del lado de la operación

- [ ] **El usuario de Odoo detrás del MCP tiene ACLs de solo lectura**
      (o escritura acotada y consciente). Es el último control antes de los
      datos: si esa cuenta es administradora, el scope `odoo:write` equivale a
      acceso total. El código no puede verificarlo por ti.
- [ ] **`ALLOWED_EMAIL_DOMAINS` configurado** (ej. `sellside.cl`). Vacío
      significa que cualquier cuenta de Google que complete el flujo obtiene un
      token.
- [ ] **La redirect URI `${ISSUER}/callback/google`** autorizada en el cliente
      OAuth de Google, y ninguna otra de más.
- [ ] **Verificación previa ejecutada**: `USE_IAM_TOKEN=1 deploy/04-verificar.sh`
      en verde. `deploy/05-abrir-acceso.sh` la corre solo, pero conviene mirarla.
- [ ] **Si vas a conectar bases de clientes: el DPA (Ley 21.719) actualizado
      antes, no después.**

## Lo que este diseño *no* cubre

Vale la pena tenerlo escrito para que nadie lo descubra en el peor momento.

**Un access token robado sirve hasta que expira.** Son JWT: el MCP los valida
con la llave pública y no consulta a nadie. Revocar corta la renovación, no el
token vivo. Ventana máxima: `ACCESS_TOKEN_TTL`, 15 minutos por defecto. Para un
corte inmediato hay dos palancas: `deploy/rollback-acceso.sh` (cierra el MCP a
todo el mundo) o rotar la llave de firma (invalida todos los access tokens a la
vez; los refresh sobreviven).

**El registro dinámico es público.** Lo exige el flujo de conectores de Claude.
Los frenos son la lista de hosts permitidos para redirect URIs y un límite por
IP y por instancia. Un atacante puede registrar clientes, pero no obtiene nada
sin que una persona autorizada complete el login y acepte el consentimiento.

**El límite de registro es por instancia.** Con `--max-instances=3` el techo
real es tres veces `REGISTRATION_RATE_LIMIT`. Si eso importa, la herramienta
adecuada es Cloud Armor, no este contador.

**El consentimiento no se recuerda entre conectores.** Cada `client_id` nuevo
vuelve a pedir autorización. Es deliberado: el consentimiento silencioso es
cómodo hasta el día en que autoriza a un cliente que no reconoces.

**Firestore guarda el hash, no el token.** Ni los refresh tokens ni los
`client_secret` se pueden leer de la base. Lo que sí queda en claro es el email
del sujeto autorizado, que es dato personal: cuenta para el DPA.
