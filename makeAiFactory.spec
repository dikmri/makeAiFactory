# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

# spec ファイルと同階層を基準にする (SPECPATH は PyInstaller が設定する)
_spec_dir = Path(SPECPATH)
_src_path = str(_spec_dir / 'src')
_site_pkgs = _spec_dir / '.venv' / 'Lib' / 'site-packages'

# makeaifactory パッケージを PyInstaller が認識できるよう sys.path に追加
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

datas = [
    ('app/manifest', 'app/manifest'),
    ('app/workflow', 'app/workflow'),
    ('app/assets', 'app/assets'),
    # インターネット投入口 β の Web UI 静的ファイル (HTML / JS / CSS)
    ('src/makeaifactory/remote_room/static', 'makeaifactory/remote_room/static'),
]
binaries = []
hiddenimports = []

# makeaifactory 全サブパッケージを明示的に収集
hiddenimports += collect_submodules('makeaifactory')

# websockets: datas に直接追加して _internal/ 以下に展開させる
_ws = _site_pkgs / 'websockets'
if _ws.exists():
    datas.append((str(_ws), 'websockets'))
    for _pyd in _ws.rglob('*.pyd'):
        binaries.append((str(_pyd), 'websockets'))
    hiddenimports += collect_submodules('websockets')
else:
    print(f'[WARN] websockets not found at {_ws}', file=sys.stderr)

# httpx / pydantic / aiofiles / discord / aiohttp / Pillow / qrcode
for _pkg in ('httpx', 'pydantic', 'aiofiles', 'discord', 'aiohttp', 'PIL', 'qrcode'):
    tmp_ret = collect_all(_pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# aiohttp が依存する multidict / yarl / frozenlist
for _pkg in ('multidict', 'yarl', 'frozenlist'):
    tmp_ret = collect_all(_pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# orjson を全リストから除去: discord.py は try/except で標準 json にフォールバックする
# collect_all('discord') が orjson.pyd をバイナリ収集することがあるため
# excludes だけでなく各リストから明示的に除外する
def _drop_orjson(seq):
    return [(s, d) for s, d in seq if 'orjson' not in s.lower().replace('\\', '/')]
datas        = _drop_orjson(datas)
binaries     = _drop_orjson(binaries)
hiddenimports = [h for h in hiddenimports if 'orjson' not in h.lower()]


a = Analysis(
    ['makeaifactory_launcher.py'],
    pathex=[_src_path],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['orjson'],  # discord.py が orjson を try/except でオプション使用するため除外し標準jsonにフォールバックさせる
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
