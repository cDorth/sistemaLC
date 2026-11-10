"""
Funções de geração de visualizações e mapas interativos.

VERSÃO LEAFLET - Alta performance sem perda de qualidade.
Usa html_templates.py para estrutura de sidebar/logos (como plotting_original.py).

Vantagens do Leaflet sobre Plotly para mapas geoespaciais:
- Canvas rendering nativo (não cria elementos DOM por feature)
- Popups carregados sob demanda
- Filtros via visibility (instantâneo)
- Suporte nativo a milhares de geometrias
- Zoom e pan muito mais fluidos
"""

import os
import sys
import json
import gzip
import base64
from pathlib import Path
from datetime import date
from typing import Optional, Dict, List, Tuple, Sequence
import numpy as np
import pandas as pd
import geopandas as gpd
from project_heatmaps.config import COLORS, PATHS, simplify_contract_name, GEO
from project_heatmaps.history import load_collection_history
from project_heatmaps.parsers import sector_code_sort_key
from project_heatmaps.sector_labels import SECTOR_LABELS_CSS, SECTOR_LABELS_JS, add_labels_to_features, add_stats_to_features, sector_label_code, sector_label_points, sector_stats, shared_sector_keys
from project_heatmaps.basemap_cache import bloco_camada_base
from project_heatmaps.web_assets import leaflet_css_inline, leaflet_js_inline
from project_heatmaps.html_templates import get_styles, get_sidebar_template
from project_heatmaps.data_security import prepare_passage_data
from project_heatmaps.secure_integration import build_secure_html_scripts
from project_heatmaps.ai_poison import inject_ai_poison
from project_heatmaps.watermark import get_html_watermark_overlay
from project_heatmaps.priorities import load_priority_edges, flag_priority_segments

def prepare_map_data_leaflet(segments_map, sectors, single_day_mode, contract_name=None):
    """Prepara dados para visualização com Leaflet."""
    seg = segments_map.copy()
    sec = sectors.copy()
    if seg.crs and seg.crs.to_epsg() != 4326:
        seg = seg.to_crs(4326)
    if sec.crs and sec.crs.to_epsg() != 4326:
        sec = sec.to_crs(4326)
    seg = seg[((~seg.geometry.is_empty) & seg.geometry.notna())].copy()
    sec = sec[((~sec.geometry.is_empty) & sec.geometry.notna())].copy()
    seg['total_days'] = seg['total_days'].replace(0, np.nan)
    seg['miss_ratio'] = (seg['missed_days'] / seg['total_days'])
    seg['collected_ratio'] = (1.0 - seg['miss_ratio'])
    if 'classification' not in seg.columns:
        seg['classification'] = ''
    seg['classification'] = seg['classification'].fillna('').astype(str).str.lower().str.strip()
    seg['is_manual'] = seg['classification'] == 'manual'
    for col in ('n_gps_gap_days', 'n_days_possible_manual', 'n_days_manual_collected'):
        if col not in seg.columns:
            seg[col] = 0
        seg[col] = seg[col].fillna(0).astype(int)
    if single_day_mode:
        def classify_single_day(row):
            is_manual = row.get('is_manual', False)
            visited = row.get('n_days_visited', 0) > 0
            n_gps_gap = row.get('n_gps_gap_days', 0)
            n_possible_manual = row.get('n_days_possible_manual', 0)
            n_manual_collected = row.get('n_days_manual_collected', 0)
            if is_manual:
                if n_manual_collected > 0:
                    return 'manual_collected'
                if n_gps_gap > 0:
                    return 'manual_gps_gap'
                return 'manual_not_collected'
            if visited:
                return 'collected'
            if n_possible_manual > 0:
                return 'possible_manual'
            if n_gps_gap > 0:
                return 'possible_gps_gap'
            return 'not_collected'

        seg['viz_class'] = seg.apply(classify_single_day, axis=1)
        _single_day_color_map = {'collected': COLORS.single_day_colors['collected'], 'not_collected': COLORS.single_day_colors['not_collected'], 'possible_gps_gap': COLORS.single_day_colors['possible_gps_gap'], 'possible_manual': COLORS.possible_manual_color, 'manual_collected': COLORS.manual_single_day_colors['collected'], 'manual_not_collected': COLORS.manual_single_day_colors['not_collected'], 'manual_gps_gap': COLORS.manual_single_day_colors['possible_gps_gap']}
        seg['color'] = seg['viz_class'].map(_single_day_color_map)
    else:
        def get_color_by_ratio(row):
            ratio = row.get('collected_ratio')
            is_manual = row.get('is_manual', False)
            n_possible_manual = row.get('n_days_possible_manual', 0)
            total = row.get('total_days', 0)
            if pd.isna(ratio):
                return '#808080'
            if not is_manual and n_possible_manual > 0 and row.get('n_days_visited', 0) == 0:
                return COLORS.possible_manual_color
            colors = COLORS.manual_period_colors if is_manual else None
            if colors:
                if ratio >= 0.8:
                    return colors['80-100']
                if ratio >= 0.6:
                    return colors['60-80']
                if ratio >= 0.4:
                    return colors['40-60']
                if ratio >= 0.2:
                    return colors['20-40']
                return colors['0-20']
            if ratio >= 0.8:
                return '#00ff00'
            if ratio >= 0.6:
                return '#ffff00'
            if ratio >= 0.4:
                return '#ffa500'
            if ratio >= 0.2:
                return '#ff0000'
            return '#4b0000'

        seg['color'] = seg.apply(get_color_by_ratio, axis=1)
        def get_category(row):
            ratio = row.get('collected_ratio')
            is_manual = row.get('is_manual', False)
            if pd.isna(ratio):
                return 'unknown'
            prefix = 'manual_' if is_manual else ''
            if ratio >= 0.8:
                return f"{prefix}80-100"
            if ratio >= 0.6:
                return f"{prefix}60-80"
            if ratio >= 0.4:
                return f"{prefix}40-60"
            if ratio >= 0.2:
                return f"{prefix}20-40"
            return f"{prefix}0-20"

        seg['category'] = seg.apply(get_category, axis=1)
        seg['flag_once'] = (seg['n_days_visited'] == 1 & seg['total_days'] > 2)
        seg['flag_never'] = seg['n_days_visited'] == 0
    if contract_name:
        priority_edges = load_priority_edges(contract_name)
        seg = flag_priority_segments(seg, priority_edges)
    else:
        seg['is_priority'] = False
    return (seg, sec)

def segments_to_geojson(segments, single_day_mode, date_from):
    """Converte segmentos para GeoJSON com todas as propriedades para hover."""
    features = []
    date_str = date_from.strftime('%d/%m/%Y')
    for idx_num, (idx, row) in enumerate(segments.iterrows()):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        total = int((row.get('total_days', 0) or 0))
        visited = int((row.get('n_days_visited', 0) or 0))
        missed = int((row.get('missed_days', 0) or 0))
        n_gps_gap = int((row.get('n_gps_gap_days', 0) or 0))
        n_poss_manual = int((row.get('n_days_possible_manual', 0) or 0))
        missed_with_gps = max(0, ((missed - n_gps_gap) - n_poss_manual))
        pct = round(((1 - (missed / total)) * 100)) if total > 0 else None
        pct_gps = round(((1 - (missed_with_gps / total)) * 100)) if total > 0 else None
        props = {'route_code': str((row.get('route_code_full', '') or '')), 'sector_key': sector_label_code(str((row.get('route_code_full', '') or ''))), 'schedule': str((row.get('route_schedule_label', '') or '')), 'sector': str((row.get('sector_code', '') or '')), 'classification': str((row.get('classification', '') or '')), 'is_manual': bool(row.get('is_manual', False)), 'is_priority': bool(row.get('is_priority', False)), 'street_name': next((str(v) for v in (row.get('name'), row.get('street_name')) if v is not None and v == v and str(v).strip()), ''), 'total_days': total, 'visited': visited, 'missed': missed, 'missed_gps': missed_with_gps, 'n_gps_gap': n_gps_gap, 'pct': pct, 'pct_gps': pct_gps, 'len_m': round(float((row.get('segment_length_m', 0) or 0)), 1), 'color': row.get('color', '#808080'), 'date_str': date_str, 'dwell_seconds': round(float((row.get('dwell_seconds', 0) or 0)), 0), 'vicinity_seconds': round(float((row.get('vicinity_seconds', 0) or 0)), 0)}
        if single_day_mode:
            if visited > 0:
                pass
            else:
                pass
            props['status'] = row.get('viz_class', 'collected', 'possible_gps_gap' if n_gps_gap > 0 else 'not_collected')
        else:
            props['category'] = row.get('category', 'unknown')
            props['flag_once'] = bool(row.get('flag_once', False))
            props['flag_never'] = bool(row.get('flag_never', False))
        props['fid'] = idx_num
        props['shared'] = list((row.get('_shared') or []))
        pd_val = row.get('passage_details')
        if isinstance(pd_val, list) and pd_val:
            seg_vehicles = sorted({str(entry.get('v')) for entry in pd_val if isinstance(entry, dict) and entry.get('v')})
        else:
            seg_vehicles = []
        props['vehicles'] = seg_vehicles
        try:
            geojson_geom = geom.__geo_interface__
            features.append({'type': 'Feature', 'geometry': geojson_geom, 'properties': props})
        except Exception:
            pass
        continue
    return {'type': 'FeatureCollection', 'features': features}

def sectors_to_geojson(sectors, active_days):
    """Converte setores para GeoJSON (polígonos completos)."""
    from project_heatmaps.parsers import get_sc_base_prefix
    def sector_is_active(days_set):
        if (not isinstance(days_set, set) or not days_set):
            return False
        return len((days_set & active_days)) > 0

    filtered = sectors[sectors['weekday_set'].apply(sector_is_active)].copy()
    features = []
    for idx, row in filtered.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        prefix = get_sc_base_prefix(str(row.get('sector_prefix', '')))
        color = COLORS.sector_colors.get(prefix, COLORS.default_sector_color)
        props = {'name': str(row.get('sector_name', '')), 'prefix': prefix, 'color': color, 'schedule': str((row.get('schedule_label', '') or ''))}
        try:
            geojson_geom = geom.__geo_interface__
            features.append({'type': 'Feature', 'geometry': geojson_geom, 'properties': props})
        except Exception:
            continue
    add_labels_to_features(features, sector_label_points(filtered))
    return {'type': 'FeatureCollection', 'features': features}

def build_filter_options(segments, single_day_mode):
    """Constrói opções de filtro."""
    filters = {'byRatio': [], 'byCode': []}
    if single_day_mode:
        filters['byRatio'] = [{'label': 'Todos', 'value': 'all'}, {'label': 'Coletadas', 'value': 'collected'}, {'label': 'Possível falha de GPS', 'value': 'possible_gps_gap'}, {'label': 'Não coletadas', 'value': 'not_collected'}, {'label': 'Possível coleta manual', 'value': 'possible_manual'}, {'label': 'Manual: coletada', 'value': 'manual_collected'}, {'label': 'Manual: poss. falha GPS', 'value': 'manual_gps_gap'}, {'label': 'Manual: não coletada', 'value': 'manual_not_collected'}]
    else:
        filters['byRatio'] = [{'label': 'Todos', 'value': 'all'}, {'label': '80-100%', 'value': '80-100'}, {'label': '60-80%', 'value': '60-80'}, {'label': '40-60%', 'value': '40-60'}, {'label': '20-40%', 'value': '20-40'}, {'label': '0-20%', 'value': '0-20'}, {'label': 'Apenas 1 visita', 'value': 'once'}, {'label': 'Nunca coletado', 'value': 'never'}, {'label': 'Possível coleta manual', 'value': 'possible_manual'}, {'label': 'Manual: 80-100%', 'value': 'manual_80-100'}, {'label': 'Manual: 60-80%', 'value': 'manual_60-80'}, {'label': 'Manual: 40-60%', 'value': 'manual_40-60'}, {'label': 'Manual: 20-40%', 'value': 'manual_20-40'}, {'label': 'Manual: 0-20%', 'value': 'manual_0-20'}]
    if 'route_code_full' in segments.columns:
        codes = sorted(segments['route_code_full'].dropna().astype(str).unique().tolist(), key=sector_code_sort_key)
        codes = [c for c in codes if c.strip()]
        filters['byCode'] = [{'label': 'Todos os setores', 'value': 'all'}]
        filters['byCode'].extend([{'label': f"Apenas {code}", 'value': code} for code in codes])
    vehicles = set()
    if 'passage_details' in segments.columns:
        for pd_val in segments['passage_details']:
            if isinstance(pd_val, list):
                for entry in pd_val:
                    if isinstance(entry, dict):
                        v = entry.get('v')
                        if v:
                            vehicles.add(str(v))
                    continue
            continue
    if vehicles:
        filters['byVehicle'] = [{'label': 'Todos os veículos', 'value': 'all'}]
        filters['byVehicle'].extend([{'label': v, 'value': v} for v in sorted(vehicles)])
    else:
        filters['byVehicle'] = [{'label': 'Nenhum veículo disponível', 'value': 'all'}]
    return filters

