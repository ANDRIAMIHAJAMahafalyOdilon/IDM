"""
Extracteurs de vidéos pour FlashDL.
Prend une URL de page web et retourne les infos nécessaires au téléchargement.

Hiérarchie d'extraction :
  1. Extracteurs de "sites indexeurs" (AnimeSama, FrenchStream, YouTube)
     └─> extraient des iframes/URLs vers des lecteurs externes
  2. Extracteurs de "lecteurs externes" (voe, dood, uqload, streamtape, mixdrop...)
     └─> extraient l'URL finale (.m3u8 HLS ou .mp4 direct)
"""
import base64
import codecs
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import re
import json
import unicodedata
from dataclasses import dataclass, field
from typing import Optional, List, Callable
from urllib.parse import urlparse, parse_qs, unquote, urljoin

import requests
from bs4 import BeautifulSoup

from .config import get_site_extractor, get_headers_for_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structure de retour
# ---------------------------------------------------------------------------

@dataclass
class VideoFormat:
    """Un format de qualité disponible."""
    label: str
    ext: str
    url: str
    is_hls: bool = False
    size: Optional[int] = None
    extra_headers: Optional[dict] = None
    format_id: Optional[str] = None
    has_audio: bool = True


@dataclass
class VideoInfo:
    """Résultat d'une extraction."""
    success: bool
    title: str = ""
    formats: List[VideoFormat] = field(default_factory=list)
    thumbnail: str = ""
    source_url: str = ""
    error: str = ""
    extractor: str = ""

    def best_format(self) -> Optional[VideoFormat]:
        if not self.formats:
            return None
        rank = {2160: 9, 1440: 8, 1080: 7, 720: 6, 540: 5,
                480: 4, 360: 3, 240: 2, 144: 1}
        def score(fmt):
            label = (fmt.label or "").lower()
            if "4k" in label:
                return 10
            match = re.search(r"(\d{3,4})\s*p", label)
            return rank.get(int(match.group(1)), 0) if match else 0
        return max(self.formats, key=score)


# ---------------------------------------------------------------------------
# Helpers HTTP + parsing
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_HEADERS_HTML = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8,"
               "application/signed-exchange;v=b3;q=0.7"),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
    "Sec-CH-UA": ('"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'),
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Priority": "u=0, i",
}


def _http_get(url, timeout=20, extra_headers=None, session=None):
    sess = session or _SESSION
    h = dict(_HEADERS_HTML)
    h.update(get_headers_for_url(url))
    if extra_headers:
        h.update(extra_headers)
    r = sess.get(url, headers=h, timeout=timeout, allow_redirects=True)
    if r.status_code not in (200, 206):
        raise RuntimeError(f"HTTP {r.status_code} sur {url[:80]}")
    ce = r.headers.get("Content-Encoding", "").lower()
    if "br" in ce:
        try:
            import brotli
            r._content = brotli.decompress(r.content)
        except Exception:
            try:
                import brotlicffi as brotli
                r._content = brotli.decompress(r.content)
            except Exception:
                pass
    return r


def _http_post(url, json_data=None, data=None, timeout=20, extra_headers=None, session=None):
    sess = session or _SESSION
    h = dict(_HEADERS_HTML)
    h.pop("Sec-Fetch-Dest", None)
    h.pop("Sec-Fetch-Mode", None)
    h.pop("Sec-Fetch-Site", None)
    h.pop("Sec-Fetch-User", None)
    h.pop("Upgrade-Insecure-Requests", None)
    h.pop("Cache-Control", None)
    h["Sec-Fetch-Dest"] = "empty"
    h["Sec-Fetch-Mode"] = "cors"
    h["Sec-Fetch-Site"] = "same-origin"
    h["Accept"] = "application/json, text/plain, */*"
    h.update(get_headers_for_url(url))
    if json_data is not None:
        h.setdefault("Content-Type", "application/json")
    if extra_headers:
        h.update(extra_headers)
    r = sess.post(url, headers=h, timeout=timeout, json=json_data, data=data)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"HTTP {r.status_code} POST {url[:80]}")
    ce = r.headers.get("Content-Encoding", "").lower()
    if "br" in ce:
        try:
            import brotli
            r._content = brotli.decompress(r.content)
        except Exception:
            try:
                import brotlicffi as brotli
                r._content = brotli.decompress(r.content)
            except Exception:
                pass
    return r


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:180] or "video"


_M3U8_RE = re.compile(r"""https?://[^\s"'`<>)]+\.m3u8(?:\?[^\s"'`<>)]*)?""")
_MP4_RE = re.compile(r"""https?://[^\s"'`<>)]+\.mp4(?:\?[^\s"'`<>)]*)?""")
_BLOB_URL_RE = re.compile(r"""src\s*=\s*["'](blob:[^"']+)["']""")


def _find_video_urls(text: str) -> List[str]:
    """Cherche toutes les URLs de vidéos dans un texte HTML/JS."""
    found = []
    for m in _M3U8_RE.finditer(text or ""):
        if m.group(0) not in found:
            found.append(m.group(0))
    for m in _MP4_RE.finditer(text or ""):
        if m.group(0) not in found:
            found.append(m.group(0))
    return found


def _fmt_from_url(url: str, label_prefix: str = "") -> Optional[VideoFormat]:
    url_l = url.lower()
    if ".m3u8" in url_l:
        ext, is_hls = "mp4", True
    elif ".mp4" in url_l:
        ext, is_hls = "mp4", False
    elif ".webm" in url_l:
        ext, is_hls = "webm", False
    else:
        return None
    label = f"{label_prefix} HLS" if is_hls else f"{label_prefix} MP4"
    return VideoFormat(label=label.strip(), ext=ext, url=url, is_hls=is_hls)


def _expand_hls_master(formats: List[VideoFormat]) -> List[VideoFormat]:
    """Expose the resolutions inside a generic HLS master playlist."""
    candidates = [
        fmt for fmt in formats
        if fmt.is_hls and not re.search(
            r"\b\d{3,4}p\b|\b\d+\s*kb/s\b", fmt.label or "", re.I)
    ]
    if not candidates and len(formats) == 1 and formats[0].is_hls:
        candidates = [formats[0]]
    if not candidates:
        enriched = []
        for fmt in formats:
            if fmt.is_hls and not fmt.size:
                size = _estimate_hls_size(fmt.url, 0, fmt.extra_headers)
                if size:
                    fmt = VideoFormat(
                        label=fmt.label, ext=fmt.ext, url=fmt.url,
                        is_hls=True, size=size,
                        extra_headers=fmt.extra_headers,
                        format_id=fmt.format_id, has_audio=fmt.has_audio,
                    )
            enriched.append(fmt)
        return enriched

    source = candidates[0]
    response = None
    try:
        response = _http_get(source.url, timeout=12,
                             extra_headers=source.extra_headers)
        text = response.text or ""
        base_url = response.url or source.url
        if not text.lstrip().startswith("#EXTM3U"):
            return formats

        variants = []
        pending = None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                attrs = {
                    key: value.strip().strip('"')
                    for key, value in re.findall(
                        r'([A-Z0-9-]+)=((?:"[^"]*")|[^,]*)', line.split(":", 1)[1])
                }
                pending = attrs
            elif pending is not None and line and not line.startswith("#"):
                target = urljoin(base_url, line)
                resolution = pending.get("RESOLUTION", "")
                height = resolution.split("x", 1)[-1] if "x" in resolution else ""
                bandwidth = int(pending.get("AVERAGE-BANDWIDTH")
                                or pending.get("BANDWIDTH") or 0)
                quality = f"{height}p" if height.isdigit() else (
                    f"{bandwidth // 1000} kb/s" if bandwidth else "Qualité inconnue")
                variants.append((target, quality, bandwidth))
                pending = None

        if not variants:
            size = _estimate_hls_size(source.url, 0, source.extra_headers)
            if not size:
                return formats
            updated = VideoFormat(
                label=source.label, ext=source.ext, url=source.url,
                is_hls=True, size=size, extra_headers=source.extra_headers,
                format_id=source.format_id, has_audio=source.has_audio,
            )
            return [updated if fmt is source else fmt for fmt in formats]

        expanded = []
        for target, quality, bandwidth in variants[:16]:
            size = _estimate_hls_size(target, bandwidth, source.extra_headers)
            label = source.label or "HLS"
            if quality.lower() not in label.lower():
                label = f"{label} · {quality}"
            expanded.append(VideoFormat(
                label=label, ext=source.ext, url=target, is_hls=True,
                size=size, extra_headers=source.extra_headers,
            ))
        result = list(formats)
        result[result.index(source):result.index(source) + 1] = expanded
        return result
    except Exception:
        return formats
    finally:
        if response is not None:
            response.close()


