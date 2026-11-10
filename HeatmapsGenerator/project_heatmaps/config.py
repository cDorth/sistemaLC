# pycrefine: 8 places could not be reconstructed reliably; treat the source below as unverified there:
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in <module> at SETUP_ANNOTATIONS (offset 2); Python 3.11]
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in PathConfig at SETUP_ANNOTATIONS (offset 10); Python 3.11]
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in ColorConfig at SETUP_ANNOTATIONS (offset 10); Python 3.11]
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in GeoConfig at SETUP_ANNOTATIONS (offset 10); Python 3.11]
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in ValidationConfig at SETUP_ANNOTATIONS (offset 10); Python 3.11]
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in ProcessingConfig at SETUP_ANNOTATIONS (offset 10); Python 3.11]
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in OptimizationConfig at SETUP_ANNOTATIONS (offset 10); Python 3.11]
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in StopsConfig at SETUP_ANNOTATIONS (offset 10); Python 3.11]
__doc__ = """
Configurações centralizadas do sistema de geração de heatmaps.

Este módulo contém todas as constantes, parâmetros e configurações
utilizadas pelo sistema.
"""
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Set, List, Optional
import os
import sys
import re
from project_heatmaps.user_config import load_user_config
_user_cfg = load_user_config()

def _resolve_base_dir():
    """
    Resolve a pasta raiz operacional do projeto a partir do .env ou do executável.

    Procura a pasta operacional que contenha a base de dados ou contratos reais
    (como 'FS - DIE').
    """
    import sys
    starts = []
    if _user_cfg.filepath is not None:
        starts.append(_user_cfg.filepath.parent)
    if getattr(sys, 'frozen', False):
        starts.append(Path(sys.executable).parent)
    else:
        starts.append(Path(os.getcwd()))
        starts.append(Path(__file__).resolve().parent.parent)

    # 1. Procura pasta que contenha dependencias com gps.duckdb ou CONTRATOS
    for start in starts:
        for directory in ([start] + list(start.parents)[:6]):
            if (directory / 'dependencias' / 'outputs' / 'gps.duckdb').is_file() or (directory / 'dependencias' / 'CONTRATOS').is_dir():
                return directory

    # 2. Procura pasta 'FS - DIE' explicitamente
    for start in starts:
        for directory in ([start] + list(start.parents)[:5]):
            fs_candidate = directory / 'FS - DIE'
            if (fs_candidate / 'dependencias').is_dir():
                return fs_candidate
            fs_parent = directory.parent / 'FS - DIE'
            if (fs_parent / 'dependencias').is_dir():
                return fs_parent

    # 3. Caminho padrão comum no sistema
    default_fs = Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE')
    if (default_fs / 'dependencias').is_dir():
        return default_fs

    # 4. Procura qualquer pasta com dependencias
    for start in starts:
        for directory in ([start] + list(start.parents)[:6]):
            if (directory / 'dependencias').is_dir():
                return directory

    return starts[0] if starts else None

BASE_DIR: Optional[Path] = _resolve_base_dir()
GPS_DB_SEARCHED: List[Path] = []

def _find_gps_db():
    """
    Descobre o caminho para gps.duckdb.

    Ordem de prioridade:
      1. Configuração explícita em config.txt / .env ([GPS_DATABASE] caminho = ...)
         — caminho relativo é resolvido em relação à pasta do config.txt ou BASE_DIR
      2. BASE_DIR / dependencias / outputs / gps.duckdb
      3. FS - DIE / dependencias / outputs / gps.duckdb
      4. Pastas relativas comuns
    """
    import sys
    if _user_cfg.gps_db_path:
        p = Path(_user_cfg.gps_db_path)
        if not p.is_absolute():
            for base_ref in [_user_cfg.filepath.parent if _user_cfg.filepath else None, BASE_DIR]:
                if base_ref:
                    cand = (base_ref / p).resolve()
                    if cand not in GPS_DB_SEARCHED:
                        GPS_DB_SEARCHED.append(cand)
                    if cand.is_file():
                        return cand
        else:
            try:
                resolved = p.resolve()
                if resolved not in GPS_DB_SEARCHED:
                    GPS_DB_SEARCHED.append(resolved)
                if resolved.is_file():
                    return resolved
            except Exception:
                pass

    candidates = []
    if BASE_DIR is not None:
        candidates += [
            (BASE_DIR / 'dependencias' / 'outputs' / 'gps.duckdb'),
            (BASE_DIR / 'outputs' / 'gps.duckdb'),
        ]
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(os.getcwd())
    candidates += [
        (base.parent / 'FS - DIE' / 'dependencias' / 'outputs' / 'gps.duckdb'),
        (base.parent / 'FS - DIE' / 'outputs' / 'gps.duckdb'),
        Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE\dependencias\outputs\gps.duckdb'),
        Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE\outputs\gps.duckdb'),
        (base / 'dependencias' / 'outputs' / 'gps.duckdb'),
        (base / 'outputs' / 'gps.duckdb'),
        (base.parent / 'inlog_api_project' / 'outputs' / 'gps.duckdb'),
    ]
    for c in candidates:
        try:
            resolved = c.resolve()
        except Exception:
            continue
        if resolved not in GPS_DB_SEARCHED:
            GPS_DB_SEARCHED.append(resolved)
        if resolved.is_file():
            return resolved
    return None