def prepare_history_data(history_df, date_from, date_to, single_day_mode, overall_ratio, overall_with_manual=None):
    """Prepara dados de histórico."""
    history_dates = []
    history_pcts = []
    history_pcts_gps = []
    month_start = None
    month_end = None
    if history_df is not None:
        try:
            history_df['date'] = pd.to_datetime(history_df['date'])
            base_dt = pd.to_datetime(date_from)
            month_start = base_dt.replace(day=1).normalize()
            month_end = (month_start + pd.offsets.MonthEnd(0)).normalize()
            if single_day_mode:
                dfm = history_df[(history_df['date'] >= month_start & history_df['date'] <= month_end)].sort_values('date')
            else:
                dfm = history_df[(history_df['date'] >= pd.to_datetime(date_from) & history_df['date'] <= pd.to_datetime(date_to))].sort_values('date')
            history_dates = dfm['date'].dt.strftime('%Y-%m-%d').tolist()
            history_pcts = [float(x) for x in dfm['pct'].tolist()]
            if 'pct_gps' in dfm.columns:
                history_pcts_gps = [float(x) for x in dfm['pct_gps'].tolist()]
            else:
                history_pcts_gps = history_pcts.copy()
        except Exception:
            pass
    if isinstance(overall_ratio, (tuple, list)):
        overall_strict = float(overall_ratio[0]) if overall_ratio[0] is not None else None
        if len(overall_ratio) > 1:
            pass
        overall_gps = float(overall_ratio[1]) if overall_ratio[1] is not None else overall_strict
    elif overall_ratio is not None:
        overall_strict = float(overall_ratio)
        overall_gps = overall_strict
    else:
        overall_strict = None
        overall_gps = None
    if isinstance(overall_with_manual, (tuple, list)):
        owm_strict = float(overall_with_manual[0]) if overall_with_manual[0] is not None else None
        if len(overall_with_manual) > 1:
            pass
        owm_gps = float(overall_with_manual[1]) if overall_with_manual[1] is not None else owm_strict
    elif overall_with_manual is not None:
        owm_strict = float(overall_with_manual)
        owm_gps = owm_strict
    else:
        owm_strict = None
        owm_gps = None
    if month_start:
        pass
    else:
        pass
    if month_end:
        pass
    else:
        pass
    if single_day_mode:
        pass
    else:
        pass
    if single_day_mode:
        pass
    else:
        pass
    return {'dates': None, 'pcts': month_end.strftime('%Y-%m-%d'), 'pcts_gps': None, 'month_start': date_from.strftime('%Y-%m-%d'), 'month_end': None, 'current_day': date_from.strftime('%Y-%m-%d'), 'period_from': date_to.strftime('%Y-%m-%d'), 'period_to': 'single', 'mode': 'range', 'overall': overall_strict, 'overall_gps': overall_gps, 'overall_manual': owm_strict, 'overall_manual_gps': owm_gps}

def get_leaflet_styles():
    """Retorna CSS adicional especifico para Leaflet."""
    return (('<style>' + SECTOR_LABELS_CSS) + """</style>
    <style>
        /* Faz o mapa Leaflet ocupar todo o container */
        #leafletMap {
            width: 100%;
            height: 100%;
            position: absolute;
            top: 0;
            left: 0;
        }
        
        /* Titulo do mapa */
        #map-title {
            position: absolute;
            top: 10px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(255,255,255,0.95);
            padding: 10px 20px;
            border-radius: 5px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            z-index: 1000;
            font-size: 16px;
            font-weight: bold;
            color: #333;
            white-space: nowrap;
            pointer-events: none;
        }
        
        /* Legenda — posicionada na lateral esquerda via JS (abaixo do geocoder) */
        #legend {
            position: absolute;
            left: 10px;
            background: rgba(255,255,255,0.95);
            padding: 12px;
            border-radius: 5px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            z-index: 1000;
            font-size: 12px;
            /* top definido dinamicamente via JS */
        }
        
        #legend h4 {
            margin: 0 0 8px 0;
            font-size: 13px;
            color: #333;
        }
        
        .legend-item {
            display: flex;
            align-items: center;
            margin: 5px 0;
        }
        
        .legend-color {
            width: 25px;
            height: 4px;
            margin-right: 8px;
            border-radius: 2px;
        }
        
        /* Popup e Tooltip coloridos */
        .leaflet-popup-content-wrapper {
            border-radius: 8px;
            box-shadow: 0 3px 14px rgba(0,0,0,0.3);
            padding: 0;
        }
        
        .leaflet-popup-content {
            margin: 0;
            padding: 0;
        }
        
        .popup-content {
            padding: 10px 12px;
            line-height: 1.6;
            font-size: 12px;
        }
        
        .popup-content b {
            color: #333;
        }
        
        .popup-header {
            padding: 8px 12px;
            border-radius: 8px 8px 0 0;
            margin: 0;
            font-weight: bold;
            font-size: 13px;
        }
        
        /* Tooltip colorido */
        .colored-tooltip {
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            padding: 0 !important;
        }
        
        .tooltip-inner {
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: bold;
            white-space: nowrap;
            box-shadow: 0 2px 6px rgba(0,0,0,0.3);
        }
        
        /* Controles Leaflet — Zoom Slider */
        .leaflet-control-zoom {
            display: none !important;
        }

        /* Botão de resetar visualização (acima do zoom slider) */
        .reset-view-container {
            background: rgba(255,255,255,.95);
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,.2);
            padding: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .reset-view-btn {
            width: 38px;
            height: 38px;
            border: none;
            background: transparent;
            cursor: pointer;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #333;
            transition: background 0.15s;
        }
        .reset-view-btn:hover {
            background: #eee;
        }
        .reset-view-btn:active {
            background: #ddd;
        }
        .reset-view-btn svg {
            width: 22px;
            height: 22px;
        }
        
        .zoom-slider-container {
            background: rgba(255,255,255,.95);
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,.2);
            padding: 6px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 4px;
        }
        .zoom-slider-btn {
            width: 34px; height: 34px;
            border: none; background: transparent;
            font-size: 20px; font-weight: bold;
            cursor: pointer; color: #333;
            border-radius: 4px;
            display: flex; align-items: center; justify-content: center;
            user-select: none; -webkit-user-select: none;
        }
        .zoom-slider-btn:hover { background: #eee; }
        .zoom-slider-btn:active { background: #ddd; }
        .zoom-slider-track {
            width: 8px; height: 150px;
            background: #ddd; border-radius: 4px;
            position: relative; cursor: pointer;
        }
        .zoom-slider-fill {
            position: absolute; bottom: 0; width: 100%;
            background: #4A90D9; border-radius: 4px;
            pointer-events: none; transition: height 0.15s ease;
        }
        .zoom-slider-thumb {
            position: absolute; left: 50%;
            width: 18px; height: 18px;
            background: #fff; border: 2px solid #4A90D9;
            border-radius: 50%;
            transform: translate(-50%, 50%);
            pointer-events: none;
            box-shadow: 0 1px 4px rgba(0,0,0,.2);
            transition: bottom 0.15s ease;
        }
        
        /* ==================== ESTILOS DO GEOCODER ==================== */
        /* Container - posicionado dinamicamente via JS */
        #geocoder-container {
            position: absolute;
            left: 10px;
            z-index: 20000;
            display: flex;
            align-items: center;
            gap: 0;
        }
        
        /* Botao da lupa */
        #geocoder-toggle {
            width: 36px;
            height: 36px;
            background: rgba(255,255,255,0.95);
            border: none;
            border-radius: 8px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.2);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }
        
        #geocoder-toggle:hover {
            background: #fff;
            box-shadow: 0 2px 8px rgba(0,0,0,0.25);
        }
        
        #geocoder-toggle svg {
            width: 18px;
            height: 18px;
            color: #555;
        }
        
        /* Animacao de loading */
        #geocoder-toggle.loading svg {
            animation: spin 1s linear infinite;
        }
        
        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        
        /* Wrapper do input - oculto por padrao */
        #geocoder-input-wrapper {
            display: none;
            background: rgba(255,255,255,0.95);
            border-radius: 0 8px 8px 0;
            box-shadow: 0 2px 6px rgba(0,0,0,0.2);
            margin-left: -4px;
            overflow: hidden;
        }
        
        #geocoder-container.expanded #geocoder-input-wrapper {
            display: block;
        }
        
        #geocoder-container.expanded #geocoder-toggle {
            border-radius: 8px 0 0 8px;
        }
        
        #geocoder-input {
            width: 220px;
            padding: 8px 12px;
            font-size: 14px;
            border: none;
            outline: none;
            background: transparent;
        }
        
        #geocoder-input::placeholder {
            color: #999;
        }
        
        /* Lista de resultados */
        #geocoder-results {
            position: absolute;
            top: 100%;
            left: 0;
            width: 280px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
            max-height: 300px;
            overflow-y: auto;
            display: none;
            z-index: 20001;
            margin-top: 4px;
        }
        
        #geocoder-results.active {
            display: block;
        }
        
        #geocoder-results .result-item {
            padding: 10px 12px;
            cursor: pointer;
            border-bottom: 1px solid #eee;
            font-size: 13px;
            color: #333;
            line-height: 1.4;
        }
        
        #geocoder-results .result-item:hover {
            background: #f5f5f5;
        }
        
        #geocoder-results .result-item:last-child {
            border-bottom: none;
        }
        
        #geocoder-results .result-item .result-main {
            font-weight: 500;
            color: #222;
        }
        
        #geocoder-results .result-item .result-secondary {
            font-size: 11px;
            color: #666;
            margin-top: 2px;
        }
        
        #geocoder-results .no-results {
            padding: 12px;
            color: #999;
            font-style: italic;
            text-align: center;
        }
        
        /* Marcador de busca */
        .search-marker-popup {
            font-size: 13px;
            line-height: 1.5;
        }
        
        .search-marker-popup strong {
            color: #007bff;
        }
        
        /* ==================== DETAIL SIDEBAR (por cima do sidebar principal) ==================== */
        #detail-sidebar {
            position: fixed;
            top: 0;
            right: 0;
            width: 380px;
            height: 100vh;
            background: #fff;
            border-left: 1px solid #ddd;
            overflow-y: auto;
            padding: 0;
            box-shadow: -2px 0 12px rgba(0, 0, 0, 0.12);
            box-sizing: border-box;
            z-index: 10002;
            transform: translateX(380px);
            transition: transform .26s ease;
        }
        
        #detail-sidebar.open {
            transform: translateX(0);
        }
        
        .detail-header {
            position: sticky;
            top: 0;
            z-index: 1;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 14px;
            border-bottom: 2px solid #eee;
            background: #fff;
        }
        
        .detail-header h3 {
            margin: 0;
            font-size: 14px;
            font-weight: 700;
            color: #111;
        }
        
        .detail-close-btn {
            background: none;
            border: 1px solid #ccc;
            border-radius: 4px;
            width: 28px;
            height: 28px;
            font-size: 16px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #666;
            transition: all .15s;
        }
        
        .detail-close-btn:hover {
            background: #f5f5f5;
            color: #333;
            border-color: #999;
        }
        
        .detail-body {
            padding: 12px 14px;
        }
        
        .detail-segment-info {
            padding: 10px 12px;
            margin-bottom: 12px;
            background: #f8f9fa;
            border-radius: 6px;
            border-left: 4px solid #007bff;
        }
        
        .detail-segment-info .route-code {
            font-size: 15px;
            font-weight: 700;
            color: #111;
            margin-bottom: 4px;
        }
        
        .detail-segment-info .meta {
            font-size: 11px;
            color: #666;
            line-height: 1.6;
        }
        
        .detail-vehicle-card {
            margin-bottom: 10px;
            border: 1px solid #e0e0e0;
            border-radius: 6px;
            overflow: hidden;
        }
        
        .detail-vehicle-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            background: #f0f4f8;
            cursor: pointer;
            user-select: none;
            transition: background .15s;
        }
        
        .detail-vehicle-header:hover {
            background: #e4eaf0;
        }
        
        .detail-vehicle-name {
            font-size: 12px;
            font-weight: 700;
            color: #333;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        .detail-vehicle-name .vehicle-icon {
            font-size: 14px;
        }
        
        .detail-vehicle-badge {
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 10px;
            background: #007bff;
            color: #fff;
            font-weight: 600;
        }
        
        .detail-vehicle-body {
            padding: 0;
            max-height: 0;
            overflow: hidden;
            transition: max-height .3s ease, padding .3s ease;
        }
        
        .detail-vehicle-body.expanded {
            padding: 8px 12px;
            max-height: 500px;
            overflow-y: auto;
        }
        
        .detail-summary-row {
            display: flex;
            justify-content: space-between;
            padding: 4px 0;
            font-size: 11px;
            color: #555;
            border-bottom: 1px solid #f0f0f0;
        }
        
        .detail-summary-row:last-child {
            border-bottom: none;
        }
        
        .detail-summary-row .label {
            font-weight: 600;
            color: #333;
        }
        
        .detail-passage-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 6px;
            font-size: 11px;
        }
        
        .detail-passage-table th {
            text-align: left;
            padding: 4px 6px;
            background: #f8f9fa;
            color: #555;
            font-weight: 600;
            border-bottom: 1px solid #e0e0e0;
            font-size: 10px;
            text-transform: uppercase;
        }
        
        .detail-passage-table td {
            padding: 3px 6px;
            border-bottom: 1px solid #f0f0f0;
            color: #444;
        }
        
        .detail-passage-table tr:last-child td {
            border-bottom: none;
        }
        
        .detail-no-data {
            padding: 20px;
            text-align: center;
            color: #999;
            font-size: 12px;
            font-style: italic;
        }
        
        .detail-vehicle-expand-icon {
            font-size: 11px;
            color: #888;
            transition: transform .2s;
        }
        
        .detail-vehicle-header.active .detail-vehicle-expand-icon {
            transform: rotate(180deg);
        }
    </style>
    """)