def _estimate_hls_size(url: str, bandwidth: int, extra_headers=None):
    """Estimate a VOD playlist size from its bitrate or segment lengths."""
    response = None
    try:
        response = _http_get(url, timeout=12, extra_headers=extra_headers)
        lines = (response.text or "").splitlines()
        duration = 0.0
        segments = []
        segment_duration = None
        for line in lines:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                raw_duration = line.split(":", 1)[1].split(",", 1)[0]
                try:
                    segment_duration = float(raw_duration)
                    duration += segment_duration
                except ValueError:
                    segment_duration = None
            elif line and not line.startswith("#"):
                segments.append((urljoin(response.url or url, line), segment_duration))
                segment_duration = None
        if duration and bandwidth:
            return int(bandwidth * duration / 8)
        if not segments:
            return None

        # Some media playlists omit BANDWIDTH. Probe all short playlists and a
        # spread of at most 32 segments on long ones, then scale by duration.
        if len(segments) <= 32:
            sample_indexes = list(range(len(segments)))
        else:
            sample_indexes = sorted({round(i * (len(segments) - 1) / 31)
                                     for i in range(32)})
        def probe_segment(index):
            segment_url, seconds = segments[index]
            if not seconds:
                return 0, 0
            try:
                head = requests.head(
                    segment_url,
                    headers={**get_headers_for_url(segment_url), **(extra_headers or {})},
                    timeout=4, allow_redirects=True)
                try:
                    length = int(head.headers.get("Content-Length", 0) or 0)
                    return (length, seconds) if length else (0, 0)
                finally:
                    head.close()
            except Exception:
                return 0, 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            measured = list(pool.map(probe_segment, sample_indexes))
        measured_bytes = sum(item[0] for item in measured)
        measured_seconds = sum(item[1] for item in measured)
        if measured_bytes and measured_seconds and duration:
            return int(measured_bytes / measured_seconds * duration)
        return None
    except Exception:
        return None
    finally:
        if response is not None:
            response.close()


# ---------------------------------------------------------------------------
# 1) YouTube — via yt-dlp
# ---------------------------------------------------------------------------

def _extract_youtube(url: str, extra_headers=None) -> VideoInfo:
    try:
        import yt_dlp
    except ImportError:
        return VideoInfo(success=False, error="yt-dlp n'est pas installé",
                         source_url=url, extractor="youtube")

    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "format": None, "noplaylist": True,
                "js_runtimes": {"node": {}},
                "extractor_args": {
                    # Avoid the broken tv_downgraded client used for signed-in
                    # sessions; default + web_embedded is the current yt-dlp
                    # workaround for "The page needs to be reloaded".
                    "youtube": {"player_client": ["default", "web_embedded"]},
                    "youtubetab": {"skip": ["authcheck"]},
                }}
    if extra_headers:
        # Do not forward browser cookies as a raw Cookie header to yt-dlp:
        # newer yt-dlp warns about this and YouTube's logged-in client is
        # currently prone to returning "The page needs to be reloaded".
        ydl_headers = {key: value for key, value in extra_headers.items()
                       if key.lower() in ("user-agent", "referer")}
        if ydl_headers:
            ydl_opts["http_headers"] = ydl_headers

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return VideoInfo(success=False, error=str(e),
                         source_url=url, extractor="youtube")

    title = info.get("title") or info.get("id") or "youtube_video"
    thumb = (info.get("thumbnail")
             or (info.get("thumbnails") or [{}])[-1].get("url", ""))

    formats = []
    seen = set()
    duration = float(info.get("duration") or 0)
    audio_bitrate = max(
        (float(fmt.get("abr") or 0) for fmt in info.get("formats", [])
         if fmt.get("vcodec") == "none"), default=0.0)
    for f in info.get("formats", []):
        # Keep video-only DASH formats too. QueueManager pairs them with audio
        # through yt-dlp/ffmpeg when downloading.
        if f.get("vcodec") == "none":
            continue
        ext = f.get("ext") or "mp4"
        if ext not in ("mp4", "webm", "mkv"):
            continue
        height = f.get("height")
        label = (f.get("format_note") or f.get("resolution")
                 or (f"{height}p" if height else "Qualité inconnue"))
        if height and not re.search(r"\d{3,4}p", str(label), re.I):
            label = f"{height}p · {label}"
        proto = f.get("protocol", "")
        url_f = f.get("url")
        if not url_f:
            continue
        format_id = str(f.get("format_id") or "")
        key = (format_id, ext, proto)
        if key in seen:
            continue
        seen.add(key)
        is_hls = proto in ("m3u8", "m3u8_native") or url_f.lower().endswith(".m3u8")
        size = f.get("filesize") or f.get("filesize_approx")
        if not size and duration:
            bitrate = float(f.get("tbr") or 0) * 1000
            if f.get("acodec") == "none":
                bitrate += audio_bitrate * 1000
            size = int(bitrate * duration / 8) if bitrate else None
        formats.append(VideoFormat(
            label=str(label), ext=ext, url=url_f, is_hls=is_hls,
            size=size, format_id=format_id,
            has_audio=f.get("acodec") != "none",
            extra_headers={key: value for key, value in (extra_headers or {}).items()
                           if key.lower() in ("user-agent", "referer")},
        ))

    if not formats:
        best_v = best_a = None
        for f in info.get("formats", []):
            if f.get("vcodec") != "none" and f.get("acodec") == "none":
                if best_v is None or int(f.get("height") or 0) > int(best_v.get("height") or 0):
                    best_v = f
            if f.get("acodec") != "none" and f.get("vcodec") == "none":
                if best_a is None or int(f.get("abr") or 0) > int(best_a.get("abr") or 0):
                    best_a = f
        if best_v and best_a and best_v.get("url"):
            formats.append(VideoFormat(
                label=best_v.get("format_note") or "Vidéo seule",
                ext=best_v.get("ext") or "mp4",
                url=best_v["url"],
                is_hls=best_v.get("protocol", "").startswith("m3u8"),
                format_id=str(best_v.get("format_id") or ""),
                has_audio=False,
            ))

    if not formats and info.get("url"):
        formats.append(VideoFormat(
            label="Défaut",
            ext=info.get("ext") or "mp4",
            url=info["url"],
            is_hls=info.get("protocol", "").startswith("m3u8"),
            format_id=str(info.get("format_id") or ""),
        ))

    return VideoInfo(
        success=bool(formats), title=title, formats=formats, thumbnail=thumb,
        source_url=url, extractor="youtube",
        error="" if formats else "Aucun format vidéo trouvé",
    )


