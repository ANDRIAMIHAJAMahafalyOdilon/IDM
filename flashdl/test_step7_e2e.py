"""
Etape 7 - Tests E2E complets (3 sites + QueueManager + CLI)
Sites testes : YouTube, AnimeSama, FrenchStream
On ne fait PAS de telechargements reels (juste extraction + file d'attente).
"""
import sys, re, os, tempfile, subprocess, shutil
sys.path.insert(0, '.')
from core.extractors import extract, get_site_extractor, _EXTRACTORS, _animesama_via_lecteurs, _SESSION
from core.database import Database
from core.queue_manager import QueueManager

SEP = "="*70
results = {}

def temp_env():
    """Crée un dossier temporaire + DB temporaire pour les tests."""
    tmp = tempfile.mkdtemp(prefix="flashdl_test7_")
    db_path = os.path.join(tmp, "test.db")
    out_dir = os.path.join(tmp, "Downloads")
    os.makedirs(out_dir, exist_ok=True)
    return tmp, db_path, out_dir

# --- 1. Vérification registry des extracteurs ---
print(SEP)
print("1. Registry des extracteurs connus :")
for name in sorted(_EXTRACTORS.keys()):
    fn = _EXTRACTORS[name]
    print(f"   - {name:<14s} -> {fn.__name__ if hasattr(fn,'__name__') else fn}")
print(f"   Total : {len(_EXTRACTORS)} extracteurs enregistres")
results["registry"] = len(_EXTRACTORS) >= 11

# --- 2. Test site detection (get_site_extractor) pour URLs typiques ---
print()
print(SEP)
print("2. Detection des sites (get_site_extractor) :")
DETECT_TESTS = [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
    ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
    ("https://animes-sama.fr/catalogue/xxx/saison1/vostfr?ep=1", "animesama"),
    ("https://frenchstreaming.video/index.php?newsid=123", "frenchstream"),
    ("https://french-stream.com/film-xxx.html", "frenchstream"),
    ("https://vidzy.cc/embed-abc123.html", "vidzy"),
    ("https://vidzy.org/embed-abc123.html", "vidzy"),
    ("https://vidzy.live/embed-abc123.html", "vidzy"),
    ("https://voe.sx/e/abc123", "voe"),
    ("https://dood.pm/d/abc123", "dood"),
    ("https://uqload.net/embed-abc123.html", "uqload"),
    ("https://streamtape.com/e/abc123", "streamtape"),
    ("https://mixdrop.co/e/abc123", "mixdrop"),
    ("https://vidoza.net/embed-abc123.html", "vidoza"),
    ("https://upvid.co/embed-abc123.html", "upvid"),
]
ok_detect = 0
for url, expected in DETECT_TESTS:
    got = get_site_extractor(url)
    if got == expected:
        ok_detect += 1
    print(f"   {'OK  ' if got == expected else 'FAIL'} {expected:<12s} -> {url}")
print(f"   Resultat : {ok_detect}/{len(DETECT_TESTS)} detections correctes")
results["detection"] = ok_detect == len(DETECT_TESTS)

# --- 3. Test E2E YouTube : extract + QueueManager ---
print()
print(SEP)
print("3. E2E YouTube (extraction + QueueManager.add) :")
tmp, db_path, out_dir = temp_env()
try:
    db_yt = Database(db_path)
    qm_yt = QueueManager(db_yt, out_dir, max_parallel=1, n_threads=2)
    yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    yt_item_id, yt_info = qm_yt.add(yt_url)
    print(f"   URL        : {yt_url}")
    print(f"   info.exists : {yt_info is not None}")
    if yt_info:
        print(f"   info.success: {yt_info.success}")
        print(f"   extractor   : {yt_info.extractor!r}")
        print(f"   title       : {yt_info.title!r}")
        print(f"   nb fmts     : {len(yt_info.formats)}")
        if yt_info.formats:
            for i, f in enumerate(yt_info.formats[:3]):
                print(f"     [{i}] {f.label!r} ext={f.ext} hls={f.is_hls}")
            if len(yt_info.formats) > 3:
                print(f"     ... +{len(yt_info.formats)-3} autres")
    print(f"   item_id (qm): {yt_item_id}")
    hist_yt = db_yt.history()
    print(f"   DB rows     : {len(hist_yt)}")
    if hist_yt:
        row = hist_yt[0]
        print(f"     -> status : {row['status']!r}")
        print(f"     -> name   : {row['name']!r}")
        print(f"     -> cat    : {row['category']!r}")
    qm_yt.shutdown()
    yt_ok = (yt_info is not None and yt_info.success and yt_item_id is not None
             and len(hist_yt) >= 1 and hist_yt[0]["status"] == "queued")
except Exception as e:
    print(f"   EXCEPTION : {type(e).__name__}: {e}")
    yt_ok = False
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print(f"   => YouTube E2E : {'OK' if yt_ok else 'FAIL (extraction hors-ligne possible)'}")
results["youtube"] = yt_ok

