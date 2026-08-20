#!/usr/bin/env python3
"""Revocación operativa contra Firestore.

El endpoint /revoke sirve para que un cliente devuelva su propio token. Esto es
lo otro: cortar el acceso de una persona o de un conector desde fuera, sin
pedirle permiso a nadie.

    python tools/revocar.py listar --sujeto google:1234567890
    python tools/revocar.py sujeto google:1234567890
    python tools/revocar.py cliente c_AbCdEf...
    python tools/revocar.py familia f_...

Requiere credenciales con acceso a Firestore (`gcloud auth application-default
login`) y GOOGLE_CLOUD_PROJECT apuntando al proyecto correcto.

Lo que revoca son refresh tokens: el acceso deja de renovarse de inmediato. Un
access token ya emitido sigue siendo válido hasta que expira (15 minutos por
defecto). Si necesitas un corte instantáneo, además de esto:

    deploy/rollback-acceso.sh        # cierra el MCP a todo el mundo

o rota la llave de firma, que invalida todos los access tokens a la vez.
"""

from __future__ import annotations

import argparse
import os
import sys

from google.cloud import firestore

COLLECTION = "oauth_refresh_tokens"


def _client() -> firestore.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        sys.exit("Define GOOGLE_CLOUD_PROJECT antes de correr esto")
    return firestore.Client(project=project)


def _query(db: firestore.Client, campo: str, valor: str, solo_activos: bool = True):
    query = db.collection(COLLECTION).where(
        filter=firestore.FieldFilter(campo, "==", valor)
    )
    if solo_activos:
        query = query.where(filter=firestore.FieldFilter("consumed", "==", False))
    return query.stream()


def listar(campo: str, valor: str) -> None:
    db = _client()
    total = 0
    for snapshot in _query(db, campo, valor, solo_activos=False):
        data = snapshot.to_dict()
        estado = "revocado/usado" if data.get("consumed") else "ACTIVO"
        print(
            f"{estado:>14}  familia={data.get('family_id')}  cliente={data.get('client_id')}  "
            f"recurso={data.get('resource')}  scope={data.get('scope')}"
        )
        total += 1
    print(f"\n{total} token(s) encontrados para {campo}={valor}")


def revocar(campo: str, valor: str) -> None:
    db = _client()
    batch = db.batch()
    total = 0
    for snapshot in _query(db, campo, valor):
        batch.update(snapshot.reference, {"consumed": True})
        total += 1
        if total % 400 == 0:
            batch.commit()
            batch = db.batch()
    if total % 400 != 0:
        batch.commit()
    print(f"{total} refresh token(s) revocados para {campo}={valor}")
    if total:
        print(
            "Los access tokens ya emitidos siguen vivos hasta su expiración "
            "(ACCESS_TOKEN_TTL). Para cortar ya: deploy/rollback-acceso.sh"
        )


CAMPOS = {"sujeto": "subject", "cliente": "client_id", "familia": "family_id", "sesion": "session_id"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="accion", required=True)

    listar_cmd = sub.add_parser("listar", help="mostrar tokens sin revocarlos")
    for nombre in CAMPOS:
        listar_cmd.add_argument(f"--{nombre}")

    for nombre in CAMPOS:
        cmd = sub.add_parser(nombre, help=f"revocar por {nombre}")
        cmd.add_argument("valor")

    args = parser.parse_args()

    if args.accion == "listar":
        seleccion = [(CAMPOS[n], getattr(args, n)) for n in CAMPOS if getattr(args, n, None)]
        if len(seleccion) != 1:
            sys.exit("Indica exactamente un criterio: --sujeto, --cliente, --familia o --sesion")
        listar(*seleccion[0])
        return

    revocar(CAMPOS[args.accion], args.valor)


if __name__ == "__main__":
    main()