# ---------------------------------------------------------------------------
# 2) Lecteurs externes : VOE, DoodStream, Uqload, Streamtape, Mixdrop, Vidoza, UpVid
# ---------------------------------------------------------------------------

def _extract_voe(url: str) -> VideoInfo:
    """Extrait depuis un lien voe.sx / voeunblock.com"""
    try:
        r = _http_get(url)
    except Exception as e:
        return VideoInfo(success=False, source_url=url, extractor="voe", error=str(e))

    html = r.text
    soup = BeautifulSoup(html, "lxml")

    # Méthode 1 : chercher m3u8/mp4 dans tout le HTML/JS
    urls = _find_video_urls(html)

    # Méthode 2 : regex spécifique à VOE (sources encodées)
    if not urls:
        for pat in [
            r"'hls':\s*'([^']+)'",
            r'"hls"\s*:\s*"([^"]+)"',
            r"'file':\s*'([^']+)'",
            r'"file"\s*:\s*"([^"]+)"',
            r"let\s+mp4\s*=\s*['\"]([^'\"]+)",
            r"window\.location\.href\s*=\s*['\"]([^'\"]+\.m3u8[^'\"]*)",
        ]:
            m = re.search(pat, html)
            if m:
                u = m.group(1)
                if u.startswith("//"):
                    u = "https:" + u
                if u.startswith("/"):
                    u = urljoin(url, u)
                urls.append(u)

    # Méthode 3 : décodage VOE (sources souvent encodées en base64)
    if not urls:
        for pat in [
            r"JSON\.parse\(atob\(['\"]([A-Za-z0-9+/=]+)['\"]\)\)",
            r"atob\(['\"]([A-Za-z0-9+/=]{40,})['\"]\)",
        ]:
            for m in re.finditer(pat, html):
                try:
                    decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
                    for u in _find_video_urls(decoded):
                        urls.append(u)
                except Exception:
                    pass

    # Méthode 4 : chercher iframe -> extraire depuis iframe
    if not urls:
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src")
            if src and ("voe" in src or ".m3u8" in src or ".mp4" in src):
                try:
                    sub_r = _http_get(src if src.startswith("http") else urljoin(url, src))
                    for u in _find_video_urls(sub_r.text):
                        urls.append(u)
                except Exception:
                    pass

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    title = title.split("|")[0].split("-")[0].strip() or "voe_video"

    formats = []
    for u in urls[:5]:
        fmt = _fmt_from_url(u, label_prefix="VOE")
        if fmt:
            formats.append(fmt)

    return VideoInfo(
        success=bool(formats), title=title, formats=formats,
        source_url=url, extractor="voe",
        error="" if formats else "Aucune URL vidéo trouvée sur VOE",
    )


def _extract_dood(url: str) -> VideoInfo:
    """DoodStream / dood.pm / dood.watch / dood.so"""
    try:
        r = _http_get(url)
    except Exception as e:
        return VideoInfo(success=False, source_url=url, extractor="dood", error=str(e))

    html = r.text
    soup = BeautifulSoup(html, "lxml")
    urls = _find_video_urls(html)

    # Pattern Dood : /pass_md5/... suivi de token dans le JS
    if not urls:
        # Pattern 1 : script inline avec window.location.href ou sources
        for pat in [
            r"""\$\.get\(['"]([^'"]+/pass_md5/[^'"]+)""",
            r"""['"](/d[^'"]+/video[^'"]*)['"]""",
            r"""file\s*:\s*['"]([^'"]+\.(?:mp4|m3u8)[^'"]*)""",
            r"""source\s*src=['"]([^'"]+)""",
            r"""<video[^>]+src=['"]([^'"]+)""",
        ]:
            for m in re.finditer(pat, html):
                u = m.group(1)
                if u.startswith("//"):
                    u = "https:" + u
                elif u.startswith("/"):
                    u = urljoin(r.url or url, u)
                urls.append(u)

        # Pattern 2 : token encodé dans JS (obfuscation Dood classique)
        if not urls:
            m_token = re.search(r'[?&]token=([a-zA-Z0-9]+)', html)
            if not m_token:
                m_token = re.search(r"makePlay\s*\(\s*['\"]?([a-zA-Z0-9_-]+)", html)
            if m_token:
                pass

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    title = title.split("|")[0].split("-")[0].strip() or "dood_video"

    formats = []
    for u in urls[:5]:
        fmt = _fmt_from_url(u, label_prefix="Dood")
        if fmt:
            formats.append(fmt)

    return VideoInfo(
        success=bool(formats), title=title, formats=formats,
        source_url=url, extractor="dood",
        error="" if formats else "Aucune URL vidéo trouvée sur Dood",
    )


def _extract_uqload(url: str) -> VideoInfo:
    """Uqload.net / uqload.com / uqload.io"""
    try:
        r = _http_get(url)
    except Exception as e:
        return VideoInfo(success=False, source_url=url, extractor="uqload", error=str(e))

    html = r.text
    soup = BeautifulSoup(html, "lxml")
    urls = _find_video_urls(html)

    if not urls:
        for pat in [
            r"""sources?\s*:\s*\[\s*['"]([^'"]+)""",
            r"""file\s*:\s*['"]([^'"]+\.(?:mp4|m3u8)[^'"]*)""",
            r"""<video[^>]*>\s*<source[^>]+src=['"]([^'"]+)""",
            r"""<video[^>]+src=['"]([^'"]+)""",
            r"""window\.player\.src\(['"]([^'"]+)""",
            r"""Clappr\.Player\([\s\S]*?source:\s*['"]([^'"]+)""",
        ]:
            for m in re.finditer(pat, html):
                u = m.group(1)
                if u.startswith("//"):
                    u = "https:" + u
                elif u.startswith("/"):
                    u = urljoin(r.url or url, u)
                urls.append(u)

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    title = title.split("|")[0].split("-")[0].strip() or "uqload_video"

    # Titre alternatif depuis balise meta og:title ou h1/h2
    if not title or title == "uqload_video":
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()
        else:
            h = soup.find(["h1", "h2"])
            if h and h.get_text(strip=True):
                title = h.get_text(strip=True)[:100]

    formats = []
    for u in urls[:5]:
        fmt = _fmt_from_url(u, label_prefix="Uqload")
        if fmt:
            formats.append(fmt)

    return VideoInfo(
        success=bool(formats), title=title, formats=formats,
        source_url=url, extractor="uqload",
        error="" if formats else "Aucune URL vidéo trouvée sur Uqload",
    )


