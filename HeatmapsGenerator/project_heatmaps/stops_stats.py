"""
Cálculo de estatísticas de paradas/lentidão por segmento de rota.

Este módulo implementa o stops_mode: identifica pontos críticos onde
veículos ficam parados ou se deslocam lentamente ao longo das rotas.

Fluxo:
    1. Spatial join tracks ↔ segmentos (buffer, igual ao heatmap)
    2. Classifica cada ponto GPS por peso de parada (velocidade)
    3. Agrupa pontos consecutivos parados em sessões de parada
    4. Calcula score por segmento normalizado por densidade
    5. Produz GeoDataFrame pronto para visualização
"""

import logging
from collections import defaultdict
from datetime import date
from typing import Optional, Tuple, List, Dict
import numpy as np
import pandas as pd
import geopandas as gpd
from project_heatmaps.config import GEO, STOPS
from project_heatmaps.parsers import compute_schedule_days_for_code
logger = logging.getLogger(__name__)

def _extract_plate(vid_str):
    """
    Placa para exibição, vinda do nome do shapefile
    ('{data}_{placa}_{contrato}') ou da placa crua do DuckDB.

    Delega ao `_display_plate` do stats_module: com a união DuckDB+shapefiles,
    as duas grafias da mesma placa podem aparecer no mesmo mapa e precisam
    colapsar em uma identidade só.
    """
    from project_heatmaps.stats_module import _display_plate
    return _display_plate(vid_str)

def _compute_stop_weight(speed):
    """
    Calcula peso de parada de um ponto GPS com base na velocidade.

    - speed ≤ stop_threshold  → 1.0 (parado)
    - stop_threshold < speed ≤ slow_threshold → proporcional decrescente
    - speed > slow_threshold  → 0.0 (normal)
    """
    if (speed is None or np.isnan(speed)):
        return 0.0
    if speed <= STOPS.speed_stop_threshold:
        return 1.0
    if speed <= STOPS.speed_slow_threshold:
        return (1.0 - ((speed - STOPS.speed_stop_threshold) / (STOPS.speed_slow_threshold - STOPS.speed_stop_threshold)))
    return 0.0

def _classify_time_period(hour):
    """Retorna o nome do período do dia para uma hora (0-23)."""
    for name, h_start, h_end in STOPS.time_periods:
        if h_start <= hour:
            if hour < h_end:
                pass
        else:
            continue
        return name
    return 'Desconhecido'

def _build_stop_sessions(points_df):
    """
    Agrupa pontos consecutivos parados em sessões de parada.

    Recebe DataFrame já filtrado para um segmento, com colunas:
        vehicle (str), dt (pd.Timestamp), speed (float)

    Retorna lista de dicts:
        {vehicle, start, end, duration_sec, point_count, hour_start}
    """
    if points_df.empty:
        return []
    sessions = []
    gap_limit_sec = STOPS.gps_interval_seconds * STOPS.max_session_gap_factor
    min_dur = STOPS.min_session_duration_sec
    for vehicle, vg in points_df.groupby('vehicle'):
        vg = vg.sort_values('dt')
        current = None
        for _, row in vg.iterrows():
            is_stopped = row['speed'] <= STOPS.speed_stop_threshold
            ts = row['dt']
            if is_stopped:
                if current is None:
                    current = {'vehicle': vehicle, 'start': ts, 'end': ts, 'point_count': 1}
                else:
                    gap = (ts - current['end']).total_seconds()
                    if gap <= gap_limit_sec:
                        current['end'] = ts
                        current['point_count'] += 1
                    else:
                        dur = max((current['end'] - current['start']).total_seconds(), STOPS.gps_interval_seconds)
                        if dur >= min_dur:
                            current['duration_sec'] = dur
                            current['hour_start'] = current['start'].hour
                            sessions.append(current)
                        current = {'vehicle': vehicle, 'start': ts, 'end': ts, 'point_count': 1}
                        continue
                        if current is not None:
                            dur = max((current['end'] - current['start']).total_seconds(), STOPS.gps_interval_seconds)
                            if dur >= min_dur:
                                current['duration_sec'] = dur
                                current['hour_start'] = current['start'].hour
                                sessions.append(current)
                            current = None
        if current is not None:
            dur = max((current['end'] - current['start']).total_seconds(), STOPS.gps_interval_seconds)
            if dur >= min_dur:
                current['duration_sec'] = dur
                current['hour_start'] = current['start'].hour
                sessions.append(current)
    return sessions

