"""
Carregamento e marcação de segmentos prioritários por contrato/cidade.

Lê arquivos `dependencias/prioridades/Prioridades - {Cidade}.gpkg` (formato
OSM, com colunas `u` e `v`) e marca segmentos do heatmap cujo par topológico
(u, v) bate com algum dos pares prioritários.

O par é normalizado como tuple ordenada — assim 'u→v' e 'v→u' são tratados
como a mesma aresta (consistente com o `_edge_key_base` usado em
geo_processing.build_route_segments).
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional, Set, Tuple
import geopandas as gpd
import pandas as pd
from project_heatmaps.config import BASE_DIR
logger = logging.getLogger(__name__)
PRIORITIES_SUBDIR = (Path('dependencias') / 'prioridades')
EdgeKey = Tuple[(int, int)]

def _city_from_contract(contract):
    """Extrai 'Paulista' de 'PAULISTA - DIURNO [SELETIVA]' etc."""
    base = contract or ''
    if '[' in base and base.rstrip().endswith(']'):
        base = base.rsplit('[', 1)[0].strip()
    if ' - ' in base:
        base = base.split(' - ', 1)[0]
    return base.strip().capitalize()

def _resolve_priorities_path(city):
    if BASE_DIR is None:
        return None
    candidate = ((BASE_DIR / PRIORITIES_SUBDIR) / f"Prioridades - {city}.gpkg")
    return candidate if candidate.is_file() else None

def _edge_key(u, v):
    """Normaliza (u, v) em tuple ordenada de int. None se inválido."""
    if u is None or v is None:
        return None
    if pd.isna(u) or pd.isna(v):
        return None
    try:
        ui, vi = int(u), int(v)
    except (TypeError, ValueError):
        return None
    return vi if ui <= vi else ui

def load_priority_edges(contract):
    """
    Carrega o conjunto de pares (u, v) prioritários para o contrato.

    Returns:
        Set de tuplas ordenadas (u_min, v_max), ou None se não houver
        arquivo para a cidade.
    """
    city = _city_from_contract(contract)
    path = _resolve_priorities_path(city)
    if path is None:
        return None
    try:
        gdf = gpd.read_file(path)
    except Exception as exc:
        logger.warning('[priorities] falha ao ler %s: %s', path, exc)
        return None
    if 'u' not in gdf.columns or 'v' not in gdf.columns:
        logger.warning("[priorities] %s sem colunas 'u'/'v'", path.name)
        return None
    edges = set()
    for u, v in zip(gdf['u'], gdf['v']):
        key = _edge_key(u, v)
        if key is not None:
            edges.add(key)
    if not edges:
        return None
    logger.info('[priorities] %s: %d pares (u,v) prioritários carregados (%s)', city, len(edges), path.name)
    return edges

def flag_priority_segments(seg, priority_edges):
    """
    Adiciona coluna booleana `is_priority` ao GeoDataFrame de segmentos.

    Match por par (u, v) ordenado. Se não houver prioridades ou colunas
    u/v, todos recebem False.
    """
    if priority_edges is None or 'u' not in seg.columns or 'v' not in seg.columns:
        seg['is_priority'] = False
        return seg
    flags = [_edge_key(u, v) in priority_edges for u, v in zip(seg['u'], seg['v'])]
    seg['is_priority'] = flags
    n_pri = int(seg['is_priority'].sum())
    if n_pri:
        logger.info('[priorities] %d segmentos marcados como prioritários (de %d totais)', n_pri, len(seg))
    return seg

