# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

yt_dlp_hidden = collect_submodules('yt_dlp')
yt_dlp_datas  = collect_data_files('yt_dlp', include_py_files=False)
certifi_datas = collect_data_files('certifi',   include_py_files=False)
bs4_datas     = collect_data_files('bs4',       include_py_files=False)

HIDDENIMPORTS = [
    *yt_dlp_hidden,
    'yt_dlp.compat._legacy',
    'yt_dlp.utils._legacy',
    'yt_dlp.utils.traversal',
    'certifi',
    'lxml',
    'lxml.etree',
    'lxml.html',
    'lxml.html.soupparser',
    'lxml.html.builder',
    'bs4',
    'bs4.builder',
    'bs4.builder._lxml',
    'bs4.builder._html5lib',
    'cryptography',
    'cryptography.fernet',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'PyQt5.QtNetwork',
    'PyQt5.QtWebSockets',
    'PyQt5.QtSvg',
    'PyQt5.QtQml',
    'PyQt5.QtQmlModels',
    'PyQt5.QtQuick',
]

EXCLUDES = [
    'tkinter',
    'unittest',
    'pydoc',
    'doctest',
    'idlelib',
    'lib2to3',
    'test',
    'tests',
    'IPython',
    'jupyter',
    'notebook',
    'matplotlib',
    'numpy',
    'pandas',
    'scipy',
    'sympy',
]

DATAS = [
    *yt_dlp_datas,
    *certifi_datas,
    *bs4_datas,
]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FlashDL',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FlashDL',
)
