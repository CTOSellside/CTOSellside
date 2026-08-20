"""Límite de tasa en memoria para el registro dinámico."""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RegistrationRateLimiter:
    """Ventana deslizante por IP, en memoria y por instancia.

    Con pocas instancias frena un bucle accidental o un script curioso. No
    pretende ser antiabuso serio: para eso está Cloud Armor.
    """

    def __init__(self, window: int = 3600) -> None:
        self._window = window
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, client_ip: str, limit: int) -> bool:
        """Registra un intento. Devuelve True si hay que rechazarlo."""

        bucket = self._buckets[client_ip]
        cutoff = time.time() - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(time.time())
        return False