def _extract_streamtape(url: str) -> VideoInfo:
    """Streamtape.com / streamtape.net / streamtape.to"""
    try:
        r = _http_get(url)
    except Exception as e:
        return VideoInfo(success=False, source_url=url, extractor="streamtape", error=str(e))

    html = r.text
    soup = BeautifulSoup(html, "lxml")
    urls = _find_video_urls(html)

    if not urls:
        for pat in [
            r"""['"](https?://[^'"]+streamtape[^'"]*get_video[^'"]*)['"]""",
            r"""['"]([^'"]+/v\d+[^'"]*)['"]\s*\+\s*['"]([^'"]*)['"]""",
            r"""document\.getElementById\(['"]videolink['"]\)[^;]*innerHTML\s*=\s*['"]([^'"]+)""",
            r"""robotlink['"]\s*>\s*<div[^>]*>([^<]+)""",
            r"""id\s*=\s*['"]botlink['"][^>]*>\s*([a-zA-Z0-9_\-/.?=&]+)""",
        ]:
            m = re.search(pat, html)
            if m:
                for g in m.groups():
                    if g and ("http" in g or g.startswith("/") or ".mp4" in g or ".m3u8" in g):
                        u = g
                        if u.startswith("//"):
                            u = "https:" + u
                        elif u.startswith("/"):
                            u = urljoin(r.url or url, u)
                        urls.append(u)

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    title = title.split("|")[0].split("-")[0].strip() or "streamtape_video"
    # Streamtape a souvent le nom de fichier dans <meta name="description">
    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        t = desc["content"].strip()
        if t and len(t) < 120:
            title = t

    formats = []
    for u in urls[:5]:
        fmt = _fmt_from_url(u, label_prefix="Streamtape")
        if fmt:
            formats.append(fmt)

    return VideoInfo(
        success=bool(formats), title=title, formats=formats,
        source_url=url, extractor="streamtape",
        error="" if formats else "Aucune URL vidéo trouvée sur Streamtape",
    )


def _extract_mixdrop(url: str) -> VideoInfo:
    """Mixdrop.co / mixdrop.to / mixdrop.bz"""
    try:
        r = _http_get(url)
    except Exception as e:
        return VideoInfo(success=False, source_url=url, extractor="mixdrop", error=str(e))

    html = r.text
    soup = BeautifulSoup(html, "lxml")
    urls = _find_video_urls(html)

    if not urls:
        for pat in [
            r"""(?:eval|function|window)\([\s\S]{0,500}?((?:https?:)?//[^\s"'`]+\.mp4[^"'\s)]*)""",
            r"""['"]([^'"]+/f/[^'"]+\.(?:mp4|m3u8)[^'"]*)['"]""",
            r"""player\.poster\s*=\s*['"]([^'"]+)""",
            r"""MDCore\.setup\([\s\S]*?file\s*:\s*['"]([^'"]+)""",
            r"""<video[^>]+src=['"]([^'"]+)""",
        ]:
            for m in re.finditer(pat, html):
                u = m.group(1)
                if u.startswith("//"):
                    u = "https:" + u
                elif u.startswith("/"):
                    u = urljoin(r.url or url, u)
                urls.append(u)

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    title = title.split("|")[0].split("-")[0].strip() or "mixdrop_video"

    formats = []
    for u in urls[:5]:
        fmt = _fmt_from_url(u, label_prefix="Mixdrop")
        if fmt:
            formats.append(fmt)

    return VideoInfo(
        success=bool(formats), title=title, formats=formats,
        source_url=url, extractor="mixdrop",
        error="" if formats else "Aucune URL vidéo trouvée sur Mixdrop",
    )


def _extract_vidoza(url: str) -> VideoInfo:
    """Vidoza.net / vidoza.org"""
    try:
        r = _http_get(url)
    except Exception as e:
        return VideoInfo(success=False, source_url=url, extractor="vidoza", error=str(e))

    html = r.text
    soup = BeautifulSoup(html, "lxml")
    urls = _find_video_urls(html)

    if not urls:
        for pat in [
            r"""sourcesCode\s*=\s*(\[[\s\S]{0,2000}?\])""",
            r"""['"]([^'"]+\.mp4(?:\?[^'"]*)?)['"]""",
            r"""<source[^>]+src=['"]([^'"]+)""",
            r"""jwplayer\([^)]*\)\.setup\([\s\S]*?file\s*:\s*['"]([^'"]+)""",
        ]:
            for m in re.finditer(pat, html):
                u = m.group(1)
                if u.startswith("http") or ".mp4" in u:
                    if u.startswith("//"):
                        u = "https:" + u
                    elif u.startswith("/"):
                        u = urljoin(r.url or url, u)
                    urls.append(u)

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    title = title.split("|")[0].split("-")[0].strip() or "vidoza_video"

    formats = []
    for u in urls[:5]:
        fmt = _fmt_from_url(u, label_prefix="Vidoza")
        if fmt:
            formats.append(fmt)

    return VideoInfo(
        success=bool(formats), title=title, formats=formats,
        source_url=url, extractor="vidoza",
        error="" if formats else "Aucune URL vidéo trouvée sur Vidoza",
    )


def _extract_upvid(url: str) -> VideoInfo:
    """Upvid.co / upvid.lol"""
    try:
        r = _http_get(url)
    except Exception as e:
        return VideoInfo(success=False, source_url=url, extractor="upvid", error=str(e))

    html = r.text
    soup = BeautifulSoup(html, "lxml")
    urls = _find_video_urls(html)

    if not urls:
        for pat in [
            r"""<video[^>]*>\s*<source[^>]+src=['"]([^'"]+)""",
            r"""['"]([^'"]*cdn[^'"]*\.mp4[^'"]*)['"]""",
            r"""['"]([^'"]*\.(?:mp4|m3u8)(?:\?[^'"]*)?)['"]""",
        ]:
            for m in re.finditer(pat, html):
                u = m.group(1)
                if u.startswith("//"):
                    u = "https:" + u
                elif u.startswith("/"):
                    u = urljoin(r.url or url, u)
                urls.append(u)

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    title = title.split("|")[0].split("-")[0].strip() or "upvid_video"

    formats = []
    for u in urls[:5]:
        fmt = _fmt_from_url(u, label_prefix="UpVid")
        if fmt:
            formats.append(fmt)

    return VideoInfo(
        success=bool(formats), title=title, formats=formats,
        source_url=url, extractor="upvid",
        error="" if formats else "Aucune URL vidéo trouvée sur UpVid",
    )


