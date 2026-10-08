import threading
import time


class RateLimiter:
    def __init__(self, rate=0):
        """rate en octets/seconde. 0 = illimité."""
        self._rate = rate
        self._lock = threading.Lock()
        self._next = time.monotonic()

    def set_rate(self, rate):
        with self._lock:
            self._rate = rate
            self._next = time.monotonic()

    def wait(self, n, stop=None):
        """Bloque le temps nécessaire pour envoyer n octets sans dépasser la limite."""
        with self._lock:
            if self._rate <= 0:
                return
            now = time.monotonic()
            start = max(self._next, now)
            self._next = start + n / self._rate
            delay = start - now
        if delay > 0:
            if stop is not None:
                stop.wait(delay)
            else:
                time.sleep(delay)