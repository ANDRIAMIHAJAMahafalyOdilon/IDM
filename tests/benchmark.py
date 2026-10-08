import argparse
import csv
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.downloader import Downloader


def fr(x):
    return f"{x:.2f}".replace(".", ",")   # pour Excel en français


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000/test.bin")
    ap.add_argument("--threads", default="1,2,4,8")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--csv", default="resultats.csv")
    args = ap.parse_args()

    counts = [int(x) for x in args.threads.split(",")]
    rows = []
    size = 0

    with tempfile.TemporaryDirectory() as tmp:
        for n in counts:
            times = []
            for k in range(args.repeat):
                dest = os.path.join(tmp, "bench.bin")
                dl = Downloader(args.url, dest, n_threads=n)
                t0 = time.perf_counter()
                dl.run()
                dt = time.perf_counter() - t0
                size = dl.total
                times.append(dt)
                print(f"  {n} thread(s), essai {k + 1}/{args.repeat} : {dt:.1f}s")
                os.remove(dest)
            rows.append((n, statistics.mean(times),
                         statistics.pstdev(times) if len(times) > 1 else 0.0))

    base = rows[0][1]
    print(f"\n{'Threads':>8} {'Temps moy.':>11} {'Écart-type':>11} {'Vitesse':>10} {'Gain':>6}")
    out = []
    for n, mean, std in rows:
        speed = size / mean / 1024 / 1024
        gain = base / mean
        print(f"{n:>8} {mean:>10.1f}s {std:>10.1f}s {speed:>7.2f} Mo/s {gain:>5.1f}x")
        out.append([n, fr(mean), fr(std), fr(speed), fr(gain)])

    with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Threads", "Temps moyen (s)", "Ecart-type (s)",
                    "Vitesse (Mo/s)", "Gain vs 1er"])
        w.writerows(out)
    print(f"\nRésultats enregistrés dans {args.csv} (ouvrable dans Excel)")


if __name__ == "__main__":
    main()