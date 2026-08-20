"""Punto de entrada para Cloud Run: `uvicorn main:app`."""

from auth_server import create_app

app = create_app()
