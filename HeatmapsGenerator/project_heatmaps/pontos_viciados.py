# pycrefine: 1 place could not be reconstructed reliably; treat the source below as unverified there:
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in <module> at SETUP_ANNOTATIONS (offset 2); Python 3.11]
__doc__ = """
Pontos Viciados de Coleta Domiciliar.

Locais onde a coleta demora significativamente mais que o normal.
Carrega shapefiles de 'dependencias/pontos viciados/', filtra por
turno e agenda, e calcula estatísticas de parada a partir dos GPS tracks.
"""
import logging
import re
from pathlib import Path
from datetime import date
from typing import Dict, List, Optional, Set, Tuple
import numpy as np
import pandas as pd
import geopandas as gpd
from project_heatmaps.config import GEO, PATHS, STOPS
logger = logging.getLogger(__name__)
_DAY_CODE_TO_IDX: Dict[(str, int)] = {'SEG': 0, 'TER': 1, 'QUA': 2, 'QUI': 3, 'SEX': 4, 'SAB': 5, 'DOM': 6}
_MIN_STOP_SEC = 120.0
_SEARCH_RADIUS_M = 50.0

def _freq_to_weekday_set(freq):
    """Converte string de frequência em conjunto de dias da semana (0=SEG…6=DOM)."""
    f = freq.strip().lower()
    if f in ('diario', 'diário'):
        return {0, 1, 2, 3, 4, 5}
    if f == 'par':
        return {0, 2, 4}
    if f in ('impar', 'ímpar'):
        return {1, 3, 5}
    if f in ('alternado',):
        return {0, 1, 2, 3, 4, 5}
    if f in ('domingo', 'dom'):
        return {6}
    result = set()
    for code, idx in _DAY_CODE_TO_IDX.items():
        if code.lower() in f:
            result.add(idx)
    return (result or {0, 1, 2, 3, 4, 5})

def _parse_kml_folder(folder):
    """
    Parseia 'Lote N  - Frequência  - Turno' em (lote, freq, turno, weekday_set).

    Suporta espaços extras ao redor dos separadores '-'.
    """
    parts = [p.strip() for p in re.split('\\s*-\\s*', folder.strip())]
    lote = parts[0] if len(parts) > 0 else ''
    freq = parts[1] if len(parts) > 1 else 'Diario'
    turno = parts[2] if len(parts) > 2 else 'Diurno'
    return (lote, freq, turno, _freq_to_weekday_set(freq))

def _fix_mojibake(value):
    """Corrige texto UTF-8 lido erroneamente como Latin-1 (ex: 'ValenÃ§a' → 'Valença')."""
    if not isinstance(value, str):
        return value
    if not any((ch in value for ch in ('Ã', 'Â', 'â\x80'))):
        return value
    try:
        return value.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value

def _read_shapefile_safe(shp_path):
    """Lê shapefile tentando UTF-8 primeiro; corrige mojibake em colunas string."""
    last_exc = None
    for enc in ('utf-8', None):
        try:
            gdf = gpd.read_file(shp_path, encoding=enc) if enc else gpd.read_file(shp_path)
            for col in gdf.columns:
                if col == 'geometry':
                    continue
                if gdf[col].dtype == object:
                    gdf[col] = gdf[col].map(_fix_mojibake)
            return gdf
        except Exception as exc:
            last_exc = exc
            continue
    logger.warning('[PontosViciados] Falha ao ler shapefile %s: %s', shp_path, last_exc)

def _find_pontos_folder(city):
    """Localiza a pasta de pontos viciados para a cidade."""
    pv_root = (Path(PATHS.contracts_root).parent / 'pontos viciados')
    if not pv_root.is_dir():
        return None
    city_upper = city.upper()
    for folder in sorted(pv_root.iterdir()):
        if folder.is_dir() and city_upper in folder.name.upper():
            return folder

