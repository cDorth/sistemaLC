"""
Mapas de calor de Estacionários (Caixas Brooks etc).

Estrutura paralela aos pontos viciados, mas:
  - Shapes carregados de dependencias/estacionarios/<Tipo> - <Contrato>/
  - Cada tipo (ex: "Caixas Brooks") tem um tipo de veículo associado
    (ex: POLIGUINDASTE) — relação definida em config.ESTACIONARIO_TYPES,
    podendo ser sobrescrita via [ESTACIONARIOS] no .env.
  - Stats reaproveitam compute_pontos_stats do módulo pontos_viciados.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional
import geopandas as gpd
from project_heatmaps.config import ESTACIONARIO_TYPES, PATHS
from project_heatmaps.pontos_viciados import _read_shapefile_safe
logger = logging.getLogger(__name__)

def _estacionarios_root():
    """Pasta raiz dependencias/estacionarios/."""
    return (Path(PATHS.contracts_root).parent / 'estacionarios')

def _folder_for(tipo, contract_base):
    """
    Resolve a pasta `<Tipo> - <CONTRATO>` dentro de dependencias/estacionarios/.

    A comparação é case-insensitive nos dois lados (tipo + contrato).
    Retorna o primeiro match ou None.
    """
    root = _estacionarios_root()
    if not root.is_dir():
        return None
    tipo_u = tipo.upper().strip()
    contract_u = contract_base.upper().strip()
    target = f"{tipo_u} - {contract_u}"
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and folder.name.upper().strip() == target:
            return folder

def get_estacionario_types_for_contract(contract_base):
    """
    Retorna {tipo: vehicle_type} para os tipos que possuem pasta no contrato.

    Ex: para contract_base="CABO" e pasta "Caixas Brooks - CABO" existente:
        {"Caixas Brooks": "POLIGUINDASTE"}
    """
    available = {}
    for tipo, vehicle_type in ESTACIONARIO_TYPES.items():
        if _folder_for(tipo, contract_base) is not None:
            available[tipo] = vehicle_type
    return available

def get_required_vehicle_types(profiles):
    """
    Conjunto de tipos de veículo necessários para gerar os mapas de
    estacionários de todos os contratos selecionados.

    Se nenhum contrato tem pastas de estacionários, retorna o conjunto vazio.
    """
    needed = set()
    for c in profiles:
        for vt in get_estacionario_types_for_contract(c).values():
            needed.add(vt.upper())
    return needed

def load_estacionario_points(contract_base, tipo):
    """
    Lê o shapefile `<Tipo> - <CONTRATO>.shp` dentro de
    dependencias/estacionarios/<Tipo> - <CONTRATO>/.

    Returns:
        GeoDataFrame em EPSG:4326 com colunas mínimas (NAME se houver) ou None.
    """
    folder = _folder_for(tipo, contract_base)
    if folder is None:
        return None
    shp_files = list(folder.glob('*.shp'))
    if not shp_files:
        logger.warning('[Estacionarios] Pasta sem .shp: %s', folder)
        return None
    gdf = _read_shapefile_safe(shp_files[0])
    if gdf is None or gdf.empty:
        return None
    if (gdf.crs is None or gdf.crs.to_epsg() != 4326):
        gdf = gdf.to_crs(4326)
    if '_lote' not in gdf.columns:
        gdf['_lote'] = ''
    if '_freq' not in gdf.columns:
        gdf['_freq'] = ''
    if '_turno' not in gdf.columns:
        gdf['_turno'] = ''
    gdf['_tipo_estacionario'] = tipo
    return gdf

