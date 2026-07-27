# Prompt de revisión independiente

Para pasarle a otro modelo (Gemini CLI, Codex, o quien sea) y contrastar este
trabajo. Está escrito para provocar hallazgos concretos, no un resumen amable:
pide archivo y línea, escenario de fallo, y obliga a declarar lo que no se pudo
verificar en vez de asumirlo.

Desde Cloud Shell:

```bash
cd ~/CTOSellside/sellside-oauth
gemini -p "$(cat REVISION-EXTERNA.md | sed -n '/^---$/,$p')"
```

O abre `gemini` en modo interactivo y pega todo lo que sigue a la línea de
guiones.

---

Actúas como revisor de seguridad independiente. No escribiste este código y no
tienes que defenderlo.

## Contexto

En `~/CTOSellside/sellside-oauth` hay una implementación de OAuth 2.1 para
exponer servidores MCP como conectores personalizados de Claude, sobre Cloud Run
en el proyecto `odoo-serverless-ss-001`, región `southamerica-west1`.

Dos piezas:

- `auth_server/` — `sellside-auth`, el servidor de autorización. Un servicio
  compartido para cinco MCP.
- `mcp_resource_server/` — librería que cada MCP monta para validar tokens.

El MCP que se va a exponer (`odoo-mcp-sellside`) tiene herramientas de escritura
y borrado sobre Odoo (`odoo_write`, `odoo_unlink`), y el despliegue termina
dándole `roles/run.invoker` a `allUsers`. Es decir: a partir de ese momento la
única protección es este código.

Normativa aplicable: MCP Authorization spec 2025-06-18, OAuth 2.1 (draft),
RFC 9728 (protected resource metadata), RFC 8414 (AS metadata), RFC 7591
(registro dinámico), RFC 7636 (PKCE), RFC 8707 (resource indicators), RFC 9207
(`iss` en la respuesta), RFC 7009 (revocación).

## Lo que te pido

Revisa el código, no el diseño en abstracto. Para cada invariante de la lista,
responde una de tres cosas: **se cumple** (con archivo y línea que lo demuestre),
**no se cumple** (con el escenario concreto que lo rompe), o **no pude
verificarlo** (dilo, no lo asumas).

### Invariantes

1. Un token emitido para el MCP A es rechazado por el MCP B. La comparación de
   `aud` es exacta y ambos lados normalizan la URI igual.
2. `alg=none`, HS256 y cualquier algoritmo que no sea RS256 son rechazados
   antes de cualquier otra validación.
3. PKCE `S256` es obligatorio en `/authorize`; `plain` no existe en ningún
   camino de ejecución.
4. Los `redirect_uri` se comparan por string exacto contra lo registrado. No hay
   ninguna ruta que redirija a una URI no validada, ni siquiera en errores.
5. Un código de autorización solo se canjea una vez, y el segundo intento revoca
   lo que se emitió con el primero.
6. Un refresh token solo se usa una vez; reutilizarlo revoca toda la cadena de
   rotación, incluido el token vigente.
7. Un refresh no puede ampliar el scope original.
8. El token que manda Claude no se reenvía a Odoo por ningún camino. Busca
   activamente si el JWT crudo queda accesible en algún objeto, log o excepción.
9. Un cliente no puede canjear un código emitido para otro cliente.
10. `/authorize` rechaza un `resource` que este AS no sirve, y `/token` rechaza
    un `resource` distinto al de la autorización.
11. Los secretos (client_secret, refresh tokens) se guardan hasheados, no en
    claro, y no aparecen en logs.
12. El endpoint de consentimiento está protegido contra CSRF y contra que una
    sesión apruebe una autorización de otro usuario.

### Busca además, por tu cuenta

- Confusión de audiencia o de issuer que no cubran los invariantes de arriba.
- Condiciones de carrera en el consumo de códigos o refresh tokens: mira si el
  backend de Firestore es realmente atómico (`auth_server/storage/firestore.py`).
- Inyección en el HTML de las pantallas (`auth_server/templates.py`): el
  `client_name` lo escribe quien registre un cliente y el registro es abierto.
- Redirecciones abiertas, incluidas las que pasen por el login de Google.
- Fugas de información en mensajes de error que ayuden a enumerar clientes,
  usuarios o tokens.
- Fallos abiertos: cualquier `except` que deje pasar una petición que debió
  rechazarse.
- En `deploy/`: comandos que abran acceso antes de verificar, secretos que
  queden en el historial de revisiones de Cloud Run, o permisos IAM más amplios
  de lo necesario.
- Divergencias entre lo que dicen `README.md` y `SEGURIDAD.md` y lo que hace el
  código. Si un documento promete algo que el código no cumple, eso es un
  hallazgo.

### Reglas

- **No modifiques archivos.** Reporta, no arregles.
- **No ejecutes nada que cambie estado en GCP.** Ni despliegues, ni `gcloud`
  que escriba, ni `add-iam-policy-binding`. Leer está bien.
- Puedes correr las pruebas: `python3 -m venv /tmp/v && . /tmp/v/bin/activate &&
  pip install -q -e '.[dev]' pytest-asyncio && pytest -q`.
- Las pruebas existentes pasan. Que pasen no prueba que el código sea correcto:
  dime qué invariante **no** está cubierto por ninguna prueba.

### Formato de salida

Hallazgos ordenados por severidad. Para cada uno:

```
[CRÍTICO|ALTO|MEDIO|BAJO] título
archivo:línea
Escenario: qué tiene que hacer un atacante, paso a paso, para explotarlo.
Corrección: qué cambiar.
```

Lo que esté bien, una línea por invariante. Sin elogios ni resúmenes
ejecutivos.

Termina con dos preguntas respondidas:

1. ¿Abrirías `allUsers` sobre este MCP con este código tal como está? Sí o no, y
   qué te haría cambiar de opinión.
2. ¿Qué falta para producción que **no** esté ya listado en `SEGURIDAD.md`?