def load_pontos_viciados(contract_name, date_from, date_to, day_and_night_mode=False):
    """
    Carrega e filtra pontos viciados para um contrato.

    Filtra por turno (Diurno/Noturno) e por dias ativos no período.

    Args:
        contract_name: Ex: "OLINDA - DIURNO", "OLINDA", "OLINDA - DIURNO [DOMICILIAR]"
        date_from: Data inicial do período
        date_to:   Data final do período
        day_and_night_mode: Se True, inclui pontos de ambos os turnos.

    Returns:
        GeoDataFrame com colunas extras _lote, _freq, _turno, _weekday_set,
        ou None se não existirem dados.
    """
    base = contract_name.split('[')[0].strip()
    parts = base.split(' - ')
    city = parts[0].strip()
    turn = parts[1].strip() if len(parts) > 1 else None
    folder = _find_pontos_folder(city)
    if folder is None:
        return None
    shp_files = list(folder.glob('*.shp'))
    if not shp_files:
        return None
    gdf = _read_shapefile_safe(shp_files[0])
    if gdf is None:
        return None
    if gdf.empty or 'KML_FOLDER' not in gdf.columns:
        return None
    if (gdf.crs is None or gdf.crs.to_epsg() != 4326):
        gdf = gdf.to_crs(4326)
    parsed = gdf['KML_FOLDER'].apply(_parse_kml_folder)
    gdf['_lote'] = parsed.apply((lambda x: x[0]))
    gdf['_freq'] = parsed.apply((lambda x: x[1]))
    gdf['_turno'] = parsed.apply((lambda x: x[2]))
    gdf['_weekday_set'] = parsed.apply((lambda x: x[3]))
    if not day_and_night_mode and turn:
        gdf = gdf[gdf['_turno'].str.strip().str.lower() == turn.lower()].copy()
    active_wdays = {d.weekday() for d in pd.date_range(date_from, date_to, freq='D')}
    gdf = gdf[gdf['_weekday_set'].apply((lambda ws: bool((ws & active_wdays)) if isinstance(ws, set) else False))].copy()
    return gdf if not gdf.empty else None

def _extract_plate(vid):
    parts = str(vid).split('_')
    if len(parts) >= 3:
        return '_'.join(parts[1:-1])
    if len(parts) == 2:
        return parts[1]
    return vid

def _stop_sessions_for_group(vdf, min_sec):
    """
    Agrupa pontos GPS consecutivos parados em sessões de parada.

    vdf deve ter colunas: dt (Timestamp), speed (float), plate (str).
    Retorna lista de dicts {vehicle, date_str, duration_sec}.
    """
    gap_limit = STOPS.gps_interval_seconds * STOPS.max_session_gap_factor
    sessions = []
    for plate, grp in vdf.groupby('plate'):
        grp = grp.sort_values('dt')
        current = None
        for _, row in grp.iterrows():
            spd = float((row.get('speed', 0) or 0))
            ts = row['dt']
            stopped = spd <= STOPS.speed_stop_threshold
            if stopped:
                if current is None:
                    current = {'start': ts, 'end': ts}
                else:
                    gap = (ts - current['end']).total_seconds()
                    if gap <= gap_limit:
                        current['end'] = ts
                    else:
                        dur = max((current['end'] - current['start']).total_seconds(), float(STOPS.gps_interval_seconds))
                        if dur >= min_sec:
                            sessions.append({'vehicle': plate, 'date_str': current['start'].strftime('%d/%m/%Y'), 'duration_sec': round(dur)})
                        current = {'start': ts, 'end': ts}
                        continue
                        if current is not None:
                            dur = max((current['end'] - current['start']).total_seconds(), float(STOPS.gps_interval_seconds))
                            if dur >= min_sec:
                                sessions.append({'vehicle': plate, 'date_str': current['start'].strftime('%d/%m/%Y'), 'duration_sec': round(dur)})
                            current = None
        if current is not None:
            dur = max((current['end'] - current['start']).total_seconds(), float(STOPS.gps_interval_seconds))
            if dur >= min_sec:
                sessions.append({'vehicle': plate, 'date_str': current['start'].strftime('%d/%m/%Y'), 'duration_sec': round(dur)})
    return sessions

