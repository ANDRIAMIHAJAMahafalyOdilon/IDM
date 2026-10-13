"""Safe preflight metadata and filename selection for HTTP downloads."""
from dataclasses import dataclass
from email.message import Message
import mimetypes
import os
import re
from urllib.parse import unquote, urlsplit

import requests
from requests.structures import CaseInsensitiveDict


MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-7z-compressed": ".7z",
    "application/x-rar-compressed": ".rar",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v",
                    ".wmv", ".flv", ".mpeg", ".mpg", ".3gp"}
GENERIC_NAMES = {"", "download", "file", "video", "media", "untitled",
                 "index", "master", "stream", "playlist", "manifest", "default"}


@dataclass
class DownloadMetadata:
    initial_url: str
    final_url: str
    name: str
    size: int | None = None
    mime_type: str = ""
    etag: str = ""
    last_modified: str = ""
    supports_range: bool = False
    status_code: int | None = None


def clean_filename(value):
    """Keep a filename only; never accept a remote path or Windows device name."""
    value = unquote(str(value or "")).replace("\\", "/").split("/")[-1]
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    value = re.sub(r"\s+", " ", value).strip()
    if value in (".", ".."):
        return ""
    stem = value.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}:
        value = "_" + value
    stem, extension = os.path.splitext(value)
    if len(value) > 240:
        value = stem[:max(1, 240 - len(extension))] + extension
    return value


def is_generic_filename(value):
    stem = os.path.splitext(clean_filename(value))[0].lower().strip(" ._")
    return (stem in GENERIC_NAMES or
            bool(re.fullmatch(r"(?:index|anime|frenchstream_video)(?:[-_. ].*)?", stem)))


def clean_page_title(value, source_site=""):
    """Remove a known source-site suffix while keeping title separators."""
    title = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n-|–—")
    source_site = str(source_site or "").strip()
    try:
        host = (urlsplit(source_site if "://" in source_site
                         else "//" + source_site).hostname or "").lower()
    except ValueError:
        host = ""
    suffix = None
    if "anime" in host and "sama" in host:
        suffix = r"(?:anime\s*[- ]?\s*sama)"
    elif "french" in host and "stream" in host:
        suffix = r"(?:french\s*[- ]?\s*stream(?:ing)?)"
    elif host:
        labels = host.removeprefix("www.").removeprefix("m.").split(".")
        brand_index = -3 if len(labels) >= 3 and ".".join(labels[-2:]) in {
            "co.uk", "org.uk", "com.au", "net.au", "co.nz", "com.br",
            "com.mx", "co.jp", "com.sg", "co.in",
        } else -2
        brand = labels[brand_index] if len(labels) > 1 else labels[0]
        if brand and brand not in {"www", "m", "localhost"}:
            suffix = "(?:" + re.escape(brand).replace(r"\-", r"[- ]?") + ")"
    if suffix:
        # Site titles often append a slogan after their brand, e.g.
        # "Episode - Anime-Sama - Streaming et catalogage...".
        title = re.sub(rf"\s*(?:\||–|—|-|_)?\s*{suffix}\b.*$", "", title,
                       flags=re.I).strip(" \t\r\n-|–—_")
    return title


def filename_with_extension(title, extension=""):
    """Apply the actual media extension while keeping a Windows-safe title."""
    name = clean_filename(title)
    if not name:
        return ""
    extension = str(extension or "").strip()
    if extension and not extension.startswith("."):
        extension = "." + extension
    if extension:
        stem = os.path.splitext(name)[0]
        return clean_filename(stem + extension)
    return name


