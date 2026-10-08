import argparse
import os
import sys
import time
from datetime import datetime

from core.downloader import Downloader, filename_from_url
from core.database import Database
from core.queue_manager import QueueManager
from core.rate_limiter import RateLimiter
from core.scheduler import parse_start


def mo(n):
    return n / 1024 / 1024


def make_limiter(args):
    return RateLimiter(int(args.limit * 1024 * 1024)) if args.limit else None


def run_single(args):
    out = args.out or "."
    os.makedirs(out, exist_ok=True)
    dest = os.path.join(out, filename_from_url(args.url))
    dl = Downloader(args.url, dest, args.t or args.threads, args.sha256,
                    limiter=make_limiter(args))

    t0 = time.time()
    dl.start()
    last_t, last_b = t0, 0
    try:
        while dl.is_alive():
            time.sleep(0.5)
            now = time.time()
            speed = (dl.downloaded - last_b) / (now - last_t) if now > last_t else 0
            last_t, last_b = now, dl.downloaded
            pct = dl.downloaded * 100 / dl.total if dl.total else 0
            print(f"\r{dl.status:<11} {pct:5.1f}%  {mo(speed):6.2f} Mo/s", end="")
    except KeyboardInterrupt:
        print("\nPause en cours...")
        dl.pause()
        dl.join()
        print("En pause. Relance la même commande pour reprendre.")
        return 130

    dl.join()
    print()
    if dl.status == "done":
        elapsed = time.time() - t0
        print(f"Terminé en {elapsed:.1f}s -> {dest}")
        if dl.total and elapsed > 0:
            print(f"Vitesse moyenne : {mo(dl.total) / elapsed:.2f} Mo/s")
        print(f"SHA-256 : {dl.sha256}")
        return 0
    if dl.status == "paused":
        print("En pause. Relance la même commande pour reprendre.")
        return 0
    print(f"ÉCHEC : {dl.error}")
    if "429" in str(dl.error) or "503" in str(dl.error):
        print("Le serveur limite les connexions : essaie avec moins de threads.")
    return 1


def run_queue(args, urls, start_at=None):
    if not urls:
        print("Aucun lien.")
        return 1

    db = Database()
    qm = QueueManager(db, args.out or "Downloads", args.parallel,
                      args.t or args.threads, limiter=make_limiter(args))
    for u in urls:
        qm.add(u, start_at)
    print(f"{len(urls)} fichier(s) en file, {args.parallel} en parallèle max.")
    if start_at:
        print("Démarrage prévu à "
              f"{datetime.fromtimestamp(start_at):%Y-%m-%d %H:%M}.")
    if args.limit:
        print(f"Débit total limité à {args.limit} Mo/s.")
    print()

    try:
        while not qm.all_finished():
            time.sleep(2)
            for s in qm.snapshot():
                extra = ""
                if s["state"] == "scheduled":
                    extra = f"  (à {datetime.fromtimestamp(s['start_at']):%H:%M})"
                print(f"{s['state']:<9} {s['percent']:5.1f}%  "
                      f"{s['name']}  [{s['category']}]{extra}")
            print("-" * 40)
    except KeyboardInterrupt:
        print("\nPause de tous les téléchargements...")
        qm.shutdown()
        print("Relance la même commande pour reprendre.")
        return 130

    qm.shutdown()
    print("\nRésumé :")
    for s in qm.snapshot():
        extra = f"  ({s['error']})" if s["error"] else ""
        print(f"  {s['state']:<6} {s['name']}  -> {s['category']}{extra}")
    return 0


def show_history():
    rows = Database().history()
    if not rows:
        print("Historique vide.")
        return 0
    for r in rows:
        size = f"{mo(r['size']):.1f} Mo" if r["size"] else "?"
        print(f"#{r['id']:<3} {r['status']:<11} {r['name']:<25} "
              f"{r['category']:<10} {size:>9}  {r['finished_at'] or r['added_at']}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="FlashDL - gestionnaire de téléchargement")
    ap.add_argument("url", nargs="?")
    ap.add_argument("threads", nargs="?", type=int, default=8)
    ap.add_argument("-t", type=int, help="threads par fichier")
    ap.add_argument("-o", "--out", help="dossier de destination")
    ap.add_argument("--sha256", help="hash attendu (vérification d'intégrité)")
    ap.add_argument("--queue", help="fichier texte avec un lien par ligne")
    ap.add_argument("--parallel", type=int, default=3,
                    help="téléchargements simultanés max (mode file)")
    ap.add_argument("--limit", type=float,
                    help="débit total maximum en Mo/s (ex: 2 ou 0.5)")
    ap.add_argument("--at", help="heure de démarrage : HH:MM ou 'AAAA-MM-JJ HH:MM'")
    ap.add_argument("--history", action="store_true", help="affiche l'historique")
    ap.add_argument("--gui", action="store_true", help="lance l'interface graphique")
    args = ap.parse_args()

    if args.gui:
        from gui.main_window import run_gui
        return run_gui()

    if args.history:
        return show_history()

    start_at = None
    if args.at:
        try:
            start_at = parse_start(args.at)
        except ValueError as e:
            print(e)
            return 1

    if args.queue:
        with open(args.queue, encoding="utf-8-sig") as f:
            urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        return run_queue(args, urls, start_at)
    if args.url:
        if start_at:
            return run_queue(args, [args.url], start_at)
        return run_single(args)

    from gui.main_window import run_gui    # sans argument : ouvre l'interface
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())