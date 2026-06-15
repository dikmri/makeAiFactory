# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

# spec ファイルと同階層の .venv を基準にする (SPECPATH は PyInstaller が設定する)
_site_pkgs = Path(SPECPATH) / '.venv' / 'Lib' / 'site-packages'

datas = [('app/manifest', 'app/manifest'), ('app/workflow', 'app/workflow')]
binaries = []
hiddenimports = []

# websockets: datas に直接追加して _internal/ 以下に展開させる
_ws = _site_pkgs / 'websockets'
if _ws.exists():
    datas.append((str(_ws), 'websockets'))
    for _pyd in _ws.rglob('*.pyd'):
        binaries.append((str(_pyd), 'websockets'))
    hiddenimports += collect_submodules('websockets')
else:
    print(f'[WARN] websockets not found at {_ws}', file=sys.stderr)

# httpx / pydantic / aiofiles
for _pkg in ('httpx', 'pydantic', 'aiofiles'):
    tmp_ret = collect_all(_pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['makeaifactory_launcher.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='makeAiFactory',
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
    icon='assets/icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='makeAiFactory',
)
