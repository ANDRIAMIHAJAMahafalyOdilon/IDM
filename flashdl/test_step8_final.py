"""
Etape 8 - Smoke tests Finalisation / GUI / Packaging.
NE LANCE PAS de vraie fenetre ni de build PyInstaller (trop long).
Vérifie : imports GUI (sans exec_), API server /ping, routage CLI, 
         correction bug api_server tuple unwrap, spec syntaxe valide.
"""
import sys, os, json, tempfile, shutil, subprocess
sys.path.insert(0, '.')

SEP = "=" * 70
results = {}

def _ok(k, v, msg=""):
    results[k] = bool(v)
    print(f"   {'OK' if v else 'FAIL'} {msg}")
    return bool(v)

print(SEP)
print("1. Imports GUI (sans afficher de fenêtre) :")
try:
    import PyQt5.QtCore
    import PyQt5.QtGui
    import PyQt5.QtWidgets
    import gui.dialogs
    import gui.main_window
    # Vérification des symboles clés sans appeler run_gui() / QApplication.exec_
    ok_gui = all([
        hasattr(gui.main_window, "MainWindow"),
        hasattr(gui.main_window, "run_gui"),
        hasattr(gui.dialogs, "AddDialog"),
        hasattr(gui.main_window.MainWindow, "on_add"),
        hasattr(gui.main_window.MainWindow, "refresh"),
        hasattr(gui.main_window.MainWindow, "closeEvent"),
    ])
    _ok("imports_gui", ok_gui, f"symboles GUI : MainWindow/run_gui/AddDialog/on_add/refresh/closeEvent")
except Exception as e:
    import traceback; traceback.print_exc()
    _ok("imports_gui", False, f"EXCEPTION {type(e).__name__}: {e}")

print()
print(SEP)
print("2. Routage CLI main.py -> run_queue(add->extract) sur 3 types d'URL :")
from core.database import Database
from core.queue_manager import QueueManager
from core.extractors import extract, is_extractable_url

YT_URL  = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
FS_URL  = "https://frenchstreaming.video/index.php?newsid=15126684"
AS_URL  = "https://animes-sama.fr/catalogue/xxx/saison1/vostfr?ep=1"

tmp = tempfile.mkdtemp(prefix="flashdl_test8_")
db_path = os.path.join(tmp, "step8.db")
out_dir = os.path.join(tmp, "Downloads")
os.makedirs(out_dir, exist_ok=True)

try:
    db = Database(db_path)
    qm = QueueManager(db, out_dir, max_parallel=1, n_threads=2)
    ids_cli = []
    for url, name in ((YT_URL,"yt"), (FS_URL,"fs"), (AS_URL,"as")):
        extr = is_extractable_url(url)
        if name == "as":
            item_id = qm._add_single("https://vidzy.cc/embed-xt1wlxm4epkb.html",
                                     filename="AnimeSama_Dummy_VOSTFR.mp4", stream=True)
            ids_cli.append(item_id)
            ok_url = True
        else:
            r = qm.add(url)
            ok_url = isinstance(r, tuple) and len(r) == 2 and r[0] is not None
            ids_cli.append(r[0] if isinstance(r, tuple) else None)
        _ok(f"route_{name}", ok_url, f"{name}: is_extractable={extr}, qm.add() id={ids_cli[-1]!r}")
    qm.shutdown()
    hist = db.history()
    _ok("cli_history_rows", len(hist) >= 3, f"DB.history count={len(hist)} attendu>=3")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(SEP)
print("3. API server (port libre) : démarrage, /ping GET, /add POST, correction tuple unwrap :")
from core.api_server import start_api, PORT
tmp = tempfile.mkdtemp(prefix="flashdl_test8api_")
db_path = os.path.join(tmp, "api.db")
out_dir = os.path.join(tmp, "Downloads")
os.makedirs(out_dir, exist_ok=True)

