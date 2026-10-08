import os

CATEGORIES = {
    "Vidéos":     {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"},
    "Musique":    {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
    "Documents":  {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                   ".txt", ".odt", ".epub"},
    "Images":     {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"},
    "Archives":   {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Programmes": {".exe", ".msi", ".apk", ".dmg", ".iso"},
}
DEFAULT = "Autres"


def category_for(filename):
    ext = os.path.splitext(filename)[1].lower()
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return DEFAULT


def destination_folder(filename, base_dir):
    """Renvoie (dossier, catégorie) et crée le dossier si besoin."""
    cat = category_for(filename)
    folder = os.path.join(base_dir, cat)
    os.makedirs(folder, exist_ok=True)
    return folder, cat