def _aggregate_vehicle_details(sessions):
    """
    Agrega sessões de parada por veículo para exibição.

    Retorna lista de dicts ordenada por total_stop_time_sec DESC:
        {vehicle, total_stop_time_sec, stop_count,
         max_stop_sec, max_stop_date, max_stop_time}
    """
    if not sessions:
        return []
    by_vehicle = defaultdict(list)
    for s in sessions:
        by_vehicle[s['vehicle']].append(s)
    result = []
    for vid, sess_list in by_vehicle.items():
        total_time = sum((s['duration_sec'] for s in sess_list))
        longest = max(sess_list, key=(lambda s: s['duration_sec']))
        if hasattr(longest['start'], 'strftime'):
            pass
        else:
            pass
        if hasattr(longest['start'], 'strftime'):
            pass
        else:
            pass
        vid(len(sess_list), {'v': round(total_time), 'n': round(longest['duration_sec']), 't': longest['start'].strftime('%d/%m/%Y'), 'mx': '', 'mxd': longest['start'].strftime('%H:%M'), 'mxt': ''})
    result.sort(key=(lambda x: x['t']), reverse=True)
    return result

def _compute_peak_period(sessions):
    """
    Determina em qual período do dia as paradas são mais frequentes.

    Retorna dict:
        {peak_period, period_counts: {periodo: n_sessoes}, period_times: {periodo: total_sec}}
    """
    period_counts = defaultdict(int)
    period_times = defaultdict(float)
    for s in sessions:
        hour = s.get('hour_start', 0)
        period = _classify_time_period(hour)
        period_counts[period] += 1
        period_times[period] += s['duration_sec']
    if not period_counts:
        return {'peak_period': None, 'period_counts': {}, 'period_times': {}}
    peak = max(period_times, key=period_times.get)
    return {'peak_period': peak, 'period_counts': dict(period_counts), 'period_times': {k: round(v) for k, v in period_times.items()}}