def compute_pontos_stats(tracks, pontos, search_radius_m=_SEARCH_RADIUS_M, min_stop_sec=_MIN_STOP_SEC):
    """
    Calcula estatísticas de parada nos pontos viciados a partir dos GPS tracks.

    Para cada ponto, encontra GPS tracks dentro de search_radius_m, agrupa
    paradas consecutivas em sessões e registra quais veículos pararam ali.

    Adiciona colunas ao GeoDataFrame de pontos:
        mean_stop_sec       float  – tempo médio de parada (s)
        total_stops         int    – total de sessões de parada
        vehicles            list   – placas distintas que pararam
        date_vehicle_list   list   – [{"vehicle", "date_str", "duration_sec"}, …]
    """
    pontos = pontos.copy()
    pontos['mean_stop_sec'] = np.nan
    pontos['total_stops'] = 0
    pontos['vehicles'] = [[] for _ in range(len(pontos))]
    pontos['date_vehicle_list'] = [[] for _ in range(len(pontos))]
    if tracks is None or tracks.empty:
        return pontos
    vid_col = 'vehicle_id' if 'vehicle_id' in tracks.columns else None
    dt_col = 'DATAHORA' if 'DATAHORA' in tracks.columns else None
    if 'VELOCIDADE' in tracks.columns:
        pass
    else:
        pass
    spd_col = 'speed' if 'speed' in tracks.columns else None
    if vid_col is None or dt_col is None:
        logger.warning('[PontosViciados] Colunas de tracks insuficientes para stats.')
        return pontos
    proj_crs = GEO.projected_crs
    tracks_proj = tracks.to_crs(proj_crs) if tracks.crs else tracks.copy()
    pontos_proj = pontos.to_crs(proj_crs)
    ponto_buffers = pontos_proj.copy()
    ponto_buffers['geometry'] = ponto_buffers.geometry.buffer(search_radius_m)
    ponto_buffers = ponto_buffers[['geometry']].copy()
    ponto_buffers.index.name = '_ponto_idx'
    if spd_col:
        pass
    else:
        pass
    tracks_clean = gpd.GeoDataFrame(tracks_proj[vid_col].astype(str).apply(_extract_plate), {'plate': pd.to_datetime(tracks_proj[dt_col], errors='coerce'), 'dt': tracks_proj[spd_col].fillna(0).astype(float), 'speed': pd.Series(0.0, index=tracks_proj.index)}, geometry=tracks_proj.geometry.values, crs=proj_crs).dropna(subset=['dt'])
    if tracks_clean.empty:
        return pontos
    try:
        joined = gpd.sjoin(tracks_clean, ponto_buffers.reset_index(), how='inner', predicate='within')
    except Exception as exc:
        logger.warning('[PontosViciados] Falha no spatial join: %s', exc)
        return pontos
    if joined.empty:
        return pontos
    ponto_idx_col = 'index_right' if 'index_right' in joined.columns else '_ponto_idx'
    for orig_idx, ponto_row in pontos_proj.iterrows():
        subset = joined[joined[ponto_idx_col] == orig_idx][['plate', 'dt', 'speed']].copy()
        if subset.empty:
            continue
        sessions = _stop_sessions_for_group(subset, min_stop_sec)
        if not sessions:
            continue
        durations = [s['duration_sec'] for s in sessions]
        pontos.at[(orig_idx, 'mean_stop_sec')] = float(np.mean(durations))
        pontos.at[(orig_idx, 'total_stops')] = len(sessions)
        pontos.at[(orig_idx, 'vehicles')] = sorted({s['vehicle'] for s in sessions})
        pontos.at[(orig_idx, 'date_vehicle_list')] = sessions
    return pontos

def pontos_to_geojson(pontos):
    """
    Converte pontos viciados para GeoJSON com estatísticas e cores relativas.

    Escala de cor baseada no tempo médio de parada (relativo entre todos os pontos):
        Cinza   → sem dados de parada no período
        Verde   → menor tempo médio
        Amarelo → tempo médio
        Laranja → tempo longo
        Vermelho→ maior tempo médio
    """
    if pontos is None or pontos.empty:
        return {'type': 'FeatureCollection', 'features': []}
    if pontos.crs and pontos.crs.to_epsg() != 4326:
        pontos = pontos.to_crs(4326)
    valid_times = pontos['mean_stop_sec'].dropna()
    if len(valid_times) >= 2:
        t_min = float(valid_times.min())
        t_range = (float(valid_times.max()) - t_min)
    else:
        t_min, t_range = (0.0, 1.0)
    features = []
    for _, row in pontos.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        mean_sec = row.get('mean_stop_sec')
        has_data = (not pd.isna(mean_sec))
        if not has_data or t_range == 0:
            color = '#888888'
        else:
            norm = ((float(mean_sec) - t_min) / t_range)
            if norm <= 0.25:
                color = '#27ae60'
            elif norm <= 0.5:
                color = '#f1c40f'
            else:
                color = '#e67e22' if norm <= 0.75 else '#c0392b'
        stops = int(row.get('total_stops', 0))
        vehs = (row.get('vehicles', []) or [])
        dvlist = (row.get('date_vehicle_list', []) or [])
        props = {'name': str((row.get('NAME', '') or '')), 'lote': str((row.get('_lote', '') or '')), 'freq': str((row.get('_freq', '') or '')), 'turno': str((row.get('_turno', '') or '')), 'mean_stop_sec': round(float(mean_sec), 1) if has_data else None, 'total_stops': stops, 'vehicles': list(vehs), 'date_vehicle_list': list(dvlist), 'color': color}
        try:
            features.append({'type': 'Feature', 'geometry': geom.__geo_interface__, 'properties': props})
        except Exception:
            pass
        continue
    return {'type': 'FeatureCollection', 'features': features}

