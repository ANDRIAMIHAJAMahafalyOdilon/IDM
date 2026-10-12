"""
Test E2E final : FrenchStream → Vidzy → 3 segments téléchargés.
Confirme que tout fonctionne de bout en bout.
"""
import os, sys, tempfile, time
sys.path.insert(0, ".")
from core.extractors import extract
from core.config import get_headers_for_url
from core.hls import HlsDownloader

print("=" * 60)
print("TEST E2E FINAL : FrenchStream + Vidzy + HLS segments")
print("=" * 60)

t0 = time.time()
print("\n[1] Extraction FrenchStream...")
info = extract("https://frenchstreaming.video/index.php?newsid=15126684")
print(f"    success={info.success}, title={info.title!r}")
print(f"    formats: {[(f.label, f.is_hls) for f in info.formats]}")

fmt = info.best_format()
print(f"\n[2] Format: {fmt.label}")
print(f"    url[:80]: {fmt.url[:80]}")
print(f"    extra_headers: Referer={fmt.extra_headers.get('Referer', '?')!r}")

headers = get_headers_for_url(fmt.url)
if fmt.extra_headers:
    headers.update(fmt.extra_headers)

tmp = tempfile.mkdtemp(prefix="hls_e2e_")
dest = os.path.join(tmp, "film.mp4")
dl = HlsDownloader(fmt.url, dest, n_threads=1, headers=headers)

print(f"\n[3] Résolution playlist...")
items = dl._resolve()
print(f"    {len(items)} segments")
print(f"    seg[0]: {items[0][:100]}")

print(f"\n[4] Téléchargement des 3 premiers segments...")
dl._n = len(items)
os.makedirs(dest + ".hls", exist_ok=True)
total_size = 0
for i in range(min(3, len(items))):
    dl._fetch(i, items[i])
    seg = dl._seg_path(i)
    sz = os.path.getsize(seg)
    total_size += sz
    print(f"    seg[{i}] size={sz:,} bytes")

elapsed = time.time() - t0
print(f"\n[5] Résultat:")
print(f"    Total téléchargé: {total_size:,} bytes")
print(f"    Temps: {elapsed:.1f}s")

if total_size > 30000:
    print(f"\n{'='*60}")
    print("SUCCES COMPLET ! FrenchStream + Vidzy + HLS fonctionne !")
    print("Pipeline: extraction -> XOR decode -> curl_cffi -> segments")
    print(f"{'='*60}")
else:
    print("ATTENTION: segments trop petits")