def compute_stop_scores(tracks, segments, date_from, date_to, projected_crs=GEO.projected_crs, buffer_meters=GEO.buffer_meters):
    """
    Calcula scores de parada/lentidão por segmento.

    Segue o mesmo padrão de spatial join do compute_segment_missed_days,
    mas em vez de contar dias coletados, acumula pesos de velocidade
    baixa e agrupa em sessões de parada.

    Args:
        tracks: GeoDataFrame com pontos GPS (geometry, day, vehicle_id,
                DATAHORA, VELOCIDADE)
        segments: GeoDataFrame de segmentos ideais
        date_from: Data inicial
        date_to: Data final
        projected_crs: CRS projetado (metros)
        buffer_meters: Raio do buffer para match

    Returns:
        GeoDataFrame com colunas de stop scores por segmento
    """
    t = tracks.copy()
    if 'day' not in t.columns:
        raise RuntimeError("tracks must contain a 'day' column")
    t['day'] = pd.to_datetime(t['day']).dt.normalize()
    t = t[(t['day'] >= pd.to_datetime(date_from) & t['day'] <= pd.to_datetime(date_to))].copy()
    has_vehicle_id = 'vehicle_id' in t.columns
    has_datahora = 'DATAHORA' in t.columns
    has_velocidade = 'VELOCIDADE' in t.columns
    if not has_velocidade:
        logger.warning('Coluna VELOCIDADE ausente nos tracks — stops_mode requer dados de velocidade')
    seg = segments.copy()
    if 'segment_id' not in seg.columns:
        seg = seg.reset_index().rename(columns={'index': 'segment_id'})
    def _find_route_code(row):
        for c in ('route_code_individual', 'code_full', 'route_code', 'route_code_used', 'route_name'):
            if c in row and pd.notna(row[c]):
                val = str(row[c]).strip()
                if val and '; ' not in val:
                    return val
        for c in ('route_codes_all', 'code_full'):
            if c in row and pd.notna(row[c]):
                val = str(row[c]).strip()
                if val:
                    return val.split('; ')[0]
        return ''

    seg['route_code_used'] = seg.apply(_find_route_code, axis=1)
    def _expected_dates_for_row(row):
        code = row.get('route_code_used', '')
        dates = []
        if code:
            try:
                dates = compute_schedule_days_for_code(code, date_from, date_to)
            except Exception:
                dates = []
        if not dates:
            wset = row.get('weekday_set', set())
            if isinstance(wset, set) and wset:
                rng = pd.date_range(start=pd.to_datetime(date_from), end=pd.to_datetime(date_to), freq='D')
                dates = [d.normalize() for d in rng if d.weekday() in wset]
        return sorted(set((pd.to_datetime(d).normalize() for d in dates)))

    seg['expected_dates'] = seg.apply(_expected_dates_for_row, axis=1)
    seg['total_days'] = seg['expected_dates'].apply(len)
    seg = seg[seg['total_days'] > 0].copy()
    if seg.empty:
        return _empty_stops_result(segments)
    seg_proj = seg.to_crs(projected_crs).copy()
    seg_proj['_seg_length_m'] = seg_proj.geometry.length
    seg_proj['geometry'] = seg_proj.geometry.buffer(buffer_meters)
    t_proj = t.to_crs(projected_crs) if not t.empty else gpd.GeoDataFrame(columns=t.columns, geometry='geometry', crs=projected_crs)
    track_cols = ['geometry', 'day']
    if has_vehicle_id:
        track_cols.append('vehicle_id')
    if has_datahora:
        track_cols.append('DATAHORA')
    if has_velocidade:
        track_cols.append('VELOCIDADE')
    if not t_proj.empty and not seg_proj.empty:
        left = t_proj[[c for c in track_cols if c in t_proj.columns]].copy()
        right = seg_proj[['segment_id', 'geometry']].copy()
        j = gpd.sjoin(left, right, how='inner', predicate='intersects')
        if not j.empty:
            j['day'] = pd.to_datetime(j['day']).dt.normalize()
    else:
        j = gpd.GeoDataFrame(columns=['geometry', 'day', 'segment_id'])
    stop_weight_by_seg = defaultdict(float)
    stop_points_by_seg = defaultdict(list)
    if not j.empty and has_velocidade:
        sids_arr = j['segment_id'].values
        vehicles_arr = j['vehicle_id'].astype(str).map(_extract_plate).values if has_vehicle_id else np.full(len(j), 'Desconhecido')
        speeds_arr = pd.to_numeric(j['VELOCIDADE'], errors='coerce').values
        dt_arr = pd.to_datetime(j['DATAHORA'], errors='coerce', dayfirst=True).values if has_datahora else pd.to_datetime(j['day']).values
        for i in range(len(sids_arr)):
            sid = sids_arr[i]
            spd = speeds_arr[i]
            w = _compute_stop_weight(spd)
            if w > 0:
                stop_weight_by_seg[sid] += w
            if not np.isnan(spd) and spd <= STOPS.speed_stop_threshold:
                stop_points_by_seg[sid].append({'vehicle': vehicles_arr[i], 'dt': pd.Timestamp(dt_arr[i]), 'speed': float(spd)})
            continue
    elif not j.empty and not has_velocidade:
        logger.warning('Tracks sem coluna VELOCIDADE — scores serão todos 0')
    seg_length_map = dict(zip(seg_proj['segment_id'], seg_proj['_seg_length_m']))
    rows = []
    for _, r in seg.iterrows():
        sid = r['segment_id']
        raw_score = stop_weight_by_seg.get(sid, 0.0)
        seg_len = seg_length_map.get(sid, 1.0)
        if STOPS.normalize_by_length:
            pass
        density = (raw_score / seg_len) if seg_len > 0 else raw_score
        pts_df = pd.DataFrame(stop_points_by_seg.get(sid, []))
        sessions = _build_stop_sessions(pts_df) if not pts_df.empty else []
        total_stops = len(sessions)
        total_stop_time = sum((s['duration_sec'] for s in sessions))
        avg_stop_time = (total_stop_time / total_stops) if total_stops > 0 else 0
        vehicle_details = _aggregate_vehicle_details(sessions)
        period_analysis = _compute_peak_period(sessions)
        days_with_stops = set()
        for s in sessions:
            if hasattr(s['start'], 'date'):
                days_with_stops.add(s['start'].normalize() if hasattr(s['start'], 'normalize') else pd.Timestamp(s['start'].date()))
        stats = {'segment_id': sid, 'route_code_used': r.get('route_code_used', ''), 'total_days': int(r['total_days']), 'stop_score_raw': round(raw_score, 4), 'stop_density': round(density, 6), 'segment_length_m': round(seg_len, 1), 'total_stops': total_stops, 'total_stop_time_sec': round(total_stop_time), 'avg_stop_time_sec': round(avg_stop_time), 'n_days_with_stops': len(days_with_stops), 'vehicle_details': vehicle_details, 'peak_period': period_analysis['peak_period'], 'period_counts': period_analysis['period_counts'], 'period_times': period_analysis['period_times']}
        if 'geometry' in r and r['geometry'] is not None:
            stats['geometry'] = r['geometry']
        for k in ('weekday_set', 'schedule_label', 'code_full', 'route_code_individual', 'route_codes_all'):
            if k in r and pd.notna(r.get(k)):
                stats[k] = r[k]
        rows.append(stats)
    if not rows:
        return _empty_stops_result(segments)
    result = gpd.GeoDataFrame(pd.DataFrame(rows), geometry='geometry', crs=segments.crs)
    density_col = 'stop_density'
    if result[density_col].max() > 0:
        cap = result[density_col].quantile(STOPS.normalization_percentile)
        if cap > 0:
            result['stop_ratio'] = (result[density_col] / cap).clip(0.0, 1.0)
        else:
            result['stop_ratio'] = 0.0
    else:
        result['stop_ratio'] = 0.0
    logger.info(f"  [stops] {len(result)} segmentos | max_density={result[density_col].max():.4f} | total_sessions={result['total_stops'].sum()}")
    return result

