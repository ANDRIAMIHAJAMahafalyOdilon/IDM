import os

DEFAULT = "Général"
DEFAULT_CATEGORIES = (DEFAULT, "Compressé", "Vidéo", "Musique", "Documents",
                      "Programmes", "Images", "Autres")
CATEGORIES = {
    "Vidéo": {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".flv",
              ".webm", ".mpeg", ".mpg", ".3gp"},
    "Musique": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".opus"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                  ".txt", ".odt", ".epub", ".rtf", ".csv"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
               ".tif", ".tiff", ".avif"},
    "Compressé": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
                  ".tgz"},
    "Programmes": {".exe", ".msi", ".apk", ".dmg", ".iso", ".deb", ".rpm"},
}


def category_for(filename, mime_type="", custom_categories=None):
    ext = os.path.splitext(filename)[1].lower()
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    mime_type = (mime_type or "").lower()
    for cat, prefix in (("Vidéo", "video/"), ("Musique", "audio/"),
                        ("Images", "image/"), ("Documents", "application/pdf"),
                        ("Compressé", "application/zip")):
        if mime_type.startswith(prefix):
            return cat
    return DEFAULT


def destination_folder(filename, base_dir, category=None, preferences=None):
    """Renvoie (dossier, catégorie) et crée le dossier si besoin."""
    cat = category or category_for(filename)
    prefs = preferences or {}
    folder = prefs.get(cat) or os.path.join(base_dir, cat)
    return folder, cat