def _vidzy_decode_src(b64: str, html: str, page_host: str) -> Optional[str]:
    """Décode la source Vidzy (atob + reverse + XOR) et réécrit l'urlset en flux simple."""
    if not b64:
        return None

    extra_hosts = set()
    for m2 in re.finditer(r"""https?://([a-z0-9.-]*vidzy[a-z0-9.-]*)""", html, re.I):
        extra_hosts.add(m2.group(1).lower())
    for iframe in re.findall(r"""<iframe[^>]+src=['"]([^"']+)['"]""", html, re.I):
        try:
            extra_hosts.add((urlparse(iframe).hostname or "").lower())
        except Exception:
            pass

    hosts = list(dict.fromkeys([
        (page_host or "").lower(),
        "vidzy.cc", "vidzy.org", "vidzy.live", "vidzy.lol",
    ] + list(extra_hosts)))

    URL_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~:/?#[]@!$&'()*+,;=%")

    try:
        raw = base64.b64decode(b64).decode("latin-1")
    except Exception:
        return None
    a = raw[::-1]
    a_len = len(a)

    for host in hosts:
        H = 0
        for c in (host or ""):
            H = (H + ord(c)) & 0xFF
        for BC in range(0, 220):
            r = []
            ok = True
            for i in range(a_len):
                kk = (0x3d + i * 89 + H + BC) & 0xFF
                ch = chr(ord(a[i]) ^ kk)
                if i == 0 and ch != "h":
                    ok = False
                    break
                if ch not in URL_CHARS:
                    ok = False
                    break
                r.append(ch)
            if not ok:
                continue
            decoded = "".join(r)
            if (".m3u8" in decoded or ".mp4" in decoded) and decoded.startswith("http"):
                # Appliquer la simplification fssMuxT du player JS
                try:
                    if ",.urlset/master.m3u8" in decoded:
                        qi = decoded.find("?")
                        q = decoded[qi:] if qi >= 0 else ""
                        pp = decoded[:qi] if qi >= 0 else decoded
                        ci = pp.find("/,")
                        if ci >= 0:
                            pre = pp[:ci]
                            af = pp[ci + 2:]
                            cm = af.find(",")
                            code = af[:cm] if cm >= 0 else af
                            decoded = pre + "/" + code + "/index-v1-a1.m3u8" + q
                except Exception:
                    pass
                return decoded
    return None


def _extract_vidzy(url: str) -> VideoInfo:
    """Vidzy.cc / vidzy.org / vidzy.live / vidzy.lol — lecteur embed VideoJS."""
    session = _SESSION
    try:
        r = _http_get(url, session=session, extra_headers={
            "Sec-Fetch-Dest": "iframe",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
        })
    except Exception as e:
        return VideoInfo(success=False, source_url=url, extractor="vidzy", error=str(e))

    html = r.text
    soup = BeautifulSoup(html, "lxml")

    title = ""
    m_title = re.search(
        r"""shareEmbed\s*:\s*['"][\s\S]{0,500}?endAction\s*:\s*['"]\w+['"]\s*,\s*title\s*:\s*["']([^"']{4,})["']""",
        html,
    )
    if not m_title:
        m_title = re.search(r"""endAction\s*:\s*['"]\w+['"]\s*,\s*title\s*:\s*["']([^"']{4,})["']""", html)
    if not m_title:
        m_title = re.search(r"""metaTitle\s*:\s*["']([^"']{2,})["']""", html)
    if m_title:
        title = m_title.group(1).strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.split("|")[0].split("-")[0].strip()
    title = title or "vidzy_video"

    thumbnail = ""
    m_thumb = (re.search(r"""metaThumbnail\s*:\s*["']([^"']+)["']""", html)
               or re.search(r"""<video[^>]+poster\s*=\s*["']([^"']+)["']""", html, re.I))
    if m_thumb:
        thumbnail = m_thumb.group(1)
        if thumbnail.startswith("//"):
            thumbnail = "https:" + thumbnail
        elif thumbnail.startswith("/"):
            thumbnail = urljoin(r.url or url, thumbnail)

    formats = []
    page_host = (urlparse(r.url or url).hostname or "")
    embed_url = r.url or url
    embed_host = "https://" + (page_host or "vidzy.cc") + "/"

    vidzy_extra = {
        "Referer": embed_url,
        "Origin": embed_host.rstrip("/"),
        "Accept": ("*/*"),
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }

    # Méthode 1 : décodage atob+reverse+XOR depuis la source VideoJS (IIFE dans sources)
    m_src = re.search(
        r"""src:\s*\(function\(s\)\{[\s\S]{0,1500}?\}\)\("([A-Za-z0-9+/=%]+)"\)\s*,\s*type:\s*["']application/x-mpegURL["']""",
        html,
    )
    decoded_url = None
    if m_src:
        decoded_url = _vidzy_decode_src(m_src.group(1), html, page_host)
        if decoded_url:
            fmt = VideoFormat(label="Vidzy HLS", ext="mp4", url=decoded_url, is_hls=True,
                              extra_headers=dict(vidzy_extra))
            formats.append(fmt)

    # Méthode 2 : fallback sur m3u8/mp4 trouvés directement (non troll)
    if not formats:
        for u in _find_video_urls(html):
            if "/troll/" in u.lower():
                continue
            fmt = _fmt_from_url(u, label_prefix="Vidzy")
            if fmt:
                fmt.extra_headers = dict(vidzy_extra)
                formats.append(fmt)
        if not formats:
            for u in _find_video_urls(html):
                fmt = _fmt_from_url(u, label_prefix="Vidzy")
                if fmt:
                    fmt.extra_headers = dict(vidzy_extra)
                    formats.append(fmt)
                    break

    # Méthode 3 : iframe imbriqué (vidzy.cc -> vidzy.org souvent)
    if not formats:
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src") or ""
            if not src or src.startswith("about:") or src.startswith("javascript:"):
                continue
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = urljoin(r.url or url, src)
            if get_site_extractor(src) == "vidzy" and src != (r.url or url):
                try:
                    r2 = _http_get(src, session=session, extra_headers={
                        "Sec-Fetch-Dest": "iframe",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "same-site",
                        "Referer": r.url or url,
                    })
                    sub = _extract_vidzy.__wrapped__ if hasattr(_extract_vidzy, "__wrapped__") else None
                    if sub:
                        vi = sub(src)
                    else:
                        vi = _extract_vidzy(src)
                    if vi and vi.success:
                        formats.extend(vi.formats)
                        if not title or title == "vidzy_video":
                            title = vi.title or title
                        if not thumbnail:
                            thumbnail = vi.thumbnail or thumbnail
                except Exception:
                    pass

    return VideoInfo(
        success=bool(formats), title=title, formats=formats, thumbnail=thumbnail,
        source_url=url, extractor="vidzy",
        error="" if formats else "Aucune URL vidéo trouvée sur Vidzy",
    )


# ---------------------------------------------------------------------------
# 3) AnimeSama — extracteur principal (API /player/resolve + fallback lecteurs)
# ---------------------------------------------------------------------------

def _parse_window_profile(html: str) -> dict:
    """Parse window.__PROFILE depuis le HTML d'AnimeSama (JS object literal → dict)."""
    m = re.search(r"window\.__PROFILE\s*=\s*(\{[\s\S]*?\})\s*;?\s*</script>", html, re.I)
    if not m:
        return {}
    raw = m.group(1)
    try:
        quoted = re.sub(r"""([{,]\s*)([A-Za-z_$][A-Za-z0-9_$]*)\s*:""", r'\1"\2":', raw)
        quoted = re.sub(r""":\s*undefined\b""", ":null", quoted)
        return json.loads(quoted)
    except Exception:
        try:
            name = re.search(r"""name\s*:\s*["']([^"']+)["']""", raw)
            img = re.search(r"""img\s*:\s*["']([^"']+)["']""", raw)
            lab = re.search(r"""label\s*:\s*["']([^"']+)["']""", raw)
            lang = re.search(r"""lang\s*:\s*["']([^"']+)["']""", raw)
            csrf = re.search(r"""csrf\s*:\s*["']([^"']+)["']""", raw)
            return {
                "csrf": csrf.group(1) if csrf else "",
                "anime": {
                    "name": name.group(1) if name else "",
                    "img": (img.group(1).replace("\\/", "/") if img else ""),
                    "label": lab.group(1) if lab else "",
                    "lang": lang.group(1) if lang else "",
                },
                "loggedIn": False,
            }
        except Exception:
            return {}