def _empty_stops_result(segments):
    """Retorna GeoDataFrame vazio com schema correto para stops."""
    cols = ['segment_id', 'route_code_used', 'total_days', 'stop_score_raw', 'stop_density', 'segment_length_m', 'stop_ratio', 'total_stops', 'total_stop_time_sec', 'avg_stop_time_sec', 'n_days_with_stops', 'vehicle_details', 'peak_period', 'period_counts', 'period_times', 'geometry']
    if not segments.empty:
        pass
    else:
        pass
    return gpd.GeoDataFrame(cols, columns='geometry', geometry=segments.crs, crs=None)

def build_stops_for_map(segments_ideal, segments_stats, sectors):
    """
    Produz segmentos com scores de parada prontos para visualização.

    O setor de cada segmento é o da sua ROTA — mesma regra de
    build_segments_for_map (stats_module.route_sector_join).

    Args:
        segments_ideal: Segmentos base (geometria original)
        segments_stats: Resultado de compute_stop_scores()
        sectors: Setores filtrados por agenda

    Returns:
        GeoDataFrame pronto para plotting com stop_ratio + detalhes
    """
    seg = segments_ideal.copy()
    sec = sectors.copy()
    stats_cols = ['segment_id', 'total_days', 'stop_score_raw', 'stop_density', 'stop_ratio', 'segment_length_m', 'total_stops', 'total_stop_time_sec', 'avg_stop_time_sec', 'n_days_with_stops', 'vehicle_details', 'peak_period', 'period_counts', 'period_times']
    available = [c for c in stats_cols if c in segments_stats.columns]
    seg = seg.merge(segments_stats[available], on='segment_id', how='left')
    for col, default in (('stop_ratio', 0.0), ('stop_score_raw', 0.0), ('stop_density', 0.0), ('total_stops', 0), ('total_stop_time_sec', 0), ('avg_stop_time_sec', 0), ('n_days_with_stops', 0)):
        if col in seg.columns:
            seg[col] = seg[col].fillna(default)
    if seg.crs != sec.crs:
        sec = sec.to_crs(seg.crs)
    from project_heatmaps.stats_module import route_sector_join
    join = route_sector_join(seg, sec)
    if join.empty:
        return gpd.GeoDataFrame(columns=(list(seg.columns) + ['sector_code']), geometry='geometry', crs=seg.crs)
    rows = []
    for r in join.itertuples(index=False):
        route_code = r.rota_codigo
        vd = getattr(r, 'vehicle_details', None)
        vd_list = vd if isinstance(vd, list) else []
        pc = getattr(r, 'period_counts', None)
        pc_dict = pc if isinstance(pc, dict) else {}
        pt = getattr(r, 'period_times', None)
        pt_dict = pt if isinstance(pt, dict) else {}
        row_data = {'segment_id': r.segment_id, 'geometry': r.geometry, 'route_code_full': route_code, 'sector_code': r.sector_name, 'weekday_set': r.weekday_set_route, 'route_schedule_label': r.schedule_label_route, 'sector_schedule_label': r.schedule_label_sector, 'total_days': getattr(r, 'total_days', 0), 'stop_ratio': getattr(r, 'stop_ratio', 0.0), 'stop_score_raw': getattr(r, 'stop_score_raw', 0.0), 'stop_density': getattr(r, 'stop_density', 0.0), 'segment_length_m': getattr(r, 'segment_length_m', 0.0), 'total_stops': getattr(r, 'total_stops', 0), 'total_stop_time_sec': getattr(r, 'total_stop_time_sec', 0), 'avg_stop_time_sec': getattr(r, 'avg_stop_time_sec', 0), 'n_days_with_stops': getattr(r, 'n_days_with_stops', 0), 'vehicle_details': vd_list, 'peak_period': getattr(r, 'peak_period', None), 'period_counts': pc_dict, 'period_times': pt_dict}
        rows.append(row_data)
    if not rows:
        return gpd.GeoDataFrame(columns=[], geometry='geometry', crs=seg.crs)
    return gpd.GeoDataFrame(rows, geometry='geometry', crs=seg.crs)