GPS_DB_PATH: Optional[Path] = _find_gps_db()
USER_CONFIG = _user_cfg
CONTRACTS = _user_cfg.contracts if _user_cfg.has_contracts else ['CABO', 'CANELA', 'CARUARU', 'CUIABA', 'GARANHUNS', 'JABOATAO', 'JEQUIE', 'OLINDA', 'PAULISTA', 'PESQUEIRA', 'VITORIA']
TURN = ['DIURNO', 'NOTURNO']
_DEFAULT_ESTACIONARIO_TYPES: Dict[(str, str)] = {'Caixa Brooks': 'POLIGUINDASTE'}
ESTACIONARIO_TYPES: Dict[(str, str)] = {**_DEFAULT_ESTACIONARIO_TYPES, **(_user_cfg.estacionario_types or {})}
DAY_ORDER = ['SEG', 'TER', 'QUA', 'QUI', 'SEX', 'SAB', 'DOM']
DAY_IDX = {d: i for i, d in enumerate(DAY_ORDER)}
PREFIX_DEFAULT_DAYS: Dict[(str, Set[str])] = {'SCDOM': {'DOM'}, 'SCD': {'QUA', 'QUI', 'SAB', 'SEG', 'SEX', 'TER'}, 'SCP': {'QUA', 'SEG', 'SEX'}, 'SCI': {'QUI', 'SAB', 'TER'}}
PREFIX_SUFFIX_ONLY = {'SCA', 'SCDIST', 'SCEXTRA', 'SCFEIRA', 'SCRURAL', 'SCS'}
WEEKDAY_MAP_DAY_TO_CODE = {day: i for i, day in enumerate(DAY_ORDER)}
WEEKDAY_MAP_CODE_TO_DAY = {i: day for i, day in enumerate(DAY_ORDER)}
@dataclass
class PathConfig:
    """Configuração de caminhos do sistema."""
    shapes_root: Path = Path('SHAPES')
    contracts_root: Optional[Path] = None
    heatmaps_dir: Path = Path('HEATMAPS')
    history_dir: Path = Path('HISTORICO')
    xml_reports_dir: Path = Path('RELATORIOS_HEATMAPS_XML')
    basemap_cache_dir: Path = Path('CACHE_MAPA_BASE')
    def __post_init__(self):
        """Garante que os diretórios existem."""
        shapes_candidates = []
        if BASE_DIR is not None:
            shapes_candidates += [
                BASE_DIR / 'dependencias' / 'shapes',
                BASE_DIR / 'dependencias' / 'SHAPES',
                BASE_DIR / 'shapes',
                BASE_DIR / 'SHAPES',
            ]
        base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(os.getcwd())
        shapes_candidates += [
            base.parent / 'FS - DIE' / 'dependencias' / 'shapes',
            base.parent / 'FS - DIE' / 'dependencias' / 'SHAPES',
            base.parent / 'FS - DIE' / 'shapes',
            base.parent / 'FS - DIE' / 'SHAPES',
            Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE\dependencias\shapes'),
            Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE\dependencias\SHAPES'),
            Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE\shapes'),
            Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE\SHAPES'),
            base / 'dependencias' / 'shapes',
            base / 'dependencias' / 'SHAPES',
            base / 'shapes',
            base / 'SHAPES',
        ]
        for sc in shapes_candidates:
            try:
                if sc.is_dir():
                    self.shapes_root = sc
                    break
            except Exception:
                continue

        contracts_candidates = []
        if BASE_DIR is not None:
            contracts_candidates += [
                BASE_DIR / 'dependencias' / 'CONTRATOS',
                BASE_DIR / 'dependencias' / 'contratos',
                BASE_DIR / 'CONTRATOS',
                BASE_DIR / 'contratos',
            ]
        contracts_candidates += [
            base.parent / 'FS - DIE' / 'dependencias' / 'CONTRATOS',
            base.parent / 'FS - DIE' / 'dependencias' / 'contratos',
            base.parent / 'FS - DIE' / 'CONTRATOS',
            Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE\dependencias\CONTRATOS'),
            Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE\CONTRATOS'),
            base / 'dependencias' / 'CONTRATOS',
            base / 'dependencias' / 'contratos',
            Path('dependencias') / 'CONTRATOS',
        ]
        for cc in contracts_candidates:
            try:
                if cc.is_dir():
                    self.contracts_root = cc
                    break
            except Exception:
                continue
        if self.contracts_root is None:
            self.contracts_root = Path('dependencias') / 'CONTRATOS'

        if not self.basemap_cache_dir.is_absolute() and BASE_DIR is not None:
            self.basemap_cache_dir = BASE_DIR / self.basemap_cache_dir
        if not self.xml_reports_dir.is_absolute():
            self.xml_reports_dir = self.heatmaps_dir.parent / self.xml_reports_dir.name
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.heatmaps_dir.mkdir(parents=True, exist_ok=True)