def _animesama_via_api(page_url: str, html: str, session) -> Optional[List[VideoFormat]]:
    """Tente d'obtenir le m3u8 via l'API POST /api/player/resolve"""
    profile = _parse_window_profile(html)
    csrf = profile.get("csrf") or ""
    anime = profile.get("anime") or {}
    watch_url = anime.get("watchUrl") or ""
    ep_num = 1
    em = re.search(r"[?&]ep=(\d+)|episode[_\s-]?(\d+)", page_url, re.I)
    if em:
        ep_num = int(em.group(1) or em.group(2) or 1)
    em2 = re.search(r"Episode\s+(\d+)", html, re.I)
    if ep_num == 1 and em2:
        ep_num = int(em2.group(1))

    iframe_player = None
    body_soup = BeautifulSoup(html, "lxml")
    for f in body_soup.find_all("iframe"):
        src = f.get("src") or ""
        if src and src != "about:blank" and "/player/" in src:
            iframe_player = src
            break

    if not iframe_player and csrf:
        pathname = ""
        parsed = urlparse(page_url)
        if parsed.path.startswith("/catalogue/"):
            pathname = parsed.path[len("/catalogue/"):]
        elif watch_url.startswith("/catalogue/"):
            pathname = watch_url[len("/catalogue/"):]
        if pathname:
            pathname = pathname.strip("/")
            sign_url = f"/api/player/sign/{pathname}"
            if sign_url.startswith("/"):
                sign_url = urljoin(page_url, sign_url)
            for lecteur_idx in (0, 1, 2, 3):
                try:
                    sr = _http_post(
                        sign_url,
                        json_data={"ep": ep_num, "lecteur": lecteur_idx},
                        extra_headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "X-CSRF-Token": str(csrf),
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": page_url,
                        },
                        session=session,
                    )
                    sdata = sr.json()
                    if isinstance(sdata, dict) and sdata.get("ok") and sdata.get("url"):
                        iframe_player = sdata["url"]
                        break
                except Exception:
                    continue

    if not iframe_player:
        name = anime.get("name") or ""
        label = anime.get("label") or ""
        lang = anime.get("lang") or ""
        if watch_url:
            base = watch_url.strip()
            seg = [s for s in base.split("/") if s]
            if len(seg) >= 3:
                n, lb, lg = seg[-3], seg[-2], seg[-1]
                iframe_player = f"/player/{n}/{lb}/{lg}?ep={ep_num}"
            else:
                base_clean = base.lstrip("/").rstrip("/").replace(" ", "-")
                iframe_player = f"/player/{base_clean}?ep={ep_num}"
        elif name and label and lang:
            n = name.strip().replace(" ", "-")
            lb = label.strip().replace(" ", "-")
            lg = lang.strip().replace(" ", "-")
            iframe_player = f"/player/{n}/{lb}/{lg}?ep={ep_num}"

    if not iframe_player:
        return None
    if iframe_player.startswith("//"):
        iframe_player = "https:" + iframe_player
    elif iframe_player.startswith("/"):
        iframe_player = urljoin(page_url, iframe_player)

    try:
        player_r = _http_get(iframe_player, session=session,
                            extra_headers={"Referer": page_url})
    except Exception:
        return None

    player_html = player_r.text
    body_attrs = {}
    body_tag = re.search(r"<body([^>]*)>", player_html, re.I)
    if body_tag:
        for m in re.finditer(r"""data-([a-z0-9-]+)\s*=\s*["']([^"']*)["']""", body_tag.group(1)):
            body_attrs[m.group(1)] = m.group(2)

    resolve_token = body_attrs.get("resolve-token")
    resolve_url = body_attrs.get("resolve-url") or "/api/player/resolve"
    fallback_url = body_attrs.get("fallback-url")

    # Méthode 1 : POST resolve API
    if resolve_token:
        if resolve_url.startswith("/"):
            resolve_url = urljoin(page_url, resolve_url)
        try:
            resolve_headers = {
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": iframe_player,
            }
            if csrf:
                resolve_headers["X-CSRF-Token"] = str(csrf)
            post_r = _http_post(resolve_url, json_data={"token": resolve_token},
                                extra_headers=resolve_headers,
                                session=session)
            data = post_r.json()
            urls = []
            if isinstance(data, dict):
                for key in ("src", "url", "manifest", "playlist", "hls", "source", "play_url"):
                    val = data.get(key)
                    if isinstance(val, str):
                        if val.startswith("/"):
                            val = urljoin(page_url, val)
                        if val.startswith("http"):
                            urls.append((val, ""))
                for key in ("sources", "qualities", "play_candidates"):
                    val = data.get(key)
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict):
                                for k in ("src", "url", "file", "src_uri", "play_url"):
                                    v = item.get(k)
                                    if isinstance(v, str):
                                        if v.startswith("/"):
                                            v = urljoin(page_url, v)
                                        if v.startswith("http"):
                                            label = item.get("label") or item.get("quality") or item.get("media_type") or "AS"
                                            urls.append((v, str(label)))
            resolved = []
            for u, lbl in urls:
                if "/player/media" in u:
                    try:
                        mr = _http_get(u, session=session,
                                       extra_headers={"Accept": "*/*",
                                                      "Referer": iframe_player,
                                                      "Origin": urljoin(page_url, "/").rstrip("/")},
                                       timeout=15)
                        txt = mr.text or ""
                        if txt.lstrip().startswith("#EXTM3U"):
                            ct = mr.headers.get("Content-Type", "").lower()
                            # Master playlist: parser les RESOLUTION + variantes
                            variants = []
                            cur_label = None
                            cur_bw = None
                            lines = txt.splitlines()
                            for line in lines:
                                line = line.strip()
                                if line.startswith("#EXT-X-STREAM-INF:"):
                                    meta = line[len("#EXT-X-STREAM-INF:"):]
                                    res_m = re.search(r"""RESOLUTION=\s*(\d+)x(\d+)""", meta)
                                    bw_m = re.search(r"""BANDWIDTH=\s*(\d+)""", meta)
                                    if res_m:
                                        h = int(res_m.group(2))
                                        cur_label = f"{h}p"
                                    if bw_m:
                                        cur_bw = int(bw_m.group(1))
                                elif line and not line.startswith("#"):
                                    target = line
                                    if not target.startswith("http"):
                                        target = urljoin(str(mr.url), target)
                                    qlabel = cur_label or (
                                        f"{cur_bw // 1000}kbps" if cur_bw else "HLS"
                                    )
                                    variants.append((target, qlabel))
                                    cur_label = cur_bw = None
                            # Master M3U8 avec variantes: on ajoute chaque qualite
                            if variants:
                                for variant_url, variant_lbl in variants:
                                    resolved.append((variant_url, f"AnimeSama {variant_lbl}"))
                            else:
                                # Pas de variantes (playlist simple)
                                resolved.append((u, lbl or "AnimeSama HLS"))
                            continue
                        final = mr.url if hasattr(mr, 'url') and mr.url != u else None
                        inner_urls = _find_video_urls(mr.text)
                        if final and _fmt_from_url(final):
                            resolved.append((final, lbl or "AnimeSama HLS"))
                            continue
                        for iu in inner_urls[:3]:
                            resolved.append((iu, lbl or "AnimeSama"))
                    except Exception:
                        resolved.append((u, lbl or "AnimeSama"))
                else:
                    resolved.append((u, lbl))
            urls_out = resolved or [(u, l) for u, l in urls]
            if urls_out:
                fmts = []
                for u, lbl in urls_out[:10]:
                    fmt = _fmt_from_url(u, label_prefix=lbl)
                    if not fmt and "/player/media" in u:
                        fmt = VideoFormat(label=lbl or "AnimeSama HLS",
                                          ext="mp4", url=u, is_hls=True)
                    if fmt:
                        fmts.append(fmt)
                if fmts:
                    return fmts
        except Exception:
            pass

    # Méthode 2 : chercher m3u8/mp4 directement dans le player html
    direct = _find_video_urls(player_html)
    if direct:
        fmts = []
        for u in direct[:5]:
            fmt = _fmt_from_url(u, label_prefix="AnimeSama")
            if fmt:
                fmts.append(fmt)
        if fmts:
            return fmts

    # Méthode 3 : fallback /player/embed?t=...  (iframe externe)
    if fallback_url:
        if fallback_url.startswith("//"):
            fallback_url = "https:" + fallback_url
        elif fallback_url.startswith("/"):
            fallback_url = urljoin(page_url, fallback_url)
        try:
            fb_r = _http_get(fallback_url, session=session)
            fb_iframe = re.search(r"""<iframe[^>]+src=['"]([^'"]+)""", fb_r.text)
            if fb_iframe:
                fb_url = fb_iframe.group(1)
                if fb_url.startswith("//"):
                    fb_url = "https:" + fb_url
                elif fb_url.startswith("/"):
                    fb_url = urljoin(page_url, fb_url)
                sub = _extract_any_hosted(fb_url, session=session)
                if sub and sub.success:
                    return sub.formats
        except Exception:
            pass

    return None