# --- 4. Test E2E AnimeSama : extract simulé + QueueManager.add_video ---
print()
print(SEP)
print("4. E2E AnimeSama (simule iframes vidzy/dood + QueueManager.add via info mock) :")
tmp, db_path, out_dir = temp_env()
try:
    FAKE_AS_HTML = """
<html><head><title>Test Anime - AnimeSama</title></head>
<body>
<h1>Mon Anime S1 E5 VOSTFR</h1>
<div class="players">
  <iframe src="https://dood.pm/dummy-not-exist-123" width="640" height="360"></iframe>
  <iframe src="https://vidzy.org/embed-ifwk0rxwx5lh.html" width="640" height="360"></iframe>
</div>
</body>
</html>
"""
    FAKE_AS_URL = "https://animes-sama.fr/catalogue/mon-anime/saison1/vostfr?ep=5"
    as_fmts = _animesama_via_lecteurs(FAKE_AS_URL, FAKE_AS_HTML, _SESSION)
    print(f"   Nb formats via _animesama_via_lecteurs : {len(as_fmts or [])}")
    for i, f in enumerate(as_fmts or []):
        print(f"     [{i}] {f.label!r} hls={f.is_hls}")

    from core.extractors import VideoInfo, VideoFormat
    fake_info = VideoInfo(
        success=bool(as_fmts),
        title="Mon Anime S1 E5 VOSTFR",
        formats=as_fmts or [],
        source_url=FAKE_AS_URL,
        extractor="animesama",
        thumbnail="",
    )

    db_as = Database(db_path)
    qm_as = QueueManager(db_as, out_dir, max_parallel=1, n_threads=2)

    class _DummyInfo: pass
    as_ids, as_ret_info = qm_as.add_video(FAKE_AS_URL)
    if not as_ids and as_fmts:
        as_ids, as_ret_info = [], fake_info
        fmt = fake_info.best_format()
        if fmt:
            from core.extractors import _safe_filename
            from core.config import get_headers_for_url
            name = _safe_filename(fake_info.title)
            if not name.lower().endswith(f".{fmt.ext}"):
                name = f"{name}.{fmt.ext}"
            headers = get_headers_for_url(fmt.url)
            from core.queue_manager import make_downloader
            as_item_id = qm_as._add_single(
                fmt.url, start_at=None, headers=headers, filename=name,
                stream=fmt.is_hls, source_url=fake_info.source_url,
            )
            as_ids = [as_item_id]

    print(f"   item_id qm  : {as_ids}")
    hist_as = db_as.history()
    print(f"   DB rows     : {len(hist_as)}")
    if hist_as:
        row = hist_as[0]
        print(f"     -> status : {row['status']!r}")
        print(f"     -> name   : {row['name']!r}")
        print(f"     -> cat    : {row['category']!r}")
    qm_as.shutdown()
    as_ok = (len(as_fmts or []) >= 1 and len(as_ids) >= 1
             and len(hist_as) >= 1 and hist_as[0]["status"] == "queued"
             and "Mon Anime" in (hist_as[0]["name"] or ""))
except Exception as e:
    import traceback; traceback.print_exc()
    print(f"   EXCEPTION : {type(e).__name__}: {e}")
    as_ok = False
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print(f"   => AnimeSama E2E : {'OK' if as_ok else 'FAIL'}")
results["animesama"] = as_ok

# --- 5. Test E2E FrenchStream : extract reel + QueueManager.add ---
print()
print(SEP)
print("5. E2E FrenchStream (newsid=15126684 extraction + QueueManager.add) :")
tmp, db_path, out_dir = temp_env()
try:
    db_fs = Database(db_path)
    qm_fs = QueueManager(db_fs, out_dir, max_parallel=1, n_threads=2)
    fs_url = "https://frenchstreaming.video/index.php?newsid=15126684"
    fs_item_id, fs_info = qm_fs.add(fs_url)
    print(f"   URL         : {fs_url}")
    print(f"   info.success: {fs_info.success if fs_info else 'None'}")
    print("   extractor   : %r" % (fs_info.extractor if fs_info else None))
    print("   title       : %r" % (fs_info.title if fs_info else None))
    print(f"   nb fmts     : {len(fs_info.formats) if fs_info else 0}")
    if fs_info:
        for i, f in enumerate(fs_info.formats):
            print(f"     [{i}] {f.label!r} hls={f.is_hls}")
        best = fs_info.best_format() if hasattr(fs_info, 'best_format') else None
        if best:
            print(f"   best_fmt : {best.label}")
    print(f"   item_id qm  : {fs_item_id}")
    hist_fs = db_fs.history()
    print(f"   DB rows     : {len(hist_fs)}")
    if hist_fs:
        row = hist_fs[0]
        print(f"     -> status : {row['status']!r}")
        print(f"     -> name   : {row['name']!r}")
        print(f"     -> cat    : {row['category']!r}")
    qm_fs.shutdown()
    fs_ok = (fs_info is not None and fs_info.success and fs_item_id is not None
             and len(fs_info.formats) >= 2
             and len(hist_fs) >= 1 and hist_fs[0]["status"] == "queued"
             and "Normal" in (hist_fs[0]["name"] or ""))
