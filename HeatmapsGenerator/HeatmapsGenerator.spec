# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

datas = [
    ('logo_DIE.ico', '.'),
    ('logo_DIE.png', '.'),
    ('logo_locar.png', '.'),
    ('dependencias/logo_DIE.ico', 'dependencias'),
    ('dependencias/logo_DIE.png', 'dependencias'),
    ('dependencias/logo_locar.png', 'dependencias'),
]

# Coleta dados extras de pacotes geo/visualização
datas += collect_data_files('pyogrio')
datas += collect_data_files('geopandas')
datas += collect_data_files('reportlab')

hiddenimports = [
    'project_heatmaps',
    'project_heatmaps.ai_poison',
    'project_heatmaps.basemap_cache',
    'project_heatmaps.check_files',
    'project_heatmaps.config',
    'project_heatmaps.data_security',
    'project_heatmaps.estacionarios',
    'project_heatmaps.estacionarios_plotting',
    'project_heatmaps.geo_processing',
    'project_heatmaps.history',
    'project_heatmaps.html_templates',
    'project_heatmaps.janela',
    'project_heatmaps.main',
    'project_heatmaps.mapa_mysql',
    'project_heatmaps.parsers',
    'project_heatmaps.plotting',
    'project_heatmaps.plotting_mobile_enhanced',
    'project_heatmaps.pontos_viciados',
    'project_heatmaps.priorities',
    'project_heatmaps.report_generator',
    'project_heatmaps.sector_labels',
    'project_heatmaps.secure_integration',
    'project_heatmaps.stats_module',
    'project_heatmaps.stops_plotting',
    'project_heatmaps.stops_stats',
    'project_heatmaps.user_config',
    'project_heatmaps.watermark',
    'project_heatmaps.web_assets',
    'project_heatmaps.xml_report',
    'duckdb',
    'tkcalendar',
    'babel.numbers',
    'babel.dates',
    'pyproj',
    'shapely',
    'geopandas',
    'pyogrio',
    'pypdf',
    'reportlab',
]

a = Analysis(
    ['heatmaps_gui.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='HeatmapsGenerator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='logo_DIE.ico',
)