def compute_overall_stop_ratio(segments_stats):
    """
    Calcula score médio de paradas (0–1) sobre todos os segmentos.

    Retorna média ponderada pelo comprimento do segmento, para que
    segmentos mais longos tenham mais peso na média geral.
    """
    if segments_stats is None or segments_stats.empty:
        return None
    df = segments_stats.copy()
    if 'stop_ratio' not in df.columns:
        return None
    df = df[df['stop_ratio'].notna()].copy()
    if df.empty:
        return None
    if 'segment_length_m' in df.columns:
        lengths = df['segment_length_m'].fillna(1.0).clip(lower=0.1)
        weighted_avg = float(((df['stop_ratio'] * lengths).sum() / lengths.sum()))
        return weighted_avg
    return float(df['stop_ratio'].mean())

def save_daily_stop_stat(contract_name, overall_stop_ratio, total_stops, total_stop_time_sec, stat_date, day_and_night_mode):
    """
    Salva estatística diária de paradas em Parquet.

    Arquivo: HISTORICO/<simple_name>_stops_stats.parquet
    Colunas: date, stop_pct (0-100), total_stops, total_stop_time_sec
    """
    from project_heatmaps.config import PATHS, simplify_contract_name
    if overall_stop_ratio is None:
        return None
    simple_name, _ = simplify_contract_name(contract_name)
    stats_path = PATHS.history_dir / f"{simple_name}_stops_stats.parquet"
    row = {'date': stat_date.strftime('%Y-%m-%d'), 'stop_pct': round((float(overall_stop_ratio) * 100.0), 2), 'total_stops': int(total_stops), 'total_stop_time_sec': int(total_stop_time_sec)}
    if stats_path.exists():
        df = pd.read_parquet(stats_path)
        df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
        df = df[df['date'] != row['date']]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])
    df.sort_values('date', inplace=True)
    df.to_parquet(stats_path, index=False)
    logger.info(f"  [stops] Histórico salvo: {stats_path}")
    return str(stats_path)

def load_stop_history(contract_name, day_and_night_mode):
    """
    Lê histórico de paradas do contrato.

    Retorna DataFrame com colunas (date, stop_pct, total_stops,
    total_stop_time_sec) ou None.
    """
    from project_heatmaps.config import PATHS, simplify_contract_name
    simple_name, _ = simplify_contract_name(contract_name)
    stats_path = PATHS.history_dir / f"{simple_name}_stops_stats.parquet"
    if not stats_path.exists():
        return None
    try:
        df = pd.read_parquet(stats_path)
    except Exception as e:
        logger.warning(f"Falha ao ler histórico de paradas para {contract_name}: {e}")
        return None
    if df.empty or 'date' not in df.columns or 'stop_pct' not in df.columns:
        return None
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.sort_values('date')
    return df