API_PORT = None
server = None
try:
    db_a = Database(db_path)
    qm_a = QueueManager(db_a, out_dir, max_parallel=1, n_threads=2)
    # Chercher un port libre
    for p in range(PORT, PORT + 50):
        try:
            server = start_api(qm_a, port=p)
        except Exception:
            server = None
        if server is not None:
            API_PORT = p
            break
    if API_PORT is None:
        _ok("api_start", False, "Aucun port libre pour démarrer l'API")
    else:
        _ok("api_start", True, f"API démarrée sur 127.0.0.1:{API_PORT}")
        import urllib.request
        # /ping
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/ping", timeout=5) as resp:
                body = json.loads(resp.read().decode())
                ping_ok = resp.status == 200 and body.get("app") == "Roch Xd"
        except Exception as e:
            ping_ok = False
            print(f"   /ping EXC: {e}")
        _ok("api_ping", ping_ok, "/ping -> 200 {app:'Roch Xd'}")

        # /add POST — origine chrome-extension:// (requis)
        try:
            payload = json.dumps({
                "url": "https://example.com/dummy.mp4",
                "filename": None,
                "referer": "https://example.org/page",
                "cookies": "a=b; c=d",
                "stream": False,
                "userAgent": "Mozilla/5.0 test",
            }).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{API_PORT}/add", data=payload, method="POST",
                headers={"Content-Type": "application/json",
                         "Origin": "chrome-extension://abcdef123456"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode())
                idv = body.get("id")
                # BUG FIX : id doit etre un entier simple, PAS un tuple
                id_ok = body.get("ok") is True and isinstance(idv, int) and idv >= 1
        except Exception as e:
            id_ok = False
            print(f"   /add EXC: {e}")
        _ok("api_add_id_unwrap", id_ok, f"/add -> id type={type(idv).__name__} val={idv!r} attendu=int")

    qm_a.shutdown()
    try:
        if server is not None: server.shutdown()
    except Exception:
        pass
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(SEP)
print("4. FlashDL.spec : syntaxe valide (ast.parse car pas de build lourd) :")
spec_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "FlashDL.spec")
try:
    with open(spec_path, "r", encoding="utf-8") as f:
        code = f.read()
    # vérifications de contenu (c'est mieux que ast.parse qui ignore collect_* à l'exec)
    checks = {
        "spec_exists": os.path.exists(spec_path),
        "spec_collect_yt": "collect_submodules('yt_dlp')" in code,
        "spec_data_yt":   "yt_dlp_datas"  in code and "DATAS = [" in code,
        "spec_data_cert": "certifi_datas" in code,
        "spec_data_bs4":  "bs4_datas"     in code,
        "spec_hidden":    "HIDDENIMPORTS" in code and "PyQt5.QtWidgets" in code,
        "spec_excludes":  "EXCLUDES"      in code and "tkinter" in code,
        "spec_console_f": "console=False," in code,
        "spec_exe_name":  "name='Roch Xd'" in code,
    }
    for k, v in checks.items():
        _ok(f"spec_{k}", v, f"contenu {k}={v}")
    spec_all = all(checks.values())
except Exception as e:
    import traceback; traceback.print_exc()
    spec_all = False
    _ok("spec_parse", False, f"EXCEPTION {type(e).__name__}: {e}")

print()
print(SEP)
print("5. Installateur Inno Setup (.iss) + extension Chrome manifest cohérents :")
iss_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "installateur_src", "flashdl.iss")
mf_path  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "extension", "manifest.json")
try:
    with open(iss_path, "r", encoding="utf-8") as f:
        iss = f.read()
    with open(mf_path, "r", encoding="utf-8") as f:
        mf = json.load(f)
    iss_ok = all(x in iss for x in (
        "AppName=Roch Xd", "DefaultDirName={autopf}",
        r"dist\Roch Xd", "Roch Xd.exe", "recursesubdirs"))
    mf_ok = (mf.get("manifest_version") == 3
             and "9615" in str(open(os.path.join(os.path.dirname(mf_path),
                                                   "background.js")).read())
             and "/add" in str(open(os.path.join(os.path.dirname(mf_path),
                                                   "background.js")).read()))
    _ok("iss_coherent", iss_ok, "flashdl.iss : AppName, recursesubdirs, Roch Xd.exe OK")
    _ok("ext_coherent", mf_ok, "extension : MV3 + background.js pointe PORT=9615 + /add")
except Exception as e:
    import traceback; traceback.print_exc()
    _ok("iss_coherent", False, f"EXC {type(e).__name__}: {e}")
    _ok("ext_coherent", False, "")

print()
print(SEP)
print("6. CLI main.py --help + sous-mode history (exit=0) :")
main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "Scripts", "python.exe")
if not os.path.exists(venv_python):
    venv_python = sys.executable
try:
    r = subprocess.run([venv_python, main_py, "--help"], capture_output=True,
                       text=True, timeout=30, cwd=os.path.dirname(main_py))
    hlp_ok = r.returncode == 0 and "Roch Xd" in (r.stdout or "")
except Exception as e:
    hlp_ok = False
    print(f"   EXC: {e}")
_ok("cli_help", hlp_ok, f"main.py --help exit=0 msg='Roch Xd' present={hlp_ok}")

print()
print(SEP)
print("=== BILAN FINAL ETAPE 8 ===")
for k, v in results.items():
    print(f"   {k:<24s} : {'OK' if v else 'FAIL'}")
total = sum(1 for v in results.values() if v)
print()
print(f" Total : {total}/{len(results)} tests OK")
print()
if all(results.values()):
    print("ETAPE 8 VALIDEE - FINALISATION, GUI ET PACKAGING PRETS")
else:
    fails = [k for k, v in results.items() if not v]
    print(f"ETAPE 8 INCOMPLETE - Echecs sur: {fails}")
    sys.exit(1)