def _animesama_via_lecteurs(page_url: str, html: str, session) -> Optional[List[VideoFormat]]:
    """Extrait via les iframes de lecteurs (voe/dood/uqload...) directes dans la page."""
    soup = BeautifulSoup(html, "lxml")
    fmts = []
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src") or ""
        if not src or "/player/" in src or src.startswith("blob:"):
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(page_url, src)
        if get_site_extractor(src) in ("voe", "dood", "uqload", "streamtape",
                                        "mixdrop", "vidoza", "upvid", "vidzy"):
            sub = _extract_any_hosted(src, session=session)
            if sub and sub.success:
                fmts.extend(sub.formats)
    return fmts or None


def _extract_animesama(url: str) -> VideoInfo:
    """Extracteur AnimeSama complet."""
    session = _SESSION
    try:
        r = _http_get(url, session=session)
    except Exception as e:
        return VideoInfo(success=False, source_url=url, extractor="animesama", error=str(e))

    html = r.text
    profile = _parse_window_profile(html)
    anime_info = profile.get("anime") or {}
    raw_title = (anime_info.get("name")
                 or anime_info.get("title")
                 or (BeautifulSoup(html, "lxml").title.string if BeautifulSoup(html, "lxml").title else "")
                 or "anime")
    lang = anime_info.get("lang") or ""
    label = anime_info.get("label") or ""

    # Récupérer le numéro d'épisode depuis l'URL si présent
    ep_match = re.search(r"[?&]ep=(\d+)|episode[_\s-]?(\d+)", url, re.I)
    ep_num = ep_match.group(1) or ep_match.group(2) if ep_match else ""
    episode = re.search(r"Episode\s+(\d+)", html, re.I)
    if not ep_num and episode:
        ep_num = episode.group(1)

    title_parts = [_safe_filename(raw_title)]
    if label and label.lower() != "saison1":
        title_parts.append(label)
    elif label:
        title_parts.append(label.replace("saison", "S"))
    if ep_num:
        title_parts.append(f"EP{int(ep_num):02d}")
    if lang:
        title_parts.append(lang.upper())
    title = " - ".join([p for p in title_parts if p])

    thumb = anime_info.get("img") or ""

    # Méthode 1 : API /api/player/resolve
    formats = _animesama_via_api(r.url or url, html, session)
    if not formats:
        formats = _animesama_via_lecteurs(r.url or url, html, session) or []

    return VideoInfo(
        success=bool(formats), title=title, formats=formats, thumbnail=thumb,
        source_url=url, extractor="animesama",
        error="" if formats else (
            "Aucun flux trouvé sur AnimeSama. "
            "Vérifiez que l'épisode existe et que le site est accessible."
        ),
    )


# ---------------------------------------------------------------------------
# 4) FrenchStream — pages newsid avec data-url, iframes et liens de lecteurs
# ---------------------------------------------------------------------------