def browser_filename_info(data):
    """Choose a safe browser-download name and the actual output extension."""
    data = data if isinstance(data, dict) else {}
    url = str(data.get("url") or "")
    original = clean_filename(
        data.get("original_filename") or data.get("filename") or _url_name(url)) or "download"
    source_site = data.get("source_site") or ""
    preferred = ""
    for candidate in (data.get("suggested_filename"), data.get("video_title"),
                      clean_page_title(data.get("page_title"), source_site)):
        candidate = clean_filename(candidate) if candidate else ""
        if candidate and not is_generic_filename(candidate):
            preferred = candidate
            break

    format_ext = str(data.get("format") or "").strip().lower().lstrip(".")
    allowed = {"mp4", "webm", "mkv", "avi", "mov", "m4v", "wmv", "flv",
               "mpeg", "mpg", "3gp"}
    if format_ext not in allowed:
        format_ext = ""
    try:
        is_hls = urlsplit(url).path.lower().endswith(".m3u8")
    except ValueError:
        is_hls = False

    # A playlist is an input manifest; HlsDownloader assembles its media into
    # an MP4 file. Never retain .m3u8 as the final file extension.
    extension = ".mp4" if is_hls else (
        "." + format_ext if format_ext else os.path.splitext(original)[1])
    if preferred:
        name = filename_with_extension(preferred, extension)
    elif extension:
        name = filename_with_extension(original, extension)
    else:
        name = original
    return {"original_name": original, "preferred_title": preferred,
            "format_ext": format_ext, "extension": extension, "name": name}


def _disposition_name(header):
    if not header:
        return ""
    message = Message()
    message["Content-Disposition"] = header
    return clean_filename(message.get_filename())


def _url_name(url):
    try:
        candidate = unquote(os.path.basename(urlsplit(url).path))
    except (TypeError, ValueError):
        return ""
    return clean_filename(candidate)


def _mime_extension(mime_type):
    mime_type = (mime_type or "").split(";", 1)[0].strip().lower()
    if mime_type in MIME_EXTENSIONS:
        return MIME_EXTENSIONS[mime_type]
    if mime_type.startswith(("text/html", "application/octet-stream")):
        return ""
    return mimetypes.guess_extension(mime_type, strict=False) or ""


def _size_from_headers(headers):
    content_range = headers.get("Content-Range", "")
    if "/" in content_range:
        value = content_range.rsplit("/", 1)[1]
        if value.isdigit():
            return int(value)
    value = headers.get("Content-Length", "")
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _make_metadata(initial_url, response, explicit_name=""):
    headers = response.headers
    mime_type = (headers.get("Content-Type", "").split(";", 1)[0].strip().lower())
    final_url = response.url or initial_url
    disposition_name = _disposition_name(headers.get("Content-Disposition"))
    final_url_name = _url_name(final_url)
    initial_url_name = _url_name(initial_url)
    candidates = (disposition_name, clean_filename(explicit_name), final_url_name,
                  initial_url_name)
    name = next((n for n in candidates if n and
                 os.path.splitext(n)[0].lower() not in GENERIC_NAMES), "download")
    ext = os.path.splitext(name)[1].lower()
    if not ext:
        ext = _mime_extension(mime_type)
        if ext:
            name += ext
    cr = headers.get("Content-Range", "")
    ranges = response.status_code == 206 or "bytes" in headers.get("Accept-Ranges", "").lower()
    return DownloadMetadata(
        initial_url=initial_url,
        final_url=final_url,
        name=clean_filename(name) or "download",
        size=_size_from_headers(headers),
        mime_type=mime_type,
        etag=headers.get("ETag", ""),
        last_modified=headers.get("Last-Modified", ""),
        supports_range=ranges,
        status_code=response.status_code,
    )


def probe_download(url, headers=None, timeout=4, explicit_name=""):
    """Read response headers without downloading the resource body."""
    request_headers = dict(headers or {})
    response = None
    try:
        response = requests.head(url, headers=request_headers, allow_redirects=True,
                                timeout=timeout)
        if response.status_code in (405, 501) or not response.headers:
            response.close()
            response = None
    except requests.RequestException:
        if response is not None:
            response.close()
        response = None

    head_response = response
    probe_range = response is None or not response.headers.get("Accept-Ranges")
    if probe_range:
        try:
            range_headers = dict(request_headers)
            range_headers["Range"] = "bytes=0-0"
            range_response = requests.get(url, headers=range_headers, stream=True,
                                          allow_redirects=True, timeout=timeout)
        except requests.RequestException:
            if head_response is not None:
                head_response.close()
            return DownloadMetadata(url, url, clean_filename(explicit_name) or
                                    _url_name(url) or "download")
        if head_response is not None:
            headers = dict(head_response.headers)
            headers.update(range_response.headers)
            range_response.headers = CaseInsensitiveDict(headers)
            range_response.url = range_response.url or head_response.url
            head_response.close()
        response = range_response
    try:
        return _make_metadata(url, response, explicit_name)
    finally:
        response.close()