def get_leaflet_scripts(segments_json, sectors_json, filters_json, history_json, overall_json, overall_gps_json, overall_manual_json, overall_manual_gps_json, center_lat, center_lon, single_day_mode, passage_b64, pontos_viciados_json='null', basemap_geojson=None):
    """Retorna JavaScript para Leaflet (substitui get_scripts do html_templates)."""
    fontes_basemap = basemap_geojson if basemap_geojson is not None else pontos_viciados_json
    basemap_block = bloco_camada_base(*fontes_basemap)
    return ''.join(["""
    <!-- Leaflet JS -->
    """, f"{leaflet_js_inline()}", """

    <!-- Plotly para o grafico de historico -->
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>

    <script>
        // Dados
        window.SEGMENTS = """, f"{segments_json}", """;
        window.PASSAGE_B64 = \"""", f"{passage_b64}", """\";
        window.PASSAGE_DATA = {};
        window.SECTORS = """, f"{sectors_json}", """;
        window.FILTERS = """, f"{filters_json}", """;
        window.HEATMAP_HISTORY = """, f"{history_json}", """;
        window.HEATMAP_OVERALL = """, f"{overall_json}", """;
        window.HEATMAP_OVERALL_GPS = """, f"{overall_gps_json}", """;
        window.HEATMAP_OVERALL_MANUAL = """, f"{overall_manual_json}", """;
        window.HEATMAP_OVERALL_MANUAL_GPS = """, f"{overall_manual_gps_json}", """;
        window.SINGLE_DAY_MODE = """, f"{json.dumps(single_day_mode)}", """;
        window.PONTOS_VICIADOS = """, f"{pontos_viciados_json}", """;
        
        // Descomprime passage_details sob demanda
        (function() {
            try {
                var b64 = window.PASSAGE_B64;
                var binary = atob(b64);
                var bytes = new Uint8Array(binary.length);
                for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                var ds = new DecompressionStream('gzip');
                var writer = ds.writable.getWriter();
                writer.write(bytes);
                writer.close();
                new Response(ds.readable).text().then(function(text) {
                    window.PASSAGE_DATA = JSON.parse(text);
                    console.log('[OK] Passage details descomprimidos');
                });
            } catch(e) {
                console.warn('Erro ao descomprimir passage_details:', e);
            }
        })();
    </script>
    
    <script>
    (function() {
        'use strict';
        
        var hist = window.HEATMAP_HISTORY || { dates: [], pcts: [] };
        var overall = window.HEATMAP_OVERALL;
        var overallGps = window.HEATMAP_OVERALL_GPS;
        var overallManual = window.HEATMAP_OVERALL_MANUAL;
        var overallManualGps = window.HEATMAP_OVERALL_MANUAL_GPS;
        
        // ==================== UTILITÁRIOS DE COR ====================
        function getContrastColor(hexColor) {
            var hex = hexColor.replace('#', '');
            var r = parseInt(hex.substr(0, 2), 16);
            var g = parseInt(hex.substr(2, 2), 16);
            var b = parseInt(hex.substr(4, 2), 16);
            var luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
            return luminance > 0.5 ? '#000000' : '#ffffff';
        }
        
        function darkenColor(hexColor, percent) {
            var hex = hexColor.replace('#', '');
            var r = parseInt(hex.substr(0, 2), 16);
            var g = parseInt(hex.substr(2, 2), 16);
            var b = parseInt(hex.substr(4, 2), 16);
            r = Math.floor(r * (1 - percent / 100));
            g = Math.floor(g * (1 - percent / 100));
            b = Math.floor(b * (1 - percent / 100));
            return '#' + [r, g, b].map(function(x) { return x.toString(16).padStart(2, '0'); }).join('');
        }
        
        // Resolve status para ícone e texto
        function getStatusInfo(status) {
            var map = {
                'collected':           { icon: '✔', text: 'Coletada' },
                'not_collected':       { icon: '✘', text: 'Não coletada' },
                'possible_gps_gap':    { icon: '❓', text: 'Possível falha de GPS' },
                'possible_manual':     { icon: '🔧', text: 'Possível coleta manual' },
                'manual_collected':    { icon: '✔', text: 'Manual: coletada' },
                'manual_not_collected':{ icon: '✘', text: 'Manual: não coletada' },
                'manual_gps_gap':      { icon: '❓', text: 'Manual: poss. falha GPS' }
            };
            return map[status] || { icon: '?', text: status || 'Desconhecido' };
        }
        
        // Formata tempo em segundos para "Xmin Ys"
        function formatSeconds(sec) {
            if (!sec || sec <= 0) return '0s';
            var m = Math.floor(sec / 60);
            var s = Math.round(sec % 60);
            if (m > 0) return m + 'min ' + s + 's';
            return s + 's';
        }
        
        // Gera HTML de info dwell para segmentos manual/possible_manual
        function dwellInfoHtml(p) {
            if (!p.dwell_seconds && !p.vicinity_seconds) return '';
            var html = '';
            if (p.vicinity_seconds > 0) {
                html += '<b>Tempo nos arredores:</b> ' + formatSeconds(p.vicinity_seconds) + '<br>';
            }
            if (p.dwell_seconds > 0) {
                html += '<b>Tempo parado:</b> ' + formatSeconds(p.dwell_seconds) + '<br>';
            }
            return html;
        }
        
        // ==================== INICIALIZAÇÃO DO MAPA ====================
        var map = L.map('leafletMap', {
            center: [""", f"{center_lat}", ', ', f"{center_lon}", """],
            zoom: 12,
            zoomControl: false,
            preferCanvas: true,
            zoomSnap: 0.25,
            zoomDelta: 0.5,
            wheelPxPerZoomLevel: 120
        });
        
        // Camada base
        // Mapa de fundo: fonte única em config.py (bloco BASEMAP) — o provedor
        // não é nomeado aqui de propósito, já trocou 2x em 2 dias;
        // segunda opção registrada lá: VersaTiles Colorful.
        """, f"{basemap_block}", """

        // Pane dedicado para o halo das ruas prioritárias
        // (zIndex 410 deixa acima de setores/overlay, mas o pane dos segmentos
        // 'priorityTopPane' fica em 420 — halo abaixo da linha colorida)
        map.createPane('priorityHaloPane');
        map.getPane('priorityHaloPane').style.zIndex = 410;
        map.getPane('priorityHaloPane').style.pointerEvents = 'none';
        var priorityHaloRenderer = L.canvas({ pane: 'priorityHaloPane' });

        map.createPane('priorityTopPane');
        map.getPane('priorityTopPane').style.zIndex = 420;
        var priorityTopRenderer = L.canvas({ pane: 'priorityTopPane' });

        map.createPane('pontosTopPane');
        map.getPane('pontosTopPane').style.zIndex = 430;
        // SVG renderer (em vez de canvas) garante que cliques em áreas
        // transparentes do pane caiam nos segmentos abaixo — cada marker
        // SVG só captura cliques sobre seu próprio círculo.
        var pontosTopRenderer = L.svg({ pane: 'pontosTopPane' });
        
        // ==================== GEOCODER CUSTOMIZADO (BUSCA DE ENDERECOS) ====================
        var searchMarker = null;
        var searchTimeout = null;
        var geocoderContainer = document.getElementById('geocoder-container');
        var geocoderToggle = document.getElementById('geocoder-toggle');
        var geocoderInput = document.getElementById('geocoder-input');
        var geocoderResults = document.getElementById('geocoder-results');
        var iconSearch = document.getElementById('geocoder-icon-search');
        var iconLoading = document.getElementById('geocoder-icon-loading');
        var isExpanded = false;
        
        // Layout: reset acima do slider à esquerda; geocoder abaixo do slider; legenda à direita do slider
        function positionLeftPanel() {
            var resetBtn = document.querySelector('.reset-view-container');
            var slider = document.querySelector('.zoom-slider-container');
            var legend = document.getElementById('legend');
            var gap = 10;

            if (slider) {
                var sliderRect = slider.getBoundingClientRect();

                // Geocoder: logo abaixo do slider
                if (geocoderContainer) {
                    geocoderContainer.style.top = (sliderRect.bottom + gap) + 'px';
                }

                // Legenda: alinhada ao topo do controle mais alto à esquerda (reset, se existir)
                if (legend) {
                    var topRect = (resetBtn && resetBtn.getBoundingClientRect)
                        ? resetBtn.getBoundingClientRect()
                        : sliderRect;
                    legend.style.top  = topRect.top + 'px';
                    legend.style.left = (sliderRect.right + gap) + 'px';
                }
            }
        }
        
        // Chama no inicio e observa mudancas
        setTimeout(positionLeftPanel, 200);
        window.addEventListener('resize', positionLeftPanel);
        
        // Re-posiciona ao abrir/fechar o geocoder (muda tamanho)
        if (window.MutationObserver) {
            var panelObserver = new MutationObserver(function() {
                setTimeout(positionLeftPanel, 50);
            });
            if (geocoderContainer) {
                panelObserver.observe(geocoderContainer, { attributes: true, subtree: true });
            }
        }
        
        // Toggle expandir/colapsar
        function toggleGeocoder() {
            isExpanded = !isExpanded;
            if (isExpanded) {
                geocoderContainer.classList.add('expanded');
                setTimeout(function() {
                    geocoderInput.focus();
                }, 100);
            } else {
                geocoderContainer.classList.remove('expanded');
                geocoderResults.classList.remove('active');
                geocoderInput.value = '';
            }
        }
        
        if (geocoderToggle) {
            geocoderToggle.addEventListener('click', function(e) {
                e.stopPropagation();
                toggleGeocoder();
            });
        }
        
        // Mostra/esconde loading
        function setLoading(loading) {
            if (iconSearch && iconLoading) {
                iconSearch.style.display = loading ? 'none' : 'block';
                iconLoading.style.display = loading ? 'block' : 'none';
                if (loading) {
                    geocoderToggle.classList.add('loading');
                } else {
                    geocoderToggle.classList.remove('loading');
                }
            }
        }
        
        // Mapa de estados brasileiros (com e sem acentos)
        var estadosMap = {
            'Acre': 'AC', 'Alagoas': 'AL', 'Amapá': 'AP', 'Amapa': 'AP', 'Amazonas': 'AM',
            'Bahia': 'BA', 'Ceará': 'CE', 'Ceara': 'CE', 'Distrito Federal': 'DF',
            'Espírito Santo': 'ES', 'Espirito Santo': 'ES', 'Goiás': 'GO', 'Goias': 'GO',
            'Maranhão': 'MA', 'Maranhao': 'MA', 'Mato Grosso': 'MT', 'Mato Grosso do Sul': 'MS',
            'Minas Gerais': 'MG', 'Pará': 'PA', 'Para': 'PA', 'Paraíba': 'PB', 'Paraiba': 'PB',
            'Paraná': 'PR', 'Parana': 'PR', 'Pernambuco': 'PE', 'Piauí': 'PI', 'Piaui': 'PI',
            'Rio de Janeiro': 'RJ', 'Rio Grande do Norte': 'RN', 'Rio Grande do Sul': 'RS',
            'Rondônia': 'RO', 'Rondonia': 'RO', 'Roraima': 'RR', 'Santa Catarina': 'SC',
            'São Paulo': 'SP', 'Sao Paulo': 'SP', 'Sergipe': 'SE', 'Tocantins': 'TO'
        };
        
        // Formata endereco de forma limpa: "Rua X, 123, Bairro, Cidade, UF, Brasil"
        function formatAddress(item) {
            var addr = item.address || {};
            var parts = [];
            
            // Rua + numero (prioriza house_number)
            var street = addr.road || addr.street || addr.pedestrian || addr.footway || addr.path || '';
            var number = addr.house_number || '';
            
            if (street) {
                if (number) {
                    parts.push(street + ', ' + number);
                } else {
                    parts.push(street);
                }
            } else if (item.name && item.name !== item.display_name && !item.name.includes('Região')) {
                parts.push(item.name);
            }
            
            // Bairro (apenas um)
            var bairro = addr.suburb || addr.neighbourhood || addr.quarter || '';
            if (bairro && !bairro.includes('Região')) parts.push(bairro);
            
            // Cidade
            var cidade = addr.city || addr.town || addr.village || addr.municipality || '';
            if (cidade) parts.push(cidade);
            
            // Estado (abreviado)
            var estado = addr.state || '';
            var estadoAbrev = estadosMap[estado] || '';
            if (estadoAbrev) parts.push(estadoAbrev);
            
            // Brasil
            parts.push('Brasil');
            
            // Fallback minimo
            if (parts.length <= 1) {
                // Extrai apenas primeiras 4 partes limpas do display_name
                var displayParts = (item.display_name || '').split(',').map(function(p) { return p.trim(); });
                var cleanParts = displayParts.filter(function(p) {
                    return p && !p.includes('Região') && !p.includes('CEP') && !/^\d{5}-?\d{3}$/.test(p);
                }).slice(0, 4);
                return cleanParts.join(', ');
            }
            
            return parts.join(', ');
        }
        
        // Extrai partes principais para exibicao em duas linhas
        function formatAddressParts(item) {
            var addr = item.address || {};
            
            // Linha principal: Rua + numero
            var street = addr.road || addr.street || addr.pedestrian || addr.footway || addr.path || '';
            var number = addr.house_number || '';
            var main = '';
            
            if (street) {
                main = number ? street + ', ' + number : street;
            } else if (item.name && item.name !== item.display_name && !item.name.includes('Região')) {
                main = item.name;
            } else {
                // Extrai primeira parte limpa
                var firstPart = (item.display_name || '').split(',')[0].trim();
                main = firstPart;
            }
            
            // Linha secundaria: Bairro, Cidade, UF
            var secondary = [];
            var bairro = addr.suburb || addr.neighbourhood || addr.quarter || '';
            var cidade = addr.city || addr.town || addr.village || addr.municipality || '';
            var estado = addr.state || '';
            var estadoAbrev = estadosMap[estado] || '';
            
            if (bairro && !bairro.includes('Região')) secondary.push(bairro);
            if (cidade) secondary.push(cidade);
            if (estadoAbrev) secondary.push(estadoAbrev);
            
            return {
                main: main,
                secondary: secondary.join(', ')
            };
        }
        
        function searchAddress(query) {
            if (!query || query.length < 3) {
                geocoderResults.innerHTML = '';
                geocoderResults.classList.remove('active');
                return;
            }
            
            setLoading(true);
            
            // Nominatim API - busca com parametros otimizados para endereco brasileiro
            var url = 'https://nominatim.openstreetmap.org/search?' + 
                'format=json' +
                '&q=' + encodeURIComponent(query) +
                '&countrycodes=br' +
                '&addressdetails=1' +
                '&limit=8' +
                '&accept-language=pt-BR' +
                '&featuretype=settlement,street,house';  // Prioriza enderecos
            
            fetch(url, {
                headers: {
                    'Accept': 'application/json'
                }
            })
            .then(function(response) { return response.json(); })
            .then(function(data) {
                setLoading(false);
                geocoderResults.innerHTML = '';
                
                if (data.length === 0) {
                    geocoderResults.innerHTML = '<div class="no-results">Nenhum resultado encontrado</div>';
                    geocoderResults.classList.add('active');
                    return;
                }
                
                data.forEach(function(item) {
                    var parts = formatAddressParts(item);
                    var div = document.createElement('div');
                    div.className = 'result-item';
                    div.innerHTML = '<div class="result-main">' + parts.main + '</div>' +
                                   (parts.secondary ? '<div class="result-secondary">' + parts.secondary + '</div>' : '');
                    div.onclick = function() {
                        selectAddress(item);
                    };
                    geocoderResults.appendChild(div);
                });
                
                geocoderResults.classList.add('active');
            })
            .catch(function(err) {
                setLoading(false);
                console.error('Erro na busca:', err);
                geocoderResults.innerHTML = '<div class="no-results">Erro na busca</div>';
                geocoderResults.classList.add('active');
            });
        }
        
        function selectAddress(item) {
            // Remove marcador anterior
            if (searchMarker) {
                map.removeLayer(searchMarker);
            }
            
            var lat = parseFloat(item.lat);
            var lon = parseFloat(item.lon);
            var latlng = L.latLng(lat, lon);
            var formattedAddress = formatAddress(item);
            
            // Cria marcador
            searchMarker = L.marker(latlng, {
                title: formattedAddress,
                riseOnHover: true
            }).addTo(map);
            
            // Popup com endereco formatado
            var popupContent = '<div class="search-marker-popup">' +
                '<strong>' + formattedAddress + '</strong><br>' +
                '<small style="color:#666;">Clique para remover</small>' +
                '</div>';
            
            searchMarker.bindPopup(popupContent).openPopup();
            
            // Remove ao clicar
            searchMarker.on('click', function() {
                if (searchMarker) {
                    map.removeLayer(searchMarker);
                    searchMarker = null;
                }
            });
            
            // Navega ate o local
            if (item.boundingbox) {
                var bbox = [
                    [parseFloat(item.boundingbox[0]), parseFloat(item.boundingbox[2])],
                    [parseFloat(item.boundingbox[1]), parseFloat(item.boundingbox[3])]
                ];
                map.fitBounds(bbox, { maxZoom: 17, animate: true });
            } else {
                map.setView(latlng, 17, { animate: true });
            }
            
            // Colapsa e limpa
            isExpanded = false;
            geocoderContainer.classList.remove('expanded');
            geocoderInput.value = '';
            geocoderResults.innerHTML = '';
            geocoderResults.classList.remove('active');
        }
        
        // Event listeners
        if (geocoderInput) {
            geocoderInput.addEventListener('input', function() {
                clearTimeout(searchTimeout);
                var query = this.value.trim();
                searchTimeout = setTimeout(function() {
                    searchAddress(query);
                }, 400);
            });
            
            geocoderInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    clearTimeout(searchTimeout);
                    searchAddress(this.value.trim());
                }
                // ESC fecha o geocoder
                if (e.key === 'Escape') {
                    isExpanded = false;
                    geocoderContainer.classList.remove('expanded');
                    geocoderResults.classList.remove('active');
                }
            });
            
            // Fecha ao clicar fora
            document.addEventListener('click', function(e) {
                if (!e.target.closest('#geocoder-container')) {
                    if (isExpanded) {
                        isExpanded = false;
                        geocoderContainer.classList.remove('expanded');
                    }
                    geocoderResults.classList.remove('active');
                }
            });
        }
        
        // ==================== LAYERS ====================
        // ==================== LAYERS ====================
        var sectorsLayer = null;
        var segmentsLayer = null;
        var priorityHaloLayer = null;
        var pontosLayer = null;
        var allSegmentFeatures = [];
        var allPriorityHalos = [];   // espelha apenas prioritárias
        var currentPriorityMode = 'highlight';  // 'highlight' | 'dim' | 'only'

        var PRIORITY_HALO_STYLE = {
            color: '#000000',
            weight: 8,
            opacity: 0.65,
            lineCap: 'round',
            lineJoin: 'round',
            interactive: false
        };
        var SEGMENT_BASE_OPACITY = 0.9;
        var SEGMENT_DIM_OPACITY = 0.18;
        var PRIORITY_WEIGHT_BOOST = 2;   // soma sobre weight base 3
        
        // ==================== RENDERIZAÇÃO DE SETORES ====================
        function renderSectors() {
            if (sectorsLayer) {
                map.removeLayer(sectorsLayer);
            }
            
            sectorsLayer = L.geoJSON(window.SECTORS, {
                style: function(feature) {
                    return {
                        color: feature.properties.color,
                        weight: 2,
                        opacity: 0.8,
                        fillColor: feature.properties.color,
                        fillOpacity: 0.15
                    };
                },
                onEachFeature: function(feature, layer) {
                    var p = feature.properties;
                    var color = p.color;
                    var textColor = getContrastColor(color);
                    
                    var popupContent = '<div class="popup-header" style="background:' + color + '; color:' + textColor + ';">' + p.name + '</div>' +
                        '<div class="popup-content"><b>Tipo:</b> ' + p.prefix + '<br><b>Agenda:</b> ' + (p.schedule || '—') + '</div>';
                    layer.bindPopup(popupContent);
                    
                    layer.bindTooltip(
                        '<div class="tooltip-inner" style="background:' + color + '; color:' + textColor + ';">' + p.name + '</div>',
                        { permanent: false, direction: 'center', className: 'colored-tooltip' }
                    );
                }
            }).addTo(map);
        }
        
        // ==================== RENDERIZAÇÃO DE SEGMENTOS ====================
        function renderSegments() {
            if (segmentsLayer) {
                map.removeLayer(segmentsLayer);
            }
            if (priorityHaloLayer) {
                map.removeLayer(priorityHaloLayer);
            }

            allSegmentFeatures = [];
            allPriorityHalos = [];

            // Halo das prioritárias (renderizado abaixo da linha colorida)
            var priorityFeatures = (window.SEGMENTS && window.SEGMENTS.features
                ? window.SEGMENTS.features
                : []
            ).filter(function(f) { return f.properties && f.properties.is_priority; });

            priorityHaloLayer = L.geoJSON(
                { type: 'FeatureCollection', features: priorityFeatures },
                {
                    renderer: priorityHaloRenderer,
                    pane: 'priorityHaloPane',
                    style: function() { return PRIORITY_HALO_STYLE; },
                    onEachFeature: function(feature, layer) {
                        allPriorityHalos.push({ layer: layer, props: feature.properties });
                    }
                }
            ).addTo(map);

            segmentsLayer = L.geoJSON(window.SEGMENTS, {
                renderer: priorityTopRenderer,
                pane: 'priorityTopPane',
                style: function(feature) {
                    var p = feature.properties;
                    return {
                        color: p.color,
                        weight: p.is_priority ? (3 + PRIORITY_WEIGHT_BOOST) : 3,
                        opacity: SEGMENT_BASE_OPACITY
                    };
                },
                onEachFeature: function(feature, layer) {
                    var p = feature.properties;
                    var color = p.color;
                    var textColor = getContrastColor(color);
                    var borderColor = darkenColor(color, 30);
                    
                    var priorityBadge = p.is_priority
                        ? '<span style="display:inline-block;background:#000;color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px;margin-left:6px;vertical-align:middle;letter-spacing:0.3px;">🌟 PRIORITÁRIA</span>'
                        : '';
                    var popupContent = '<div class="popup-header" style="background:' + color + '; color:' + textColor + '; border-bottom: 2px solid ' + borderColor + ';">' + p.route_code + priorityBadge + '</div>' +
                        '<div class="popup-content"><b>Agenda da rota:</b> ' + p.schedule + '<br><b>Setor(es):</b> ' + p.sector + '<br>';
                    if (p.street_name) {
                        popupContent += '<b>Rua:</b> ' + p.street_name + '<br>';
                    }
                    
                    if (window.SINGLE_DAY_MODE) {
                        var si = getStatusInfo(p.status);
                        popupContent += '<b>Data:</b> ' + p.date_str + '<br><b>Status:</b> ' + si.icon + ' ' + si.text;
                        if (p.status === 'manual_collected' || p.status === 'manual_not_collected' || p.status === 'manual_gps_gap' || p.status === 'possible_manual') {
                            popupContent += '<br>' + dwellInfoHtml(p);
                        }
                    } else {
                        var pctDisplay = (p.pct !== null && p.pct_gps !== null && p.pct !== p.pct_gps)
                            ? p.pct + '%-' + p.pct_gps + '%'
                            : (p.pct !== null ? p.pct + '%' : '—');
                        popupContent += '<b>Dias esperados:</b> ' + p.total_days + '<br>' +
                            '<b>Dias com passagem:</b> ' + p.visited + '<br>' +
                            '<b>Dias sem passagem:</b> ' + p.missed + '<br>' +
                            (p.n_gps_gap > 0 ? '<b>Possível falha GPS:</b> ' + p.n_gps_gap + ' dia(s)<br>' : '') +
                            '<b>Porcentagem:</b> ' + pctDisplay;
                    }
                    
                    popupContent += '</div>';
                    layer.bindPopup(popupContent);
                    
                    // Abre detail sidebar quando popup de segmento abre
                    layer.on('popupopen', function() {
                        openDetailSidebar(p);
                    });
                    
                    // Fecha detail sidebar quando popup fecha
                    layer.on('popupclose', function() {
                        closeDetailSidebar();
                    });
                    
                    var tooltipText;
                    if (window.SINGLE_DAY_MODE) {
                        var sInfo = getStatusInfo(p.status);
                        tooltipText = p.route_code + ' ' + sInfo.icon;
                    } else {
                        var tPct = (p.pct !== null && p.pct_gps !== null && p.pct !== p.pct_gps)
                            ? p.pct + '%-' + p.pct_gps + '%'
                            : (p.pct !== null ? p.pct + '%' : '—');
                        tooltipText = p.route_code + ' — ' + tPct;
                    }
                    
                    // Rua de divisa: o cartão diz todos os setores dela.
                    if (p.shared && p.shared.length) tooltipText += ' · também: ' + p.shared.join(', ');
                    layer.bindTooltip(
                        '<div class="tooltip-inner" style="background:' + color + '; color:' + textColor + '; border: 1px solid ' + borderColor + ';">' + tooltipText + '</div>',
                        { sticky: true, direction: 'top', className: 'colored-tooltip' }
                    );
                    
                    allSegmentFeatures.push({ layer: layer, props: p });
                    bindFocusEvents(layer, p.sector_key, p.shared);
                }
            }).addTo(map);
            
            if (segmentsLayer.getBounds().isValid()) {
                initialBounds = segmentsLayer.getBounds();
                // O fit real é disparado no bloco de inicialização,
                // após positionLeftPanel reposicionar a legenda.
            }
        }

        // ==================== VIEWPORT INICIAL / RESET ====================
        var initialBounds = null;

        // Calcula paddings dinâmicos para que os segmentos fiquem entre
        // título (topo), base da página (logo é transparente, ignorado),
        // legenda (esq.) e sidebar (dir.).
        function computeFitOpts(animate) {
            var titleEl = document.getElementById('map-title');
            var legendEl = document.getElementById('legend');
            var sidebarEl = document.getElementById('heatmap-sidebar');
            var resetEl = document.querySelector('.reset-view-container');
            var sliderEl = document.querySelector('.zoom-slider-container');

            var gap = 12;
            var W = window.innerWidth;
            var H = window.innerHeight;

            var paddingTop = gap;
            if (titleEl) {
                var r = titleEl.getBoundingClientRect();
                if (r.height > 0) {
                    paddingTop = Math.max(paddingTop, r.bottom + gap);
                }
            }

            // Bottom: vai até a base da página (o logo é transparente
            // e tem pointer-events: none, segmentos passam por trás).
            var paddingBottom = gap;

            // Esquerda: além da legenda; fallback para o controle mais à
            // esquerda (reset/slider) caso a legenda ainda não tenha sido
            // posicionada (p.ex. fit chamado antes de positionLeftPanel).
            var paddingLeft = gap;
            var leftCtl = resetEl || sliderEl;
            if (leftCtl) {
                var cr = leftCtl.getBoundingClientRect();
                if (cr.width > 0) {
                    paddingLeft = Math.max(paddingLeft, cr.right + gap);
                }
            }
            if (legendEl) {
                var lgr = legendEl.getBoundingClientRect();
                if (lgr.width > 0) {
                    paddingLeft = Math.max(paddingLeft, lgr.right + gap);
                }
            }

            var paddingRight = gap;
            if (sidebarEl && !sidebarEl.classList.contains('closed')) {
                var sr = sidebarEl.getBoundingClientRect();
                if (sr.width > 0) {
                    paddingRight = Math.max(paddingRight, W - sr.left + gap);
                }
            }

            console.log('[FIT] paddings TL=[' + paddingLeft + ',' + paddingTop + '] BR=[' + paddingRight + ',' + paddingBottom + ']');

            return {
                paddingTopLeft: [paddingLeft, paddingTop],
                paddingBottomRight: [paddingRight, paddingBottom],
                animate: !!animate
            };
        }

        function fitToInitial(animate) {
            if (!initialBounds || !initialBounds.isValid()) return;
            try {
                map.fitBounds(initialBounds, computeFitOpts(animate));
            } catch (e) {
                console.warn('fitToInitial failed:', e);
            }
        }
        
        // ==================== PONTOS VICIADOS ====================
        function formatSecPV(sec) {
            if (sec === null || sec === undefined || isNaN(sec)) return '—';
            var s = Math.round(sec);
            var m = Math.floor(s / 60);
            var r = s % 60;
            if (m > 0) return m + 'min ' + (r > 0 ? r + 's' : '');
            return r + 's';
        }

        function renderPontosViciados() {
            if (pontosLayer) map.removeLayer(pontosLayer);
            var pv = window.PONTOS_VICIADOS;
            if (!pv || !pv.features || pv.features.length === 0) return;

            pontosLayer = L.geoJSON(pv, {
                pointToLayer: function(feature, latlng) {
                    var p = feature.properties;
                    var fillColor  = p.color || '#888888';
                    var borderColor = darkenColor(fillColor, 35);
                    return L.circleMarker(latlng, {
                        renderer:    pontosTopRenderer,
                        pane:        'pontosTopPane',
                        radius:      11,
                        fillColor:   fillColor,
                        color:       borderColor,
                        weight:      2.5,
                        opacity:     1,
                        fillOpacity: 0.88,
                    });
                },
                onEachFeature: function(feature, layer) {
                    var p = feature.properties;
                    var fillColor  = p.color || '#888888';
                    var textColor  = getContrastColor(fillColor);
                    var borderClr  = darkenColor(fillColor, 35);

                    // Tooltip permanente: nome do ponto
                    layer.bindTooltip(
                        '<div class="tooltip-inner" style="background:' + borderClr + '; color:#fff;">'
                        + escapeHtmlPV(p.name) + '</div>',
                        { permanent: false, direction: 'top', className: 'colored-tooltip' }
                    );

                    // Popup completo
                    var stopLine = p.mean_stop_sec !== null && p.mean_stop_sec !== undefined
                        ? '<b>Tempo médio de parada:</b> ' + formatSecPV(p.mean_stop_sec) + ' ('
                          + p.total_stops + ' registro' + (p.total_stops !== 1 ? 's' : '') + ')<br>'
                        : '<b>Sem paradas registradas no período</b><br>';

                    var vehiclesLine = '';
                    if (p.vehicles && p.vehicles.length > 0) {
                        vehiclesLine = '<b>Veículos com coleta:</b> ' + p.vehicles.join(', ') + '<br>';
                    }

                    var dvRows = '';
                    if (p.date_vehicle_list && p.date_vehicle_list.length > 0) {
                        dvRows = '<table style="width:100%;border-collapse:collapse;font-size:11px;margin-top:6px;">'
                            + '<thead><tr style="background:#f5f5f5;">'
                            + '<th style="padding:3px 5px;text-align:left;border-bottom:1px solid #ddd;">Data</th>'
                            + '<th style="padding:3px 5px;text-align:left;border-bottom:1px solid #ddd;">Veículo</th>'
                            + '<th style="padding:3px 5px;text-align:left;border-bottom:1px solid #ddd;">Duração</th>'
                            + '</tr></thead><tbody>';
                        p.date_vehicle_list.forEach(function(d) {
                            dvRows += '<tr>'
                                + '<td style="padding:2px 5px;border-bottom:1px solid #f0f0f0;">' + escapeHtmlPV(d.date_str || '—') + '</td>'
                                + '<td style="padding:2px 5px;border-bottom:1px solid #f0f0f0;">' + escapeHtmlPV(d.vehicle || '—') + '</td>'
                                + '<td style="padding:2px 5px;border-bottom:1px solid #f0f0f0;">' + formatSecPV(d.duration_sec) + '</td>'
                                + '</tr>';
                        });
                        dvRows += '</tbody></table>';
                    }

                    var popup = '<div class="popup-header" style="background:' + fillColor
                        + '; color:' + textColor + '; border-bottom:2px solid ' + borderClr + ';">'
                        + '📍 ' + escapeHtmlPV(p.name) + '</div>'
                        + '<div class="popup-content">'
                        + '<b>Lote:</b> ' + escapeHtmlPV(p.lote) + '&nbsp;&nbsp;'
                        + '<b>Frequência:</b> ' + escapeHtmlPV(p.freq) + '&nbsp;&nbsp;'
                        + '<b>Turno:</b> ' + escapeHtmlPV(p.turno) + '<br>'
                        + stopLine
                        + vehiclesLine
                        + dvRows
                        + '</div>';

                    layer.bindPopup(popup, { maxWidth: 340 });
                }
            }).addTo(map);
        }

        function escapeHtmlPV(str) {
            var d = document.createElement('div');
            d.appendChild(document.createTextNode(str || ''));
            return d.innerHTML;
        }

        // ==================== FILTROS ====================
        var currentRatioFilter = 'all';
        var currentCodeFilter = 'all';
        var currentVehicleFilter = 'all';

        function isVisibleByFilters(p) {
            var visible = true;
            if (currentRatioFilter !== 'all') {
                if (window.SINGLE_DAY_MODE) {
                    visible = (p.status === currentRatioFilter);
                } else {
                    if (currentRatioFilter === 'once') {
                        visible = p.flag_once;
                    } else if (currentRatioFilter === 'never') {
                        visible = p.flag_never;
                    } else {
                        visible = (p.category === currentRatioFilter);
                    }
                }
            }
            if (visible && currentCodeFilter !== 'all') {
                visible = (p.route_code === currentCodeFilter);
            }
            if (visible && currentVehicleFilter !== 'all') {
                var vs = p.vehicles || [];
                visible = vs.indexOf(currentVehicleFilter) !== -1;
            }
            return visible;
        }

        // soEstilo: o foco só muda opacidade. Sem isso o hover recalculava a
        // "Média de Coleta do Dia" no JS (que não bate com a do Python mesmo
        // sem filtro — ver STATE) e o número mudava ao passar o mouse.
        function applyFilters(soEstilo) {
            allSegmentFeatures.forEach(function(item) {
                var p = item.props;
                var visible = isVisibleByFilters(p);

                // Modo "apenas prioritárias": esconde quem não é prioridade
                if (visible && currentPriorityMode === 'only' && !p.is_priority) {
                    visible = false;
                }

                if (visible) {
                    if (!segmentsLayer.hasLayer(item.layer)) {
                        segmentsLayer.addLayer(item.layer);
                    }
                    // Modo "esmaecer": baixa opacidade dos não-prioritários
                    var op = (currentPriorityMode === 'dim' && !p.is_priority)
                        ? SEGMENT_DIM_OPACITY
                        : SEGMENT_BASE_OPACITY;
                    if (isOutOfFocus(p.sector_key)) op = Math.min(op, FOCUS_FADE_OPACITY);
                    if (item.layer.setStyle) {
                        item.layer.setStyle({ opacity: op });
                    }
                } else {
                    if (segmentsLayer.hasLayer(item.layer)) {
                        segmentsLayer.removeLayer(item.layer);
                    }
                }
            });

            // Sincroniza halos: visível só se segmento prioritário estiver visível
            allPriorityHalos.forEach(function(item) {
                var p = item.props;
                var segVisible = isVisibleByFilters(p);
                if (currentPriorityMode === 'only' && !p.is_priority) {
                    segVisible = false;
                }
                if (segVisible) {
                    item.layer.setStyle({
                        opacity: isOutOfFocus(p.sector_key) ? FOCUS_FADE_OPACITY : PRIORITY_HALO_STYLE.opacity
                    });
                    if (!priorityHaloLayer.hasLayer(item.layer)) {
                        priorityHaloLayer.addLayer(item.layer);
                    }
                } else {
                    if (priorityHaloLayer.hasLayer(item.layer)) {
                        priorityHaloLayer.removeLayer(item.layer);
                    }
                }
            });

            if (!soEstilo) updateDynamicStats();
        }

        function setPriorityMode(mode) {
            if (mode !== 'highlight' && mode !== 'dim' && mode !== 'only') return;
            currentPriorityMode = mode;
            applyFilters();
        }
        
        // A barra que o filtro e o foco atualizam: no período a do dia fica oculta
        // (ver "Atualiza médias"), então vai para a do período.
        var BARRA_MEDIA = (window.HEATMAP_HISTORY && window.HEATMAP_HISTORY.mode === 'range')
            ? 'MonthTotal' : 'Day';

        function updateDynamicStats() {
            var totalVisited = 0;
            var totalExpected = 0;
            var totalVisitedGps = 0;
            
            allSegmentFeatures.forEach(function(item) {
                var p = item.props;
                // Mesma conta de stats_module.compute_overall_collection_ratio: SEM
                // as ruas de coleta manual (essas entram só em "Coleta + Manual") e
                // pesando pelo comprimento, sem piso de 1 m. Até 2026-10-28 as
                // manuais entravam aqui como não coletadas: com o filtro em
                // "Todos", Cuiabá 21/10 caía de 96,1% para 94,8% (18,2 km manuais).
                if (!map.hasLayer(item.layer) || p.is_manual || !(p.total_days > 0)) return;
                var w = p.len_m > 0 ? p.len_m : 0;
                var total = p.total_days || 0;
                totalVisited += w * Math.max(0, total - (p.missed || 0));
                totalExpected += w * total;
                var missGps = (typeof p.missed_gps === 'number') ? p.missed_gps : (p.missed || 0);
                totalVisitedGps += w * Math.max(0, total - missGps);
            });
            
            // Com um setor em foco a barra é dele (o rótulo diz qual); o filtro volta a
            // valer quando o foco solta (barrasDoFoco recalcula).
            if (focusApplied) return;
            if (totalExpected > 0) {
                var pct = (totalVisited / totalExpected) * 100;
                var pctGps = (totalVisitedGps / totalExpected) * 100;
                updateAvgBar('bar' + BARRA_MEDIA, 'txt' + BARRA_MEDIA, pct, pctGps);
            }
        }
        
        // Popula selects (usa IDs do html_templates)
        function populateFilters() {
            var ratioSelect = document.getElementById('ratioFilterSelect');
            var codeSelect = document.getElementById('codeFilterSelect');
            var vehicleSelect = document.getElementById('vehicleFilterSelect');

            if (ratioSelect) {
                (window.FILTERS.byRatio || []).forEach(function(f) {
                    var opt = document.createElement('option');
                    opt.value = f.value;
                    opt.textContent = f.label;
                    ratioSelect.appendChild(opt);
                });
                ratioSelect.addEventListener('change', function() {
                    currentRatioFilter = this.value;
                    applyFilters();
                });
            }

            if (codeSelect) {
                (window.FILTERS.byCode || []).forEach(function(f) {
                    var opt = document.createElement('option');
                    opt.value = f.value;
                    opt.textContent = f.label;
                    codeSelect.appendChild(opt);
                });
                codeSelect.addEventListener('change', function() {
                    currentCodeFilter = this.value;
                    applyFilters();
                });
            }

            if (vehicleSelect) {
                var byVehicle = window.FILTERS.byVehicle || [];
                byVehicle.forEach(function(f) {
                    var opt = document.createElement('option');
                    opt.value = f.value;
                    opt.textContent = f.label;
                    vehicleSelect.appendChild(opt);
                });
                // Desabilita se só há a opção placeholder "all"
                var hasRealVehicles = byVehicle.some(function(f) { return f.value !== 'all'; });
                if (!hasRealVehicles) {
                    vehicleSelect.disabled = true;
                    var hint = document.getElementById('vehicleFilterHint');
                    if (hint) hint.textContent = 'Nenhum veículo registrado neste período.';
                }
                vehicleSelect.addEventListener('change', function() {
                    currentVehicleFilter = this.value;
                    applyFilters();
                });
            }
        }

        // ==================== CONTROLES DE VISUALIZAÇÃO ====================
        function refreshVisualizationsSectionVisibility() {
            var section = document.getElementById('visualizations-section');
            if (!section) return;
            var prio = document.getElementById('priority-control');
            var pv = document.getElementById('pontos-viciados-control');
            var sl = document.getElementById('sector-labels-control');
            var anyVisible =
                (prio && prio.style.display !== 'none') ||
                (pv && pv.style.display !== 'none') ||
                (sl && sl.style.display !== 'none');
            section.style.display = anyVisible ? '' : 'none';
        }

        function initPriorityControl() {
            var hasPriority = allPriorityHalos.length > 0;
            var container = document.getElementById('priority-control');
            if (!container) return;
            if (!hasPriority) {
                container.style.display = 'none';
                refreshVisualizationsSectionVisibility();
                return;
            }
            container.style.display = '';
            refreshVisualizationsSectionVisibility();

            var buttons = container.querySelectorAll('.priority-btn');
            buttons.forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var mode = btn.getAttribute('data-mode');
                    buttons.forEach(function(b) { b.classList.remove('active'); });
                    btn.classList.add('active');
                    setPriorityMode(mode);
                });
            });
        }

        function initPontosViciadosControl() {
            var pv = window.PONTOS_VICIADOS;
            var hasPontos = !!(pv && pv.features && pv.features.length > 0);
            var container = document.getElementById('pontos-viciados-control');
            if (!container) return;
            if (!hasPontos) {
                container.style.display = 'none';
                refreshVisualizationsSectionVisibility();
                return;
            }
            container.style.display = '';
            refreshVisualizationsSectionVisibility();

            var buttons = container.querySelectorAll('.priority-btn');
            buttons.forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var mode = btn.getAttribute('data-pv-mode');
                    buttons.forEach(function(b) { b.classList.remove('active'); });
                    btn.classList.add('active');
                    setPontosViciadosVisible(mode === 'show');
                });
            });
        }

        function setPontosViciadosVisible(visible) {
            // A parte da legenda que só explica os pontos sai junto com eles.
            var pvLegend = document.getElementById('legend-pv');
            if (pvLegend) pvLegend.style.display = visible ? '' : 'none';
            if (!pontosLayer) return;
            if (visible) {
                if (!map.hasLayer(pontosLayer)) pontosLayer.addTo(map);
            } else {
                if (map.hasLayer(pontosLayer)) map.removeLayer(pontosLayer);
            }
        }

        // ==================== FOCO NUM SETOR ====================
        // Passar o mouse (ou clicar) numa rua, na área de um setor ou no código
        // dele esmaece todo o resto — ruas, setores e códigos — e destaca só
        // aquele setor (Jorge, 2026-10-28). Mouse = prévia; clique = fixa;
        // clique no mapa vazio solta. A chave é o código sem sufixo de agenda.
        // A barra lateral passa a mostrar o % DO SETOR (calculado no Python, pela
        // mesma régua da média geral) e volta ao soltar.
        var FOCUS_FADE_OPACITY = 0.12;
        var sectorLabels = null;
        var hoverKey = null, pinnedKey = null, focusApplied = null, hoverTimer = null;
        var sectorInfo = {};
        ((window.SECTORS && window.SECTORS.features) || []).forEach(function(f) {
            var p = f.properties || {};
            if (p.code && !sectorInfo[p.code]) sectorInfo[p.code] = p;
        });

        function currentFocusKey() { return pinnedKey || hoverKey; }
        function isOutOfFocus(key) {
            var k = currentFocusKey();
            return !!k && key !== k;
        }

        // Barra lateral: retrato do estado sem foco, para voltar exatamente a ele
        // (mexendo nos mesmos elementos, a transição de 0,4 s da barra anima).
        var barrasSemFoco = null;
        function retratoBarras() {
            return [BARRA_MEDIA, 'Manual'].map(function(n) {
                var lbl = document.getElementById('lbl' + n), bar = document.getElementById('bar' + n),
                    txt = document.getElementById('txt' + n);
                return lbl && bar && txt ? { n: n, lbl: lbl.innerHTML, w: bar.style.width,
                    c: bar.style.backgroundColor, t: txt.textContent } : null;
            });
        }
        function barrasDoFoco(k) {
            var st = k && sectorInfo[k] && sectorInfo[k].stats;
            if (k && barrasSemFoco === null) barrasSemFoco = retratoBarras();
            if (st && typeof st[0] === 'number') {
                var nome = escapeHtmlPV(k);
                document.getElementById('lbl' + BARRA_MEDIA).innerHTML = 'Média de Coleta do Setor <b>' + nome + '</b>';
                updateAvgBar('bar' + BARRA_MEDIA, 'txt' + BARRA_MEDIA, st[0] * 100, (typeof st[1] === 'number' ? st[1] : st[0]) * 100);
                if (typeof st[2] === 'number' && document.getElementById('lblManual')) {
                    document.getElementById('lblManual').innerHTML = 'Média de <u>Coleta + Manual</u> do Setor <b>' + nome + '</b>';
                    updateAvgBar('barManual', 'txtManual', st[2] * 100,
                                 (typeof st[3] === 'number' ? st[3] : st[2]) * 100, colorFromPercentManual);
                }
            } else if (barrasSemFoco) {
                barrasSemFoco.forEach(function(b) {
                    if (!b) return;
                    document.getElementById('lbl' + b.n).innerHTML = b.lbl;
                    var bar = document.getElementById('bar' + b.n);
                    bar.style.width = b.w; bar.style.backgroundColor = b.c;
                    document.getElementById('txt' + b.n).textContent = b.t;
                });
                // O retrato é de UMA soltura: o próximo foco tira outro. E a média
                // é recalculada pelo filtro de AGORA — trocado durante o foco, o
                // retrato traria o número de antes (achado do adversário, 2026-11-04).
                barrasSemFoco = null;
                if (!k) updateDynamicStats();
            }
        }

        function applyFocus() {
            var k = currentFocusKey();
            if (k === focusApplied) return;
            focusApplied = k;
            applyFilters(true);
            if (k) {
                // Rua de divisa: cada setor tem a sua cópia da linha, uma sobre a
                // outra. A do setor em foco vai para cima, senão a cópia esmaecida
                // do vizinho a cobre.
                allPriorityHalos.forEach(function(it) { if (it.props.sector_key === k && it.layer.bringToFront) it.layer.bringToFront(); });
                allSegmentFeatures.forEach(function(it) { if (it.props.sector_key === k && it.layer.bringToFront) it.layer.bringToFront(); });
            }
            if (sectorsLayer) {
                sectorsLayer.eachLayer(function(l) {
                    var key = l.feature && l.feature.properties.code;
                    var fora = isOutOfFocus(key), dentro = !!k && !fora;
                    l.setStyle({
                        opacity: fora ? 0.15 : 0.8,
                        fillOpacity: fora ? 0.03 : (dentro ? 0.3 : 0.15),
                        weight: dentro ? 3 : 2
                    });
                });
            }
            if (sectorLabels) sectorLabels.setFocus(k);
            barrasDoFoco(k);
        }
        function setHoverKey(k) {
            clearTimeout(hoverTimer);
            if (k) { hoverKey = k; applyFocus(); return; }
            // Folga: passar de uma rua para a vizinha do mesmo setor não pisca.
            hoverTimer = setTimeout(function() { hoverKey = null; applyFocus(); }, 150);
        }
        function pinKey(k) { pinnedKey = k; applyFocus(); }

        function bindFocusEvents(layer, key, shared) {
            if (!key) return;
            layer.on('mouseover', function() { setHoverKey(key); });
            layer.on('mouseout', function() { setHoverKey(null); });
            layer.on('click', function(e) {
                L.DomEvent.stopPropagation(e);
                // Rua de divisa: cliques seguidos alternam entre os setores dela.
                var chaves = [key].concat(shared || []);
                var i = chaves.indexOf(pinnedKey);
                pinKey(i >= 0 ? chaves[(i + 1) % chaves.length] : key);
            });
        }

        // Área do setor: o canvas das ruas (pane 420) cobre o mapa inteiro e
        // engole o mouse antes dos polígonos (pane 400) — evento ligado no
        // polígono nunca disparava (achado do auditor-claims, 2026-10-28).
        // Então a área é achada pelo próprio evento do mapa, que o canvas repassa
        // quando o ponteiro não está sobre uma rua.
        function sectorAt(latlng) {
            var x = latlng.lng, y = latlng.lat, achou = null;
            function dentroAnel(anel) {
                var d = false;
                for (var i = 0, j = anel.length - 1; i < anel.length; j = i++) {
                    var xi = anel[i][0], yi = anel[i][1], xj = anel[j][0], yj = anel[j][1];
                    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) d = !d;
                }
                return d;
            }
            function dentroPoligono(rings) {
                if (!dentroAnel(rings[0])) return false;
                for (var h = 1; h < rings.length; h++) if (dentroAnel(rings[h])) return false;
                return true;
            }
            if (!sectorsLayer) return null;
            sectorsLayer.eachLayer(function(l) {
                if (achou || !l.feature) return;
                if (!l.getBounds().contains(latlng)) return;
                var g = l.feature.geometry;
                var polys = g.type === 'Polygon' ? [g.coordinates] : (g.type === 'MultiPolygon' ? g.coordinates : []);
                for (var k = 0; k < polys.length; k++) {
                    if (dentroPoligono(polys[k])) { achou = l.feature.properties.code; return; }
                }
            });
            return achou;
        }
        // Cartão da área (o mesmo estilo do cartão da rua): nome do setor e % dele.
        var areaTip = L.tooltip({ direction: 'top', className: 'colored-tooltip', offset: [0, -10] });
        function cartaoArea(k) {
            var p = sectorInfo[k] || {}, cor = p.color || '#555555';
            var st = p.stats, pct = '';
            if (st && typeof st[0] === 'number') {
                pct = ' — ' + (st[0] * 100).toFixed(1).replace('.', ',') + '%';
            }
            return '<div class="tooltip-inner" style="background:' + cor + '; color:' + getContrastColor(cor) + ';">'
                + escapeHtmlPV(p.name || k) + pct + '</div>';
        }
        var areaTimer = null, areaKey = null;
        map.on('mousemove', function(e) {
            if (areaTimer) return;
            areaTimer = setTimeout(function() { areaTimer = null; }, 60);
            var k = sectorAt(e.latlng);
            if (k) {
                areaTip.setLatLng(e.latlng).setContent(cartaoArea(k));
                if (!map.hasLayer(areaTip)) areaTip.openOn(map);
            } else if (map.hasLayer(areaTip)) {
                map.closeTooltip(areaTip);
            }
            if (k === areaKey) return;
            areaKey = k;
            setHoverKey(k);
        });
        map.on('mouseout', function() {
            areaKey = null;
            if (map.hasLayer(areaTip)) map.closeTooltip(areaTip);
            setHoverKey(null);
        });
        map.on('click', function(e) {
            // Clique fora das ruas: na área de um setor, fixa ele; no vazio, solta.
            var k = sectorAt(e.latlng);
            if (k) pinKey(k);
            else if (pinnedKey) { pinnedKey = null; applyFocus(); }
        });

        // ==================== CÓDIGO DO SETOR SOBRE O MAPA ====================
        """, f"{SECTOR_LABELS_JS}", """

        function initSectorLabelsControl() {
            var labels = createSectorLabels(map, (window.SECTORS && window.SECTORS.features) || [],
                                            { onHover: setHoverKey, onClick: pinKey });
            sectorLabels = labels;
            var container = document.getElementById('sector-labels-control');
            if (!container) return;
            if (!labels.count) {
                container.style.display = 'none';
                refreshVisualizationsSectionVisibility();
                return;
            }
            container.style.display = '';
            refreshVisualizationsSectionVisibility();
            var buttons = container.querySelectorAll('.priority-btn');
            buttons.forEach(function(btn) {
                btn.addEventListener('click', function() {
                    buttons.forEach(function(b) { b.classList.remove('active'); });
                    btn.classList.add('active');
                    labels.setVisible(btn.getAttribute('data-sl-mode') === 'show');
                });
            });
            // Nasce ligado (Jorge, 2026-10-28).
            labels.setVisible(true);
        }

        // ==================== POPOVER DE CONFIGURAÇÕES AVANÇADAS ====================
        function initAdvancedConfigPopover() {
            var btn = document.getElementById('advanced-config-btn');
            var popover = document.getElementById('advanced-config-popover');
            var closeBtn = document.getElementById('advanced-config-close');
            if (!btn || !popover) return;

            btn.addEventListener('click', function() {
                popover.classList.add('open');
                popover.setAttribute('aria-hidden', 'false');
            });
            if (closeBtn) {
                closeBtn.addEventListener('click', function() {
                    popover.classList.remove('open');
                    popover.setAttribute('aria-hidden', 'true');
                });
            }
            // ESC fecha o popover
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape' && popover.classList.contains('open')) {
                    popover.classList.remove('open');
                    popover.setAttribute('aria-hidden', 'true');
                }
            });
        }

        // ==================== SIDEBAR (usa lógica do html_templates) ====================
        var handle = document.getElementById('heatmap-sidebar-handle');
        var sidebar = document.getElementById('heatmap-sidebar');
        var sidebarOpen = true;
        
        function updateSidebarState() {
            if (sidebarOpen) {
                document.body.classList.remove('sidebar-closed');
                document.body.classList.add('sidebar-open');
                sidebar.classList.remove('closed');
                handle.textContent = '>';
            } else {
                document.body.classList.remove('sidebar-open');
                document.body.classList.add('sidebar-closed');
                sidebar.classList.add('closed');
                handle.textContent = '<';
            }
            
            // Redimensiona o mapa após a animação
            setTimeout(function() {
                map.invalidateSize();
            }, 300);
        }
        
        if (handle) {
            document.body.classList.add('sidebar-open');
            handle.addEventListener('click', function() {
                sidebarOpen = !sidebarOpen;
                updateSidebarState();
            });
        }
        
        // ==================== BARRAS DE MÉDIA ====================
        function colorFromPercent(pct) {
            pct = Math.max(0, Math.min(100, pct));
            var hue = (pct / 100) * 120;
            return 'hsl(' + hue + ', 85%, 45%)';
        }
        
        function colorFromPercentManual(pct) {
            // Azul escuro (0%) → Ciano (100%)
            pct = Math.max(0, Math.min(100, pct));
            var r = Math.round(0 + (0 - 0) * pct / 100);
            var g = Math.round(0x33 + (0xFF - 0x33) * pct / 100);
            var b = Math.round(0x88 + (0xFF - 0x88) * pct / 100);
            return 'rgb(' + r + ',' + g + ',' + b + ')';
        }
        
        function updateAvgBar(barId, textId, valuePct, valuePctGps, colorFn) {
            var bar = document.getElementById(barId);
            var txt = document.getElementById(textId);
            
            if (!bar || !txt || typeof valuePct !== 'number') {
                if (txt) txt.textContent = '—';
                if (bar) bar.style.width = '0%';
                return;
            }
            
            var fn = colorFn || colorFromPercent;
            var pct = Math.max(0, Math.min(100, valuePct));
            bar.style.width = pct.toFixed(1) + '%';
            bar.style.backgroundColor = fn(pct);
            
            if (typeof valuePctGps === 'number' && Math.abs(valuePctGps - valuePct) > 0.05) {
                var pctGps = Math.max(0, Math.min(100, valuePctGps));
                txt.textContent = pct.toFixed(1).replace('.', ',') + '%~' + pctGps.toFixed(1).replace('.', ',') + '%';
            } else {
                txt.textContent = pct.toFixed(1).replace('.', ',') + '%';
            }
        }
        
        // Atualiza médias
        (function() {
            var isRange = hist.mode === 'range';
            var isSingle = hist.mode === 'single';
            
            if (isRange) {
                // A média do período é a conta do PRÓPRIO período, por metro (a mesma do
                // XML e do filtro "Todos"). Até 2026-11-04 era a média simples dos % de
                // HISTORICO/<cidade>_stats.parquet, que só o modo dia grava: cada dia
                // trazia o valor da última geração diária (.exe velho, rota velha, dia
                // aberto) e dia nunca gerado faltava. Cabo 10/2026: 73,0% x 78,5% no XML.
                var ovR = window.HEATMAP_OVERALL, ovGpsR = window.HEATMAP_OVERALL_GPS;
                var avg = (typeof ovR === 'number') ? ovR * 100 : null;
                var avgGpsR = (typeof ovGpsR === 'number') ? ovGpsR * 100 : avg;
                
                document.getElementById('history_tittle').textContent = 'Histórico de Coleta (Período)';
                document.getElementById('lblMonthTotal').innerHTML = 'Média de Coleta do Período';
                updateAvgBar('barMonthTotal', 'txtMonthTotal', avg, avgGpsR);
                
                var dayBox = document.getElementById('avgBoxDay');
                if (dayBox) dayBox.style.display = 'none';
                var untilBox = document.getElementById('avgBoxMonthUntil');
                if (untilBox) untilBox.style.display = 'none';
                var manualBox = document.getElementById('avgBoxManual');
                if (manualBox) manualBox.style.display = 'none';
            }
            
            if (isSingle) {
                var currentDateStr = hist.current_day;
                if (!currentDateStr || !hist.dates.includes(currentDateStr)) {
                    currentDateStr = hist.dates.filter(function(d) { return typeof d === 'string'; }).sort().slice(-1)[0] || null;
                }
                var currentDate = currentDateStr ? new Date(currentDateStr + 'T00:00:00') : null;
                var dayLabel = currentDate ? currentDate.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' }) : '—';
                
                // --- Barra do DIA ---
                var dayPct = (typeof overall === 'number') ? overall * 100 : null;
                var dayPctGps = (typeof overallGps === 'number') ? overallGps * 100 : dayPct;
                
                if (dayPct !== null) {
                    document.getElementById('lblDay').innerHTML = 'Média de Coleta do <b>Dia</b>';
                    updateAvgBar('barDay', 'txtDay', dayPct, dayPctGps);
                } else {
                    updateAvgBar('barDay', 'txtDay', null);
                }
                
                // --- Barra MENSAL (elimina redundância) ---
                // Se o último dia do histórico == dia do mapa → "Mês" apenas
                // Se for anterior → "Mês (Até Dia dd/mm)" apenas
                var lastHistDate = hist.dates.length > 0 ? hist.dates[hist.dates.length - 1] : null;
                var isLastDay = (lastHistDate === currentDateStr);
                
                // Calcula Mês Total
                var sum = 0, cnt = 0, sumGps = 0;
                var pGps = hist.pcts_gps || hist.pcts;
                hist.pcts.forEach(function(v, i) {
                    if (typeof v === 'number') {
                        sum += v;
                        cnt += 1;
                        sumGps += (typeof pGps[i] === 'number') ? pGps[i] : v;
                    }
                });
                
                // Calcula Mês Até Dia
                var sumU = 0, cntU = 0, sumUGps = 0;
                var pGpsU = hist.pcts_gps || hist.pcts;
                hist.dates.forEach(function(d, i) {
                    if (d <= currentDateStr && typeof hist.pcts[i] === 'number') {
                        sumU += hist.pcts[i];
                        cntU += 1;
                        sumUGps += (typeof pGpsU[i] === 'number') ? pGpsU[i] : hist.pcts[i];
                    }
                });
                
                if (isLastDay) {
                    // Mostra apenas "Mês" (Total)
                    if (cnt > 0) {
                        document.getElementById('lblMonthTotal').innerHTML = 'Média de Coleta do <b>Mês</b>';
                        updateAvgBar('barMonthTotal', 'txtMonthTotal', sum / cnt, sumGps / cnt);
                    } else {
                        updateAvgBar('barMonthTotal', 'txtMonthTotal', null);
                    }
                    var untilBox = document.getElementById('avgBoxMonthUntil');
                    if (untilBox) untilBox.style.display = 'none';
                } else {
                    // Mostra apenas "Mês (Até Dia dd/mm)"
                    var monthTotalBox = document.getElementById('avgBoxMonthTotal');
                    if (monthTotalBox) monthTotalBox.style.display = 'none';
                    
                    if (cntU > 0) {
                        document.getElementById('lblMonthUntil').innerHTML = 'Média de Coleta do <b>Mês</b> (Até Dia ' + dayLabel + ')';
                        updateAvgBar('barMonthUntil', 'txtMonthUntil', sumU / cntU, sumUGps / cntU);
                    } else {
                        updateAvgBar('barMonthUntil', 'txtMonthUntil', null);
                    }
                }
                
                // --- Barra MANUAL (Coleta + Manual) ---
                var manualPct = (typeof overallManual === 'number') ? overallManual * 100 : null;
                var manualPctGps = (typeof overallManualGps === 'number') ? overallManualGps * 100 : manualPct;
                
                if (manualPct !== null) {
                    updateAvgBar('barManual', 'txtManual', manualPct, manualPctGps, colorFromPercentManual);
                } else {
                    var manualBox = document.getElementById('avgBoxManual');
                    if (manualBox) manualBox.style.display = 'none';
                }
            }
        })();
        
        // ==================== GRÁFICO DE HISTÓRICO (usa Plotly igual ao original) ====================
        if (hist.dates && hist.dates.length && window.Plotly) {
            var x = hist.dates;
            var y = hist.pcts;
            var hover_texts = x.map(function(d, i) {
                var day = (d && d.length >= 10) ? d.slice(8, 10) : (d || '');
                var pct = (typeof y[i] === 'number') ? (y[i].toFixed(2).replace('.', ',') + '%') : (String(y[i]) || '—');
                return 'Dia ' + day + ' - ' + pct;
            });
            
            var xRange = undefined;
            if (hist.mode === 'range' && hist.period_from && hist.period_to) {
                xRange = [hist.period_from, hist.period_to];
            } else if (hist.mode === 'single' && hist.month_start && hist.month_end) {
                xRange = [hist.month_start, hist.month_end];
            }
            
            var trace = {
                x: x, y: y,
                mode: 'lines+markers',
                fill: 'tozeroy',
                fillcolor: 'rgba(0, 150, 255, 0.25)',
                line: { color: 'rgb(0, 120, 220)', width: 2 },
                marker: { size: 6 },
                text: hover_texts,
                hoverinfo: 'text',
                name: 'Média'
            };
            
            var layout = {
                height: 200,
                margin: { l: 32, r: 32, t: 26, b: 36 },
                autosize: true,
                xaxis: {
                    type: 'date',
                    tickformat: '%d/%m',
                    tickangle: 35,
                    automargin: false,
                    fixedrange: true,
                    range: xRange
                },
                yaxis: {
                    range: [0, 100],
                    fixedrange: true,
                    automargin: false
                },
                hoverlabel: {
                    bgcolor: 'rgba(0, 0, 0, 0.75)',
                    bordercolor: '#ffffff',
                    font: { size: 12, color: '#ffffff' },
                    align: 'left'
                },
                plot_bgcolor: 'rgba(0,0,0,0)',
                paper_bgcolor: 'rgba(0,0,0,0)'
            };
            
            Plotly.newPlot('historyChart', [trace], layout, { displayModeBar: false });
        }
        
        // ==================== DETAIL SIDEBAR ====================
        var detailSidebar = document.getElementById('detail-sidebar');
        var detailBody = document.getElementById('detail-body');
        var detailCloseBtn = document.getElementById('detail-close-btn');
        var isSingleDay = window.SINGLE_DAY_MODE;
        
        function openDetailSidebar(props) {
            if (!detailSidebar || !detailBody) return;
            
            var details = (window.PASSAGE_DATA && window.PASSAGE_DATA[String(props.fid)]) || [];
            var html = '';
            
            // Info do segmento
            var borderColor = props.color || '#007bff';
            html += '<div class="detail-segment-info" style="border-left-color:' + borderColor + ';">';
            html += '<div class="route-code">' + (props.route_code || '') + '</div>';
            html += '<div class="meta">';
            html += '<b>Setor:</b> ' + (props.sector || '') + '<br>';
            html += '<b>Agenda:</b> ' + (props.schedule || '') + '<br>';
            if (isSingleDay) {
                var si = getStatusInfo(props.status);
                html += '<b>Status:</b> ' + si.icon + ' ' + si.text;
                if (props.status === 'manual_collected' || props.status === 'manual_not_collected' || props.status === 'manual_gps_gap' || props.status === 'possible_manual') {
                    html += '<br>' + dwellInfoHtml(props);
                }
            } else {
                var detPct = (props.pct !== null && props.pct_gps !== null && props.pct !== props.pct_gps)
                    ? props.pct + '%-' + props.pct_gps + '%'
                    : (props.pct !== null ? props.pct + '%' : '—');
                html += '<b>Coleta:</b> ' + detPct + 
                    ' (' + (props.visited || 0) + '/' + (props.total_days || 0) + ' dias)';
            }
            html += '</div></div>';
            
            // Lista de veículos
            if (!details || details.length === 0) {
                html += '<div class="detail-no-data">Nenhuma passagem registrada neste segmento.</div>';
            } else {
                for (var i = 0; i < details.length; i++) {
                    html += buildVehicleCard(details[i], i);
                }
            }
            
            detailBody.innerHTML = html;
            detailSidebar.classList.add('open');
            
            // Inicializa toggle dos cards de veículo
            var headers = detailBody.querySelectorAll('.detail-vehicle-header');
            headers.forEach(function(h) {
                h.addEventListener('click', function() {
                    var body = this.nextElementSibling;
                    var isExpanded = body.classList.contains('expanded');
                    body.classList.toggle('expanded');
                    this.classList.toggle('active');
                });
            });
            
            // Expande automaticamente o primeiro card
            if (headers.length > 0) {
                headers[0].click();
            }
        }
        
        function closeDetailSidebar() {
            if (detailSidebar) {
                detailSidebar.classList.remove('open');
            }
        }
        
        if (detailCloseBtn) {
            detailCloseBtn.addEventListener('click', function() {
                closeDetailSidebar();
                // Fecha popup associado também
                map.closePopup();
            });
        }
        
        function buildVehicleCard(vehicle, index) {
            var v = vehicle;
            var html = '<div class="detail-vehicle-card">';
            
            // Header (toggle)
            html += '<div class="detail-vehicle-header">';
            html += '<span class="detail-vehicle-name"><span class="vehicle-icon">&#x1F69A;</span> ' + escapeHtml(v.v || 'Desconhecido') + '</span>';
            html += '<span style="display:flex;align-items:center;gap:6px;">';
            html += '<span class="detail-vehicle-badge">' + (v.n || 0) + ' passagen' + (v.n === 1 ? '' : 's') + '</span>';
            html += '<span class="detail-vehicle-expand-icon">▼</span>';
            html += '</span>';
            html += '</div>';
            
            // Body (expansível)
            html += '<div class="detail-vehicle-body">';
            
            // Resumo
            if (v.s !== null && v.s !== undefined) {
                html += '<div class="detail-summary-row"><span class="label">Vel. média</span><span>' + v.s.toFixed(1).replace('.', ',') + ' km/h</span></div>';
            }
            
            // Primeira passagem
            if (v.f && (v.f[0] || v.f[1])) {
                var firstText = '';
                if (v.f[0] && !isSingleDay) firstText += v.f[0];
                if (v.f[1]) {
                    if (firstText) firstText += ' ';
                    firstText += v.f[1];
                }
                if (v.f[2] !== null && v.f[2] !== undefined) {
                    firstText += ' (' + v.f[2].toFixed(1).replace('.', ',') + ' km/h)';
                }
                html += '<div class="detail-summary-row"><span class="label">Primeira passagem</span><span>' + firstText + '</span></div>';
            }
            
            // Última passagem
            if (v.l && (v.l[0] || v.l[1]) && v.n > 1) {
                var lastText = '';
                if (v.l[0] && !isSingleDay) lastText += v.l[0];
                if (v.l[1]) {
                    if (lastText) lastText += ' ';
                    lastText += v.l[1];
                }
                if (v.l[2] !== null && v.l[2] !== undefined) {
                    lastText += ' (' + v.l[2].toFixed(1).replace('.', ',') + ' km/h)';
                }
                html += '<div class="detail-summary-row"><span class="label">Última passagem</span><span>' + lastText + '</span></div>';
            }
            
            // Tabela de passagens
            if (v.passages && v.passages.length > 0) {
                var hasTime = v.passages.some(function(p) { return p.time && p.time !== ''; });
                var hasSpeed = v.passages.some(function(p) { return p.speed !== null && p.speed !== undefined; });
                
                html += '<table class="detail-passage-table"><thead><tr>';
                if (!isSingleDay) html += '<th>Data</th>';
                if (hasTime) html += '<th>Hora</th>';
                if (hasSpeed) html += '<th>Vel.</th>';
                html += '</tr></thead><tbody>';
                
                for (var j = 0; j < v.passages.length; j++) {
                    var p = v.passages[j];
                    html += '<tr>';
                    if (!isSingleDay) html += '<td>' + (p.date || '—') + '</td>';
                    if (hasTime) html += '<td>' + (p.time || '—') + '</td>';
                    if (hasSpeed) html += '<td>' + (p.speed !== null && p.speed !== undefined ? p.speed.toFixed(1).replace('.', ',') : '—') + '</td>';
                    html += '</tr>';
                }
                
                html += '</tbody></table>';
            }
            
            html += '</div>'; // body
            html += '</div>'; // card
            
            return html;
        }
        
        function escapeHtml(str) {
            var div = document.createElement('div');
            div.appendChild(document.createTextNode(str));
            return div.innerHTML;
        }
        
        // ==================== BOTÃO RESETAR VISUALIZAÇÃO ====================
        var ResetViewControl = L.Control.extend({
            options: { position: 'topleft' },
            onAdd: function(map) {
                var container = L.DomUtil.create('div', 'reset-view-container');
                L.DomEvent.disableClickPropagation(container);
                L.DomEvent.disableScrollPropagation(container);

                var btn = L.DomUtil.create('button', 'reset-view-btn', container);
                btn.title = 'Resetar visualização';
                btn.setAttribute('aria-label', 'Resetar visualização');
                btn.innerHTML = ''
                    + '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                    +   '<polyline points="3 7 3 3 7 3"></polyline>'
                    +   '<polyline points="21 7 21 3 17 3"></polyline>'
                    +   '<polyline points="3 17 3 21 7 21"></polyline>'
                    +   '<polyline points="21 17 21 21 17 21"></polyline>'
                    +   '<rect x="8" y="8" width="8" height="8" rx="1"></rect>'
                    + '</svg>';

                btn.onclick = function() {
                    fitToInitial(true);
                };

                return container;
            }
        });
        map.addControl(new ResetViewControl());

        // ==================== ZOOM SLIDER ====================
        var ZoomSlider = L.Control.extend({
            options: { position: 'topleft' },
            onAdd: function(map) {
                var container = L.DomUtil.create('div', 'zoom-slider-container');
                L.DomEvent.disableClickPropagation(container);
                L.DomEvent.disableScrollPropagation(container);

                var btnPlus = L.DomUtil.create('button', 'zoom-slider-btn', container);
                btnPlus.innerHTML = '+';
                btnPlus.title = 'Mais zoom';

                var track = L.DomUtil.create('div', 'zoom-slider-track', container);
                var fill = L.DomUtil.create('div', 'zoom-slider-fill', track);
                var thumb = L.DomUtil.create('div', 'zoom-slider-thumb', track);

                var btnMinus = L.DomUtil.create('button', 'zoom-slider-btn', container);
                btnMinus.innerHTML = '−';
                btnMinus.title = 'Menos zoom';

                var minZoom = map.getMinZoom() || 3;
                var maxZoom = map.getMaxZoom() || 19;
                var dragging = false;

                function updateSlider() {
                    var pct = (map.getZoom() - minZoom) / (maxZoom - minZoom);
                    pct = Math.max(0, Math.min(1, pct));
                    thumb.style.bottom = (pct * 100) + '%';
                    fill.style.height = (pct * 100) + '%';
                }

                function zoomFromY(e) {
                    var rect = track.getBoundingClientRect();
                    var y = (e.touches && e.touches[0]) ? e.touches[0].clientY : e.clientY;
                    var pct = 1 - ((y - rect.top) / rect.height);
                    pct = Math.max(0, Math.min(1, pct));
                    map.setZoom(minZoom + pct * (maxZoom - minZoom));
                }

                btnPlus.onclick = function() { map.zoomIn(0.5); };
                btnMinus.onclick = function() { map.zoomOut(0.5); };

                track.addEventListener('mousedown', function(e) { dragging = true; zoomFromY(e); });
                document.addEventListener('mousemove', function(e) { if (dragging) zoomFromY(e); });
                document.addEventListener('mouseup', function() { dragging = false; });

                track.addEventListener('touchstart', function(e) { dragging = true; zoomFromY(e); e.preventDefault(); }, { passive: false });
                document.addEventListener('touchmove', function(e) { if (dragging) zoomFromY(e); });
                document.addEventListener('touchend', function() { dragging = false; });

                map.on('zoomend zoom', updateSlider);
                setTimeout(updateSlider, 100);

                return container;
            }
        });
        map.addControl(new ZoomSlider());

        // ==================== INICIALIZAÇÃO ====================
        renderSectors();
        renderSegments();
        renderPontosViciados();
        populateFilters();
        // Sem a conta pronta do Python: a mesma conta feita aqui (= "Todos"). Sem
        // isso a barra abria vazia e o foco, ao soltar, a preenchia.
        if (typeof window.HEATMAP_OVERALL !== 'number') updateDynamicStats();
        initPriorityControl();
        initPontosViciadosControl();
        initSectorLabelsControl();
        initAdvancedConfigPopover();

        // Posiciona painel esquerdo após todos os elementos existirem
        setTimeout(positionLeftPanel, 300);

        // Aplica fit inicial após o posicionamento (paddings refletem layout real)
        setTimeout(function() { fitToInitial(false); }, 360);

        // Re-fit em window.load: garante layout final (imagens dos logos,
        // dimensões reais da legenda) antes do enquadramento definitivo.
        var _initialFitDone = false;
        function _runInitialFit() {
            if (_initialFitDone) return;
            // Re-aplica position do painel esquerdo (legenda final) e fit.
            try { positionLeftPanel(); } catch (e) {}
            fitToInitial(false);
            _initialFitDone = true;
        }
        if (document.readyState === 'complete') {
            setTimeout(_runInitialFit, 50);
        } else {
            window.addEventListener('load', function() {
                setTimeout(_runInitialFit, 50);
            });
        }

        console.log('Mapa Leaflet inicializado com sucesso!');
        console.log('Segmentos:', window.SEGMENTS.features.length);
        console.log('Setores:', window.SECTORS.features.length);
        console.log('Prioritárias:', allPriorityHalos.length);
    })();
    </script>
    """])