def _extract_frenchstream(url: str) -> VideoInfo:
    """FrenchStream / FrenchStreaming — pages newsid avec boutons data-url et lecteurs hébergés."""
    session = _SESSION
    try:
        r = _http_get(url, session=session, extra_headers={
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        })
    except Exception as e:
        return VideoInfo(success=False, source_url=url, extractor="frenchstream", error=str(e))
    html = r.text
    soup = BeautifulSoup(html, "lxml")

    title = ""
    h1 = soup.find(["h1", "h2"], class_=re.compile(r"(title|heading|name)", re.I))
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(strip=True).strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.split("|")[0].split("-")[0].strip()
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip() or title
    title = title or "frenchstream_video"

    thumb = ""
    og_img = soup.find("meta", property="og:image")
    if not og_img:
        og_img = soup.find("meta", attrs={"name": "twitter:image"})
    if og_img and og_img.get("content"):
        thumb = og_img["content"]
        if thumb.startswith("//"):
            thumb = "https:" + thumb
        elif thumb.startswith("/"):
            thumb = urljoin(r.url or url, thumb)

    formats = []

    # PRIORITE 1 : balises avec data-url (lecteurs directs vers vidzy/voe/etc.)
    #   Exemple : <div data-url="https://vidzy.cc/embed-xxx.html">Telecharger en TRUEFRENCH</div>
    candidate_srcs = []
    for tag in soup.find_all(True, attrs={"data-url": True}):
        src = (tag.get("data-url") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(r.url or url, src)
        label_hint = (tag.get_text() or "").strip()[:80]
        candidate_srcs.append((src, label_hint))

    # PRIORITE 2 : iframes et liens href classiques (stub precedent, avec vidzy inclus maintenant)
    for src in set(
        [f.get("src") or "" for f in soup.find_all("iframe")] +
        re.findall(r"""href\s*=\s*['"]([^'"]*(?:voe|dood|uqload|streamtape|mixdrop|vidoza|upvid|vidzy)[^'"]*)""", html)
    ):
        if not src or src.startswith("about:") or src.startswith("javascript:"):
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(r.url or url, src)
        candidate_srcs.append((src, ""))

    # Appliquer extraction hosted sur les candidats uniques
    seen = set()
    _ACCENTS = str.maketrans({"à": "a", "á": "a", "â": "a", "ã": "a", "ä": "a", "å": "a",
                               "ç": "c", "è": "e", "é": "e", "ê": "e", "ë": "e", "ì": "i",
                               "í": "i", "î": "i", "ï": "i", "ð": "d", "ñ": "n", "ò": "o",
                               "ó": "o", "ô": "o", "õ": "o", "ö": "o", "ù": "u", "ú": "u",
                               "û": "u", "ü": "u", "ý": "y", "ÿ": "y", "œ": "oe", "æ": "ae",
                               "À": "A", "Á": "A", "Â": "A", "Ã": "A", "Ä": "A", "Å": "A",
                               "Ç": "C", "È": "E", "É": "E", "Ê": "E", "Ë": "E", "Ì": "I",
                               "Í": "I", "Î": "I", "Ï": "I", "Ñ": "N", "Ò": "O", "Ó": "O",
                               "Ô": "O", "Õ": "O", "Ö": "O", "Ù": "U", "Ú": "U", "Û": "U",
                               "Ü": "U", "Ý": "Y"})
    for src, hint in candidate_srcs:
        if not src or src in seen:
            continue
        seen.add(src)
        site = get_site_extractor(src)
        if site in ("voe", "dood", "uqload", "streamtape", "mixdrop", "vidoza", "upvid", "vidzy"):
            sub = _extract_any_hosted(src, session=session)
            if sub and sub.success:
                if not title or title == "frenchstream_video":
                    title = sub.title or title
                if not thumb and sub.thumbnail:
                    thumb = sub.thumbnail
                for f in sub.formats:
                    # Nettoyer le hint (supprimer caracteres non imprimables, symboles parasites)
                    clean_hint = ""
                    if hint:
                        ch = hint.replace("\xa0", " ").replace("\u00f9", "").replace("\u2014", "-").replace("—", "-")
                        ch = ch.translate(_ACCENTS)
                        ch = unicodedata.normalize("NFKD", ch)
                        ch = "".join(c for c in ch if (c.isprintable() and ord(c) >= 0x20))
                        ch = re.sub(r"\s{2,}", " ", ch).strip(" —-_\t\n")
                        # Supprimer prefixes/suffixes inutiles courants
                        ch = re.sub(r"^(?:Telecharger|Télécharger|Download|Lire|Lecteur|Regarder|Version)\s*(?:en|sur|la|le|du|de|vostfr|vff)?\s*",
                                    "", ch, flags=re.I)
                        ch = re.sub(r"\s*(?:en ligne|streaming|complet|gratuit|video|film)\s*$",
                                    "", ch, flags=re.I)
                        ch = ch.strip(" —-_\t\n")
                        if len(ch) > 60:
                            ch = ch[:60]
                        clean_hint = ch
                    if clean_hint and clean_hint.lower() not in (f.label or "").lower():
                        sep = " - "
                        new_label = f"{f.label}{sep}{clean_hint}" if f.label else clean_hint
                        f = VideoFormat(
                            label=new_label, ext=f.ext, url=f.url, size=f.size,
                            is_hls=f.is_hls,
                            extra_headers=getattr(f, "extra_headers", None),
                        )
                    formats.append(f)

    # Fallback : URLs directes dans le HTML
    if not formats:
        for u in _find_video_urls(html)[:5]:
            fmt = _fmt_from_url(u, label_prefix="FS")
            if fmt:
                formats.append(fmt)

    return VideoInfo(
        success=bool(formats), title=title, formats=formats, thumbnail=thumb,
        source_url=url, extractor="frenchstream",
        error="" if formats else (
            "Aucun flux trouve sur FrenchStream. "
            "Changez de lecteur ou verifiez que le site est accessible."
        ),
    )


# ---------------------------------------------------------------------------
# Routage des lecteurs hébergés (utilisé par AnimeSama & FrenchStream)
# ---------------------------------------------------------------------------

_HOSTED_EXTRACTORS: dict = {}   # filled ci-dessous


def _register_hosted(name: str, fn: Callable[[str], VideoInfo]):
    _HOSTED_EXTRACTORS[name] = fn


for _n, _fn in [
    ("voe", _extract_voe),
    ("dood", _extract_dood),
    ("uqload", _extract_uqload),
    ("streamtape", _extract_streamtape),
    ("mixdrop", _extract_mixdrop),
    ("vidoza", _extract_vidoza),
    ("upvid", _extract_upvid),
    ("vidzy", _extract_vidzy),
]:
    _register_hosted(_n, _fn)


def _extract_any_hosted(url: str, session=None) -> Optional[VideoInfo]:
    """Pour un lien de lecteur externe, applique l'extracteur correspondant."""
    site = get_site_extractor(url)
    fn = _HOSTED_EXTRACTORS.get(site)
    if not fn:
        return None
    try:
        return fn(url)
    except Exception as e:
        logger.exception("extract hosted %s failed", url)
        return VideoInfo(success=False, source_url=url, extractor=site or "hosted",
                         error=str(e))


# ---------------------------------------------------------------------------
# Registry principal + point d'entrée
# ---------------------------------------------------------------------------

_EXTRACTORS = {
    "youtube": _extract_youtube,
    "animesama": _extract_animesama,
    "frenchstream": _extract_frenchstream,
    **_HOSTED_EXTRACTORS,   # voe, dood, uqload, streamtape, mixdrop, vidoza, upvid
}


def extract(url: str, extra_headers=None) -> VideoInfo:
    """Point d'entrée principal."""
    if urlparse(url).path.lower().endswith(".m3u8"):
        name = os.path.splitext(os.path.basename(urlparse(url).path))[0]
        name = name or "Flux vidéo"
        info = VideoInfo(
            success=True, title=name,
            formats=[VideoFormat(label="HLS", ext="mp4", url=url, is_hls=True,
                                 extra_headers=dict(extra_headers or {}))],
            source_url=url, extractor="hls",
        )
        info.formats = _expand_hls_master(info.formats)
        return info

    site = get_site_extractor(url)
    if not site:
        return VideoInfo(
            success=False, source_url=url,
            error=f"Site non supporté pour l'extraction : {urlparse(url).netloc}",
        )

    fn = _EXTRACTORS.get(site)
    if not fn:
        return VideoInfo(
            success=False, source_url=url, extractor=site,
            error=f"Aucun extracteur implémenté pour '{site}'",
        )

    try:
        info = (_extract_youtube(url, extra_headers=extra_headers)
                if site == "youtube" else fn(url))
        if not info.extractor:
            info.extractor = site
        if not info.source_url:
            info.source_url = url
        if info.success:
            info.formats = _expand_hls_master(info.formats)
        return info
    except Exception as e:
        logger.exception("Extraction échouée pour %s", url)
        return VideoInfo(
            success=False, source_url=url, extractor=site,
            error=f"Erreur extracteur '{site}' : {e}",
        )


def is_extractable_url(url: str) -> bool:
    return (get_site_extractor(url) is not None
            or urlparse(url).path.lower().endswith(".m3u8"))