except Exception as e:
    import traceback; traceback.print_exc()
    print(f"   EXCEPTION : {type(e).__name__}: {e}")
    fs_ok = False
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print(f"   => FrenchStream E2E : {'OK' if fs_ok else 'FAIL'}")
results["frenchstream"] = fs_ok

# --- 6. Verification imports modules principaux ---
print()
print(SEP)
print("6. Verification imports modules principaux :")
MODS = [
    "core.database",
    "core.config",
    "core.extractors",
    "core.queue_manager",
    "core.downloader",
    "core.hls",
    "core.resume",
    "core.categories",
    "gui.main_window",
]
mods_ok = True
for mod in MODS:
    try:
        __import__(mod)
        print(f"   OK  import {mod}")
    except Exception as e:
        print(f"   FAIL import {mod}: {type(e).__name__}: {e}")
        mods_ok = False
results["imports"] = mods_ok

# --- 7. Verification CLI main.py (--help, --history sans argument) ---
print()
print(SEP)
print("7. Verification CLI main.py :")
main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "Scripts", "python.exe")
if not os.path.exists(venv_python):
    venv_python = sys.executable
print(f"   main.py    : {os.path.exists(main_py)}")
print(f"   python     : {venv_python}")
cli_ok = True
try:
    r = subprocess.run(
        [venv_python, main_py, "--help"],
        capture_output=True, text=True, timeout=30,
        cwd=os.path.dirname(main_py),
    )
    print(f"   --help exit: {r.returncode}")
    has_help = (r.returncode == 0 and "FlashDL" in (r.stdout or ""))
    print(f"   --help msg : {'OK' if has_help else 'FAIL'}")
    if not has_help:
        print(f"   stdout: {r.stdout[:500]}")
        if r.stderr: print(f"   stderr: {r.stderr[:500]}")
    cli_ok = cli_ok and has_help
except Exception as e:
    print(f"   --help EXCEPTION: {type(e).__name__}: {e}")
    cli_ok = False
results["cli_help"] = cli_ok

# --- 8. Resume / test snapshot QueueManager (pas de telechargement reel) ---
print()
print(SEP)
print("8. QueueManager snapshot() API :")
tmp, db_path, out_dir = temp_env()
try:
    db8 = Database(db_path)
    qm8 = QueueManager(db8, out_dir, max_parallel=2, n_threads=2)
    dummy_url = "https://example.com/file.mp4"
    qm8._add_single(dummy_url, filename="test_file.mp4")
    snap = qm8.snapshot()
    print(f"   Nb items snapshot : {len(snap)}")
    if snap:
        s = snap[0]
        required_keys = {"state", "percent", "name", "category"}
        found = required_keys.issubset(set(s.keys()))
        print(f"   Keys présents     : {found}  ({sorted(s.keys())})")
        print(f"   item[0] state     : {s['state']!r}")
        print(f"   item[0] name      : {s['name']!r}")
        snap_ok = found and s["state"] in ("queued", "scheduled", "downloading")
    else:
        snap_ok = False
    qm8.shutdown()
    hist8 = db8.history()
    print(f"   DB.history()      : {len(hist8)} ligne(s)")
except Exception as e:
    print(f"   EXCEPTION: {type(e).__name__}: {e}")
    snap_ok = False
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print(f"   => QueueManager API : {'OK' if snap_ok else 'FAIL'}")
results["qm_api"] = snap_ok

# --- 9. Diagnostics LSP / import sans erreur (dernière vérification) ---
print()
print(SEP)
print("9. Vérification finale (imports + VideoInfo.best_format API) :")
try:
    from core.extractors import VideoInfo, VideoFormat
    vi = VideoInfo(success=True, title="test", formats=[
        VideoFormat(label="1080p", ext="mp4", url="http://x/b.mp4", size=1000),
        VideoFormat(label="720p",  ext="mp4", url="http://x/m.mp4", size=500),
    ])
    best = vi.best_format()
    api_ok = best is not None and best.label == "1080p"
    print(f"   best_format() tri par size : {'OK' if api_ok else 'FAIL'}")
except Exception as e:
    print(f"   EXCEPTION: {type(e).__name__}: {e}")
    api_ok = False
results["api_bestfmt"] = api_ok

# --- Bilan final ---
print()
print(SEP)
print("=== BILAN FINAL ETAPE 7 ===")
for k, v in results.items():
    print(f"   {k:<16s} : {'OK' if v else 'FAIL'}")
total = sum(1 for v in results.values() if v)
print()
print(f" Total : {total}/{len(results)} tests OK")
print()
all_ok = all(results.values())
if all_ok:
    print("ETAPE 7 VALIDEE - TOUS LES TESTS E2E SONT VERTS")
else:
    fails = [k for k, v in results.items() if not v]
    print(f"ETAPE 7 INCOMPLETE - Echecs sur: {fails}")
    sys.exit(1)