def _compress_passage_data(segments):
    """Extrai passage_details, comprime com gzip, retorna base64."""
    data = {}
    for idx_num, (idx, row) in enumerate(segments.iterrows()):
        pd_val = row.get('passage_details')
        if isinstance(pd_val, list) and pd_val:
            data[str(idx_num)] = pd_val
    json_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
    compressed = gzip.compress(json_bytes)
    return base64.b64encode(compressed).decode('ascii')

def export_segments_osm_html(contract_name, segments_map, sectors, date_from, date_to, single_day_mode, overall_ratio=None, overall_with_manual=None, secure_mode=True, pontos_viciados_gdf=None):
    """
    Exporta mapa HTML interativo com Leaflet.
    
    Usa html_templates.py para estrutura (como plotting_original.py).
    
    Args:
        secure_mode: Se True (padrão), criptografa dados embutidos no HTML
                     com AES-256-GCM, troca tiles para versão sem labels,
                     encapsula em closure e adiciona proteções anti-DevTools.
    """
    def img_to_base64(filename):
        """Lê imagem e retorna base64. Busca em _MEIPASS e depois em dependencias/."""
        candidates = []
        if getattr(sys, 'frozen', False):
            meipass = getattr(sys, '_MEIPASS', None)
            if meipass:
                candidates.append((Path(meipass) / filename))
            start = Path(sys.executable).parent
        else:
            start = Path(__file__).resolve().parent
        for directory in ([start] + list(start.parents)[:6]):
            candidates.append(((directory / 'dependencias') / filename))
            candidates.append((directory / filename))
        for p in candidates:
            try:
                data = p.read_bytes()
                return base64.b64encode(data).decode('utf-8')
            except Exception:
                continue
        return ''

    logo_main = img_to_base64('logo_DIE.png')
    logo_1 = img_to_base64('logo_locar.png')
    simple_name, display_name = simplify_contract_name(contract_name)
    from project_heatmaps.pontos_viciados import pontos_to_geojson as _pv_to_geojson
    pv_geojson = _pv_to_geojson(pontos_viciados_gdf) if pontos_viciados_gdf is not None else {'type': 'FeatureCollection', 'features': []}
    pv_json = json.dumps(pv_geojson, ensure_ascii=False)
    seg, sec = prepare_map_data_leaflet(segments_map, sectors, single_day_mode, contract_name=contract_name)
    if seg.empty:
        print(f"[WARN] Nenhum segmento para {contract_name}")
        return None
    if single_day_mode:
        active_days = {date_from.weekday()}
    else:
        active_days = {d.weekday() for d in pd.date_range(date_from, date_to, freq='D')}
    seg['_shared'] = shared_sector_keys(seg)
    segments_geojson = segments_to_geojson(seg, single_day_mode, date_from)
    sectors_geojson = sectors_to_geojson(sec, active_days)
    add_stats_to_features(sectors_geojson['features'], sector_stats(seg))
    if secure_mode:
        passage_data = prepare_passage_data(seg)
    else:
        passage_b64 = _compress_passage_data(seg)
    filter_options = build_filter_options(seg, single_day_mode)
    history_df = load_collection_history(contract_name, day_and_night_mode=False)
    history_data = prepare_history_data(history_df, date_from, date_to, single_day_mode, overall_ratio, overall_with_manual)
    bounds = seg.total_bounds
    center_lat = ((bounds[1] + bounds[3]) / 2)
    center_lon = ((bounds[0] + bounds[2]) / 2)
    date_str = f"do dia {date_from.strftime('%d/%m/%Y')}" if single_day_mode else f"de {date_from.strftime('%d/%m/%Y')} à {date_to.strftime('%d/%m/%Y')}"
    title = f"Mapa de Execução de {display_name} {date_str}"
    if single_day_mode:
        legend_items = [{'color': '#00ff00', 'label': 'Coletadas'}, {'color': '#ffff00', 'label': 'Possível falha de GPS'}, {'color': '#ff0000', 'label': 'Não coletadas'}, {'color': '#008080', 'label': 'Possível coleta manual'}, {'color': '#00FFFF', 'label': 'Manual: coletada'}, {'color': '#0088FF', 'label': 'Manual: possível falha GPS'}, {'color': '#0044AA', 'label': 'Manual: não coletada'}]
    else:
        legend_items = [{'color': '#00ff00', 'label': '80-100%'}, {'color': '#ffff00', 'label': '60-80%'}, {'color': '#ffa500', 'label': '40-60%'}, {'color': '#ff0000', 'label': '20-40%'}, {'color': '#4b0000', 'label': '0-20%'}, {'color': '#008080', 'label': 'Possível coleta manual'}, {'color': '#00FFFF', 'label': 'Manual: 80-100%'}, {'color': '#00CCEE', 'label': 'Manual: 60-80%'}, {'color': '#0099CC', 'label': 'Manual: 40-60%'}, {'color': '#0066AA', 'label': 'Manual: 20-40%'}, {'color': '#003388', 'label': 'Manual: 0-20%'}]
    pv_has_features = bool(pv_geojson.get('features'))
    pv_legend_items = []
    if pv_has_features:
        pv_legend_items = [{'color': '#27ae60', 'label': 'PV: menor tempo de parada', 'circle': True}, {'color': '#f1c40f', 'label': 'PV: tempo médio', 'circle': True}, {'color': '#e67e22', 'label': 'PV: tempo longo', 'circle': True}, {'color': '#c0392b', 'label': 'PV: maior tempo de parada', 'circle': True}, {'color': '#888888', 'label': 'PV: sem dados no período', 'circle': True}]
    legend_html = '<div id="legend"><h4>Legenda</h4>'
    for item in legend_items:
        legend_html += f"<div class=\"legend-item\"><div class=\"legend-color\" style=\"background:{item['color']}\"></div><span>{item['label']}</span></div>"
    if pv_legend_items:
        legend_html += '<div id="legend-pv"><hr style="margin:6px 0;border-color:#ccc;"><div style="font-size:11px;font-weight:600;margin-bottom:4px;">Pontos Viciados</div>'
        for item in pv_legend_items:
            legend_html += f"<div class=\"legend-item\"><div style=\"width:14px;height:14px;border-radius:50%;background:{item['color']};border:2px solid {item['color']};margin-right:8px;flex-shrink:0;\"></div><span>{item['label']}</span></div>"
            continue
        legend_html += '</div>'
    legend_html += '</div>'
    geocoder_html = """
        <div id="geocoder-container">
            <button id="geocoder-toggle" title="Buscar endereco">
                <svg id="geocoder-icon-search" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                </svg>
                <svg id="geocoder-icon-loading" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:none;">
                    <circle cx="12" cy="12" r="10"></circle>
                    <path d="M12 6v6l4 2"></path>
                </svg>
            </button>
            <div id="geocoder-input-wrapper">
                <input type="text" id="geocoder-input" placeholder="Buscar endereco..." autocomplete="off">
            </div>
            <div id="geocoder-results"></div>
        </div>
    """
    body_class = 'legend-large' if not single_day_mode else ''
    leaflet_map_html = f"\n        {leaflet_css_inline()}\n        <div id=\"leafletMap\"></div>\n        <div id=\"map-title\">{title}</div>\n        {legend_html}\n        {geocoder_html}\n    "
    if secure_mode:
        scripts_html = build_secure_html_scripts(segments_geojson=segments_geojson, sectors_geojson=sectors_geojson, filter_options=filter_options, history_data=history_data, overall_ratio=history_data.get('overall'), overall_gps=history_data.get('overall_gps'), overall_manual=history_data.get('overall_manual'), overall_manual_gps=history_data.get('overall_manual_gps'), pontos_viciados_geojson=pv_geojson, center_lat=center_lat, center_lon=center_lon, single_day_mode=single_day_mode, passage_data=passage_data, get_leaflet_scripts_fn=get_leaflet_scripts)
    else:
        def _sanitize_for_json(obj):
            """Remove surrogates recursivamente de strings em dicts/lists."""
            if isinstance(obj, str):
                try:
                    obj.encode('utf-8')
                    return obj
                except (UnicodeEncodeError, UnicodeDecodeError):
                    return obj.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')
            if isinstance(obj, dict):
                return {k: _sanitize_for_json(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_sanitize_for_json(v) for v in obj]
            return obj

        try:
            segments_json = json.dumps(segments_geojson, ensure_ascii=False)
        except (UnicodeEncodeError, UnicodeDecodeError):
            segments_json = json.dumps(_sanitize_for_json(segments_geojson), ensure_ascii=False)
        sectors_json = json.dumps(sectors_geojson, ensure_ascii=False)
        filters_json = json.dumps(filter_options, ensure_ascii=False)
        history_json = json.dumps(history_data, ensure_ascii=False)
        overall_json = json.dumps(history_data.get('overall'))
        overall_gps_json = json.dumps(history_data.get('overall_gps'))
        overall_manual_json = json.dumps(history_data.get('overall_manual'))
        overall_manual_gps_json = json.dumps(history_data.get('overall_manual_gps'))
        scripts_html = get_leaflet_scripts(segments_json, sectors_json, filters_json, history_json, overall_json, overall_gps_json, overall_manual_json, overall_manual_gps_json, center_lat, center_lon, single_day_mode, passage_b64, pontos_viciados_json=pv_json)
    out_file = f"{simple_name}_dia-{date_from.strftime('%d.%m.%Y')}.html" if single_day_mode else f"{simple_name}_de-{date_from.strftime('%d.%m.%Y')}-à-{date_to.strftime('%d.%m.%Y')}.html"
    html = f"\n<!DOCTYPE html>\n<html lang='pt-BR'>\n<head>\n    <meta charset='utf-8' />\n    <meta name='viewport' content='width=device-width,initial-scale=1'>\n    <title>{out_file}</title>\n    {get_styles()}\n    {get_leaflet_styles()}\n</head>\n<body class='sidebar-open {body_class}'>\n    <div class=\"main-wrapper\">\n        <div class=\"plot-container\">\n            <div id=\"mapPlot\" class=\"plot-wrapper\">\n                {leaflet_map_html}\n            </div>\n        </div>\n    </div>\n    \n    {get_sidebar_template()}\n    \n    <!-- Detail sidebar (por cima do sidebar principal) -->\n    <div id=\"detail-sidebar\">\n        <div class=\"detail-header\">\n            <h3 id=\"detail-title\">Detalhes do Segmento</h3>\n            <button class=\"detail-close-btn\" id=\"detail-close-btn\" title=\"Fechar\">&times;</button>\n        </div>\n        <div class=\"detail-body\" id=\"detail-body\">\n            <div class=\"detail-no-data\">Clique em um segmento de rota para ver detalhes.</div>\n        </div>\n    </div>\n    \n    <div id='logo_container'>\n        <div class=\"logo-bar\">\n            <div class=\"logo-left\">\n                <img src=\"data:image/webp;base64,{logo_main}\">\n            </div>\n            <div class=\"logo-right\">\n                <img src=\"data:image/webp;base64,{logo_1}\">\n            </div>\n        </div>\n    </div>\n    \n    {scripts_html}\n</body>\n</html>\n    "
    out_path = PATHS.heatmaps_dir / out_file
    if secure_mode:
        html = inject_ai_poison(html, contract_name=contract_name)
    if secure_mode:
        _wm = get_html_watermark_overlay()
        if '</body>' in html.lower():
            _idx = html.lower().rfind('</body>')
            html = (((html[:_idx] + _wm) + """
""") + html[_idx:])
        else:
            html += _wm
    with open(out_path, 'w', encoding='utf-8', errors='xmlcharrefreplace') as f:
        f.write(html)
    print(f"[{contract_name}] Mapa Leaflet salvo: {out_path}")
    return out_path