PATHS = PathConfig()
OUTER_DATE_REGEX = re.compile('^Shapes - (?P<date>\\d{2}\\.\\d{2}\\.\\d{4})\\.zip$')
INNER_CITY_REGEX = re.compile('^Shapes - (?P<city>.+?) - (?P<date>\\d{2}\\.\\d{2}\\.\\d{4})\\.zip$')
@dataclass
class ColorConfig:
    """Configuração de cores para visualização."""
    sector_colors: Dict[(str, str)] = None
    default_sector_color: str = '#9E9E9E'
    route_colorscale: List[List] = None
    single_day_colors: Dict[(str, str)] = None
    manual_single_day_colors: Dict[(str, str)] = None
    manual_period_colors: Dict[(str, str)] = None
    possible_manual_color: str = '#008080'
    def __post_init__(self):
        if self.sector_colors is None:
            self.sector_colors = {'SCD': '#6D6FFA', 'SCI': '#935CFA', 'SCP': '#B16FFD', 'SCA': '#F85AFD', 'SCS': '#F85AFD', 'SCDOM': '#0098FF', 'SCDIST': '#5AFDFD', 'SCFEIRA': '#5AFDFD', 'SCRURAL': '#5AFDFD', 'SCEXTRA': '#5AFDFD'}
        if self.route_colorscale is None:
            self.route_colorscale = [[0.0, '#00ff00'], [0.25, '#ffff00'], [0.5, '#ffa500'], [0.75, '#ff0000'], [1.0, '#2d0000']]
        if self.single_day_colors is None:
            self.single_day_colors = {'collected': '#00ff00', 'not_collected': '#ff0000', 'possible_gps_gap': '#ffff00', 'possible_manual': '#008080'}
        if self.manual_single_day_colors is None:
            self.manual_single_day_colors = {'collected': '#00FFFF', 'not_collected': '#0044AA', 'possible_gps_gap': '#0088FF'}
        if self.manual_period_colors is None:
            self.manual_period_colors = {'80-100': '#00FFFF', '60-80': '#00CCEE', '40-60': '#0099CC', '20-40': '#0066AA', '0-20': '#003388'}
            return None

