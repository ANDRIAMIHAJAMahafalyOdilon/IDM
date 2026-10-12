"""
GET HTTP avec impersonation TLS Chrome quand le CDN bloque `requests` (JA3).
Vidzy (uN.vidzy.cc) répond 403 à urllib3/requests, 200 à curl/curl_cffi.
"""
import threading
from urllib.parse import urlparse

import requests

IMPERSONATE = "chrome120"
_WAF_HOST_PARTS = ("vidzy.cc", "vidzy.org", "vidzy.live", "vidzy.lol")

_tls = threading.local()
_cffi_mod = None
_cffi_tried = False


def needs_impersonate(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(part in host for part in _WAF_HOST_PARTS)


def _curl_cffi():
    global _cffi_mod, _cffi_tried
    if _cffi_tried:
        return _cffi_mod
    _cffi_tried = True
    try:
        from curl_cffi import requests as creq
        _cffi_mod = creq
    except ImportError:
        _cffi_mod = None
    return _cffi_mod


def _cffi_session():
    creq = _curl_cffi()
    if creq is None:
        return None
    sess = getattr(_tls, "cffi_session", None)
    if sess is None:
        sess = creq.Session(impersonate=IMPERSONATE)
        _tls.cffi_session = sess
    return sess


def http_get(url, headers=None, timeout=15, stream=False, **kwargs):
    """GET : curl_cffi (Chrome TLS) pour Vidzy, sinon requests. Retry 403 via cffi."""
    headers = dict(headers or {})
    use_cffi = needs_impersonate(url)
    sess = _cffi_session() if use_cffi else None
    if sess is not None:
        return sess.get(url, headers=headers, timeout=timeout, stream=stream, **kwargs)

    r = requests.get(url, headers=headers, timeout=timeout, stream=stream, **kwargs)
    if r.status_code == 403:
        sess = _cffi_session()
        if sess is not None:
            try:
                r.close()
            except Exception:
                pass
            return sess.get(url, headers=headers, timeout=timeout, stream=stream, **kwargs)
    return r
