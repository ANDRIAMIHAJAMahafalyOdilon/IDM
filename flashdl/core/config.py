"""
Configuration des headers HTTP et sites supportés pour FlashDL
"""

import random

# Headers par défaut pour les requêtes
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0",
    "Accept": "*/*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}

# Headers spécifiques par site
SITE_HEADERS = {
    "youtube.com": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Cookie": "CONSENT=YES+cb.20210328-17-p0.fr+FX+{}".format(random.randint(100, 999)),
    },
    "animes-sama.fr": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Referer": "https://animes-sama.fr/",
    },
    "anime-sama.world": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    },
    "french-stream.re": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://french-stream.re/",
    },
    "frenchstreaming.video": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
    "french-stream.one": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
    "fs20.lol": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
    "voe.sx": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Referer": "https://voe.sx/",
    },
    "dood.pm": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://dood.pm/",
    },
    "dood.watch": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://dood.watch/",
    },
    "dood.so": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://dood.so/",
    },
    "uqload.net": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://uqload.net/",
    },
    "uqload.com": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://uqload.com/",
    },
    "uqload.io": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://uqload.io/",
    },
    "streamtape.com": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://streamtape.com/",
    },
    "streamtape.net": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://streamtape.net/",
    },
    "streamtape.to": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://streamtape.to/",
    },
    "mixdrop.co": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://mixdrop.co/",
    },
    "vidoza.net": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://vidoza.net/",
    },
    "upvid.co": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://upvid.co/",
    },
    "vidzy.cc": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Referer": "https://vidzy.cc/",
    },
    "vidzy.org": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://vidzy.org/",
    },
    "vidzy.live": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://vidzy.live/",
    },
    "fs13.lol": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
    "french-stream.pink": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
    "fstream.info": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
}

# Patterns pour détecter les URLs de flux vidéo
VIDEO_PATTERNS = {
    "mp4": [r"https?://[^\s\"']+\.mp4(?:\?[^\s\"']*)?"],
    "webm": [r"https?://[^\s\"']+\.webm(?:\?[^\s\"']*)?"],
    "m3u8": [r"https?://[^\s\"']+\.m3u8(?:\?[^\s\"']*)?"],
    "dash": [r"https?://[^\s\"']+\.mpd(?:\?[^\s\"']*)?"],
    "ts": [r"https?://[^\s\"']+\.ts(?:\?[^\s\"']*)?"],
}

# Sites avec extracteurs spécifiques
SUPPORTED_SITES = {
    "animes-sama.fr": "animesama",
    "animesama.fr": "animesama",
    "anime-sama.world": "animesama",
    "animesama.mov": "animesama",
    "animeesama.world": "animesama",
    "french-stream.re": "frenchstream", 
    "french-stream.co": "frenchstream",
    "frenchstreaming.video": "frenchstream",
    "french-stream.one": "frenchstream",
    "fs20.lol": "frenchstream",
    "fs13.lol": "frenchstream",
    "french-stream.pink": "frenchstream",
    "fstream.info": "frenchstream",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "voe.sx": "voe",
    "voeunblock.com": "voe",
    "dood.pm": "dood",
    "dood.watch": "dood",
    "dood.so": "dood",
    "dood.cx": "dood",
    "dood.la": "dood",
    "uqload.net": "uqload",
    "uqload.com": "uqload",
    "uqload.io": "uqload",
    "uqload.co": "uqload",
    "streamtape.com": "streamtape",
    "streamtape.net": "streamtape",
    "streamtape.to": "streamtape",
    "streamtape.xyz": "streamtape",
    "mixdrop.co": "mixdrop",
    "mixdrop.to": "mixdrop",
    "mixdrop.bz": "mixdrop",
    "vidoza.net": "vidoza",
    "vidoza.org": "vidoza",
    "vidoza.in": "vidoza",
    "upvid.co": "upvid",
    "upvid.lol": "upvid",
    "vidzy.cc": "vidzy",
    "vidzy.org": "vidzy",
    "vidzy.live": "vidzy",
    "vidzy.lol": "vidzy",
}


def get_headers_for_url(url: str, extra_headers: dict = None) -> dict:
    """
    Retourne les headers appropriés pour une URL donnée
    """
    headers = DEFAULT_HEADERS.copy()
    
    # Détecter le site et appliquer ses headers spécifiques
    for domain, site_headers in SITE_HEADERS.items():
        if domain in url.lower():
            headers.update(site_headers)
            break
    
    # Fusionner avec les headers supplémentaires
    if extra_headers:
        headers.update(extra_headers)
    
    return headers


def get_site_extractor(url: str) -> str:
    """
    Retourne le nom de l'extracteur pour un site donné
    """
    url_lower = url.lower()
    for domain, extractor in SUPPORTED_SITES.items():
        if domain in url_lower:
            return extractor
    return None