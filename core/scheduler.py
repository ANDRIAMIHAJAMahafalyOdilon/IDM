from datetime import datetime, timedelta


def parse_start(text):
    """'02:00' -> prochaine occurrence de 02:00 ; '2026-10-09 02:00' -> date précise.
    Renvoie un timestamp (secondes)."""
    text = text.strip()
    now = datetime.now()
    try:
        t = datetime.strptime(text, "%H:%M")
        dt = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt.timestamp()
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        raise ValueError("Format d'heure invalide. Utilise HH:MM ou 'AAAA-MM-JJ HH:MM'")