COLORS = ColorConfig()
@dataclass
class GeoConfig:
    """Configuração de parâmetros geográficos."""
    projected_crs: str = 'EPSG:31985'
    display_crs: str = 'EPSG:4326'
    buffer_meters: float = 8.0
    min_edge_coverage: float = 0.35
    min_intersection_length: float = 6.0
    gps_gap_endpoint_tolerance: float = 2.0
    gps_gap_max_size: int = 1
    gps_recheck_buffer_meters: float = 15.0
    gps_parallel_band_meters: float = 25.0
    coverage_bins: int = 10
    coverage_min_ratio: float = 0.5
    leg_max_seconds: float = 120.0
    leg_max_meters: float = 250.0
    leg_max_turn_deg: float = 45.0
    leg_heading_min_meters: float = 3.0
    leg_turn_speed_ratio: float = 0.6
    leg_min_parallel: float = 0.9
    point_endpoint_margin_meters: float = 8.0
    point_endpoint_margin_frac: float = 0.25
    point_max_axis_deg: float = 30.0
    corner_stop_min_seconds: float = 15.0
    corner_radius_meters: float = 40.0
    corner_slow_kmh: float = 5.0
    dwell_radius_meters: float = 20.0
    dwell_min_seconds: float = 120.0
    mobile_simplify_segments: float = 5.0
    mobile_simplify_sectors: float = 10.0

GEO = GeoConfig()
BASEMAP_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
BASEMAP_ATTRIBUTION = '&copy; OpenStreetMap contributors'
BASEMAP_MAX_ZOOM = 19
BASEMAP_OFFLINE_ENABLED = True
BASEMAP_OFFLINE_CONTEXTO_ZOOM_MIN = 10
BASEMAP_OFFLINE_CONTEXTO_ZOOM_MAX = 13
BASEMAP_OFFLINE_CONTEXTO_FOLGA_KM = 15.0
BASEMAP_OFFLINE_CONTEXTO_TETO_TILES = 400
BASEMAP_OFFLINE_CIDADE_ZOOM_MIN = 14
BASEMAP_OFFLINE_CIDADE_ZOOM_MAX = 15
BASEMAP_OFFLINE_DETALHE_ZOOM_MIN = 16
BASEMAP_OFFLINE_DETALHE_ZOOM_MAX = 17
BASEMAP_OFFLINE_ANEL_TILES = 1
BASEMAP_OFFLINE_ORCAMENTO_MB = 14.0
BASEMAP_OFFLINE_WEBP_QUALIDADE = 65
BASEMAP_MOBILE_TETO_ARQUIVO_MB = 9.0
BASEMAP_MALHAS_SUBDIR = 'Correções/Edições/Malhas Viárias - Somente Transitáveis'
BASEMAP_ALT3_NAME = 'Esri World Street Map'
BASEMAP_ALT3_TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}'
BASEMAP_ALT3_ATTRIBUTION = 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS User Community'
BASEMAP_ALT_NAME = 'VersaTiles Colorful'
BASEMAP_ALT_STYLE_URL = 'https://tiles.versatiles.org/assets/styles/colorful/style.json'
BASEMAP_ALT_ATTRIBUTION = '&copy; OpenStreetMap contributors, &copy; VersaTiles'
BASEMAP_ALT_MAX_ZOOM = 20
BASEMAP_ALT_REQUIRES_VECTOR_RENDERER = True

def get_plotly_basemap_style():
    """Mesmo basemap do Leaflet, no formato de style spec que o Plotly aceita.

    Existe porque `plotting_original.py` desenha com Plotly, não com Leaflet, e
    um nome de estilo embutido (`"open-street-map"`, `"carto-positron"`) faria
    dele um SEGUNDO dono do mapa de fundo — foi exatamente o furo que o
    adversário achou em 2026-09-02 (trocar BASEMAP_TILE_URL não mudava nada
    naquele gerador). Devolvendo o style spec, o Plotly passa a ler daqui.
    """
    return {'version': 8, 'sources': {'basemap': {'type': 'raster', 'tiles': [BASEMAP_TILE_URL], 'tileSize': 256, 'attribution': BASEMAP_ATTRIBUTION, 'maxzoom': BASEMAP_MAX_ZOOM}}, 'layers': [{'id': 'basemap', 'type': 'raster', 'source': 'basemap'}]}

@dataclass
class ValidationConfig:
    """Configuração de limites de validade para histórico."""
    min_pct_single_turn: float = 10.0
    min_pct_day_and_night: float = 20.0
    def get_min_valid_pct(self, day_and_night_mode):
        """Retorna o percentual mínimo baseado no modo."""
        return self.min_pct_day_and_night if day_and_night_mode else self.min_pct_single_turn

