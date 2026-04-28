# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

needed_datas = [
    ('item_prices.csv', '.'),
    ('calculator_data_merged.csv', '.'),
    ('drop_table_weights.csv', '.'),
    ('物品轮廓爆率推断器.html', '.'),
]

a = Analysis(
    ['show_grid.py'],
    pathex=[],
    binaries=[],
    datas=needed_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='艾莎鉴影',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