VALIDATION = ValidationConfig()
@dataclass
class ProcessingConfig:
    """Configuração de parâmetros de processamento."""
    max_points_per_line: int = 40
    visualization_tolerance: float = 5e-05
    enable_cache: bool = True
    cache_size: int = 128

PROCESSING = ProcessingConfig()
@dataclass
class OptimizationConfig:
    """
    Configuração de otimizações para mapas mais leves e rápidos.
    
    Use enable_optimizations=True para ativar simplificação agressiva
    e metadados para cálculos dinâmicos.
    """
    enable_optimizations: bool = False
    optimized_tolerance: float = 0.0001
    optimized_max_points: int = 25
    max_traces_per_category: int = 150
    enable_dynamic_stats: bool = True
    enable_combined_filters: bool = True

OPTIMIZATION = OptimizationConfig()
@dataclass
class StopsConfig:
    """Configuração do modo de pontos críticos de parada."""
    speed_stop_threshold: float = 3.0
    speed_slow_threshold: float = 10.0
    gps_interval_seconds: int = 60
    max_session_gap_factor: int = 3
    normalization_percentile: float = 0.95
    normalize_by_length: bool = True
    min_session_duration_sec: int = 30
    time_periods: list = None
    def __post_init__(self):
        if self.time_periods is None:
            self.time_periods = [('Madrugada', 0, 5), ('Manhã', 5, 12), ('Tarde', 12, 18), ('Noite', 18, 24)]
            return None

STOPS = StopsConfig()

def get_visualization_tolerance():
    """
    Retorna tolerância de simplificação baseada em configuração.
    
    Returns:
        Tolerância em graus
    """
    if OPTIMIZATION.enable_optimizations:
        return OPTIMIZATION.optimized_tolerance
    return PROCESSING.visualization_tolerance

def get_max_points_per_line():
    """
    Retorna máximo de pontos por linha baseado em configuração.
    
    Returns:
        Número máximo de pontos
    """
    if OPTIMIZATION.enable_optimizations:
        return OPTIMIZATION.optimized_max_points
    return PROCESSING.max_points_per_line

def get_contract_city_mapping(profiles):
    """
    Cria mapeamento de contratos para cidades.
    
    Args:
        profiles: Lista de perfis/contratos base
        
    Returns:
        Dicionário {contrato_completo: cidade}
    """
    mapping = {}
    for p in profiles:
        for turn in TURN:
            key = f"{p} - {turn}"
            mapping[key] = p.capitalize()
    return mapping

def simplify_contract_name(contract):
    """
    Normaliza o nome do contrato para uso em arquivos e títulos.
    
    Suporta sufixo de tipo de coleta entre colchetes, ex:
    'CABO - DIURNO [SELETIVA]' ou 'CABO [DOMICILIAR]'
    
    Args:
        contract: Nome do contrato
        
    Returns:
        Tupla (nome_arquivo, nome_display)
        
    Examples:
        >>> simplify_contract_name('CABO - DIURNO')
        ('Cabo_Diurno', 'Cabo Diurno')
        >>> simplify_contract_name('CABO')
        ('Cabo', 'Cabo (Diurno + Noturno)')
        >>> simplify_contract_name('CABO - DIURNO [SELETIVA]')
        ('Cabo_Diurno_Seletiva', 'Cabo Diurno - Seletiva')
        >>> simplify_contract_name('CABO [DOMICILIAR]')
        ('Cabo_Domiciliar', 'Cabo (Diurno + Noturno) - Domiciliar')
    """
    ctype_suffix = ''
    ctype_display = ''
    if '[' in contract and contract.rstrip().endswith(']'):
        base_part, ctype_raw = contract.rsplit('[', 1)
        contract = base_part.strip()
        ctype_suffix = ctype_raw.rstrip(']').strip().capitalize()
        ctype_display = ctype_suffix
    if ' - ' in contract:
        left, right = contract.split(' - ', 1)
        file_name = f"{left.capitalize()}_{right.capitalize()}"
        display_name = f"{left.capitalize()} {right.capitalize()}"
    else:
        base = contract.capitalize()
        file_name = base
        display_name = f"{base} (Diurno + Noturno)"
    if ctype_suffix:
        file_name = f"{file_name}_{ctype_suffix}"
        display_name = f"{display_name} - {ctype_display}"
    return (file_name, display_name)

