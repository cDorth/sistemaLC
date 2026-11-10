"""
Integração de segurança para plotting.py

Este módulo fornece funções que se conectam ao plotting.py existente
para gerar HTMLs com dados criptografados.

Para integrar ao plotting.py:
1. Adicionar: from project_heatmaps.data_security import generate_secure_map_script, prepare_passage_data, TILE_URL_NO_LABELS
2. Adicionar: from project_heatmaps.secure_integration import build_secure_html_scripts
3. Em export_segments_osm_html(), substituir a chamada a get_leaflet_scripts()
   por build_secure_html_scripts() quando secure_mode=True

Ou simplesmente usar export_secure_html() deste módulo diretamente.
"""

import json
import re
import logging
from typing import Any, Dict, Optional
from project_heatmaps.data_security import generate_secure_map_script, prepare_passage_data, localize_window_references, TILE_URL_NO_LABELS, TILE_ATTRIBUTION
logger = logging.getLogger(__name__)

def _extract_map_init_js_from_raw(raw_scripts):
    """
    Extrai o código de inicialização do mapa do output de get_leaflet_scripts().

    Processo:
    1. Localiza o segundo bloco <script> (código do mapa)
    2. Remove wrapper IIFE
    3. Substitui window.* por referências locais
    4. Troca tile layer para versão sem labels
    5. Remove console.log/warn
    """
    import re
    inline_blocks = list(re.finditer('<script>(.+?)</script>', raw_scripts, re.DOTALL))
    if len(inline_blocks) < 2:
        raise ValueError(f"Estrutura de script inesperada: encontrados {len(inline_blocks)} blocos inline, esperados >= 2")
    map_block = inline_blocks[-1].group(1).strip()
    strict_marker = "'use strict';"
    strict_pos = map_block.find(strict_marker)
    if strict_pos == -1:
        raise ValueError("'use strict' não encontrado no bloco do mapa")
    inner_start = (strict_pos + len(strict_marker))
    inner_end = map_block.rfind('})();')
    if inner_end == -1:
        inner_end = map_block.rfind('})()')
    if inner_end == -1:
        raise ValueError('Fechamento IIFE })(); não encontrado')
    inner_code = map_block[inner_start:inner_end].strip()
    inner_code = localize_window_references(inner_code)
    lines = inner_code.split("""
""")
    clean_lines = [line for line in lines if 'console.log' not in line and 'console.warn' not in line]
    return """
""".join(clean_lines)

def build_secure_html_scripts(segments_geojson, sectors_geojson, filter_options, history_data, overall_ratio, overall_gps, overall_manual, overall_manual_gps, center_lat, center_lon, single_day_mode, passage_data, get_leaflet_scripts_fn, pontos_viciados_geojson=None):
    """
    Constrói o bloco <script> seguro completo para um mapa HTML.

    Em vez de embutir dados em texto puro, criptografa tudo com AES-256-GCM
    e adiciona proteções anti-DevTools.

    Args:
        segments_geojson: GeoJSON dos segmentos
        sectors_geojson: GeoJSON dos setores
        filter_options: Opções de filtro
        history_data: Dados de histórico
        overall_ratio: Ratio geral (strict)
        overall_gps: Ratio geral (com GPS gap)
        overall_manual: Ratio manual (strict)
        overall_manual_gps: Ratio manual (com GPS gap)
        center_lat: Latitude do centro do mapa
        center_lon: Longitude do centro do mapa
        single_day_mode: Se é modo dia único
        passage_data: Dict de detalhes de passagem {fid: details}
        get_leaflet_scripts_fn: Referência à função get_leaflet_scripts()
                                (passada para evitar import circular)

    Returns:
        String HTML com tags <script> (libs externas + código seguro)
    """
    raw_js = get_leaflet_scripts_fn(segments_json='null', sectors_json='null', filters_json='{}', history_json='{"dates":[],"pcts":[],"pcts_gps":[]}', overall_json='null', overall_gps_json='null', overall_manual_json='null', overall_manual_gps_json='null', center_lat=center_lat, center_lon=center_lon, single_day_mode=single_day_mode, passage_b64='', pontos_viciados_json='null', basemap_geojson=(segments_geojson, sectors_geojson, pontos_viciados_geojson))
    map_init_js = _extract_map_init_js_from_raw(raw_js)
    logger.info(f"Map init JS extraído: {len(map_init_js)} caracteres")
    payload = {'SEGMENTS': segments_geojson, 'SECTORS': sectors_geojson, 'FILTERS': filter_options, 'HEATMAP_HISTORY': history_data, 'HEATMAP_OVERALL': overall_ratio, 'HEATMAP_OVERALL_GPS': overall_gps, 'HEATMAP_OVERALL_MANUAL': overall_manual, 'HEATMAP_OVERALL_MANUAL_GPS': overall_manual_gps, 'SINGLE_DAY_MODE': single_day_mode, 'PASSAGE_DATA': passage_data, 'PONTOS_VICIADOS': (pontos_viciados_geojson or {'type': 'FeatureCollection', 'features': []})}
    secure_js = generate_secure_map_script(payload=payload, map_init_js=map_init_js)
    logger.info('Script seguro gerado com sucesso')
    return secure_js

def _extract_mobile_app_js(full_html):
    """
    Extrai o bloco de aplicação JS de um HTML mobile gerado por _build_mobile_html().

    O HTML mobile tem a estrutura:
        <script src="leaflet.js"></script>
        <script>window.SEGMENTS_DATA = ...;</script>     ← dados (ignorado)
        <script>(function() { ... })();</script>          ← aplicação (extraído)

    Processo:
    1. Localiza o último bloco <script>...</script> inline
    2. Remove wrapper IIFE
    3. Substitui window.* por referências locais
    4. Troca tile layer para versão sem labels
    5. Remove console.log/warn
    """
    import re
    inline_blocks = list(re.finditer('<script>(.+?)</script>', full_html, re.DOTALL))
    if len(inline_blocks) < 2:
        raise ValueError(f"Estrutura mobile inesperada: encontrados {len(inline_blocks)} blocos inline, esperados >= 2")
    app_block = inline_blocks[-1].group(1).strip()
    iife_start = app_block.find('(function()')
    if iife_start == -1:
        raise ValueError('IIFE (function() não encontrado no bloco mobile')
    brace_start = app_block.find('{', iife_start)
    if brace_start == -1:
        raise ValueError('Abertura { da IIFE não encontrada')
    iife_end = app_block.rfind('})();')
    if iife_end == -1:
        iife_end = app_block.rfind('})()')
    if iife_end == -1:
        raise ValueError('Fechamento IIFE })(); não encontrado no mobile')
    inner_code = app_block[(brace_start + 1):iife_end].strip()
    inner_code = localize_window_references(inner_code)
    lines = inner_code.split("""
""")
    clean_lines = [line for line in lines if 'console.log' not in line and 'console.warn' not in line]
    return """
""".join(clean_lines)

def build_secure_mobile_scripts(segments_geojson, sectors_geojson, filter_config, history_data, single_day_mode, build_mobile_html_fn, pontos_viciados_geojson=None):
    """
    Constrói o bloco <script> seguro completo para o mapa MOBILE.

    Fluxo:
    1. Chama _build_mobile_html com dados dummy para obter a estrutura JS
    2. Extrai o bloco de aplicação JS
    3. Monta payload de dados para criptografia
    4. Gera script seguro (AES-256-GCM + anti-DevTools + closure)

    Args:
        segments_geojson: GeoJSON dos segmentos
        sectors_geojson: GeoJSON dos setores
        filter_config: Filtros mobile
        history_data: Dados de histórico
        single_day_mode: Se é modo dia único
        build_mobile_html_fn: Referência à _build_mobile_html()

    Returns:
        String HTML com tags <script> (Leaflet + código seguro)
    """
    from datetime import date as dt_date
    dummy_date = dt_date(2025, 1, 1)
    dummy_geojson = {'type': 'FeatureCollection', 'features': []}
    dummy_history = {'dates': [], 'pcts': [], 'pcts_gps': [], 'overall': None, 'overall_gps': None, 'overall_manual': None, 'overall_manual_gps': None}
    raw_html = build_mobile_html_fn(segments_geojson=dummy_geojson, sectors_geojson=dummy_geojson, filter_config={'byRatio': [], 'byCode': []}, history_data=dummy_history, contract_name='dummy', date_from=dummy_date, date_to=dummy_date, single_day_mode=single_day_mode, basemap_geojson=(segments_geojson, sectors_geojson))
    map_init_js = _extract_mobile_app_js(raw_html)
    logger.info(f"Mobile app JS extraído: {len(map_init_js)} caracteres")
    payload = {'SEGMENTS_DATA': segments_geojson, 'SECTORS_DATA': sectors_geojson, 'FILTER_CONFIG': filter_config, 'HISTORY_DATA': history_data, 'SINGLE_DAY_MODE': single_day_mode, 'PONTOS_VICIADOS': (pontos_viciados_geojson or {'type': 'FeatureCollection', 'features': []})}
    secure_js = generate_secure_map_script(payload=payload, map_init_js=map_init_js, plotly_src=None)
    logger.info('Script seguro mobile gerado com sucesso')
    return secure_js

def build_secure_stops_scripts(segments_geojson, sectors_geojson, filter_options, history_data, overall_value, center_lat, center_lon, vehicle_data, get_stops_scripts_fn):
    """
    Constrói o bloco <script> seguro para o mapa de PARADAS desktop.

    Args:
        segments_geojson: GeoJSON dos segmentos com dados de parada
        sectors_geojson: GeoJSON dos setores
        filter_options: Filtros de intensidade
        history_data: Dados de histórico de paradas
        overall_value: Valor overall (float ou None)
        center_lat/lon: Centro do mapa
        vehicle_data: Dict {fid: vehicle_details}
        get_stops_scripts_fn: Referência à _get_stops_leaflet_scripts()
    """
    raw_js = get_stops_scripts_fn(segments_json='null', sectors_json='null', filters_json='{}', history_json='{"dates":[],"pcts":[]}', overall_json='null', center_lat=center_lat, center_lon=center_lon, details_b64='', basemap_geojson=(segments_geojson, sectors_geojson))
    map_init_js = _extract_map_init_js_from_raw(raw_js)
    logger.info(f"Stops desktop JS extraído: {len(map_init_js)} caracteres")
    payload = {'SEGMENTS': segments_geojson, 'VEHICLE_DATA': vehicle_data, 'SECTORS': sectors_geojson, 'FILTERS': filter_options, 'STOPS_HISTORY': history_data, 'STOPS_OVERALL': overall_value}
    secure_js = generate_secure_map_script(payload=payload, map_init_js=map_init_js, plotly_src='https://cdn.plot.ly/plotly-2.27.0.min.js')
    logger.info('Script seguro stops desktop gerado')
    return secure_js

def build_secure_stops_mobile_scripts(segments_geojson, sectors_geojson, filter_options, history_data, overall_pct, build_stops_mobile_html_fn):
    """
    Constrói o bloco <script> seguro para o mapa de PARADAS mobile.

    Args:
        segments_geojson: GeoJSON dos segmentos
        sectors_geojson: GeoJSON dos setores
        filter_options: Filtros
        history_data: Dados de histórico
        overall_pct: Percentual overall (float ou None)
        build_stops_mobile_html_fn: Referência à _build_stops_mobile_html()
    """
    import json as _json
    dummy_geojson = _json.dumps({'type': 'FeatureCollection', 'features': []})
    dummy_hist = _json.dumps({'dates': [], 'pcts': []})
    raw_html = build_stops_mobile_html_fn(seg_json=dummy_geojson, sec_json=dummy_geojson, filt_json='{"byRatio":[],"byCode":[]}', hist_json=dummy_hist, overall_pct_json='null', display_name='dummy', date_str='01/01/2025', basemap_geojson=(segments_geojson, sectors_geojson))
    map_init_js = _extract_mobile_app_js(raw_html)
    logger.info(f"Stops mobile JS extraído: {len(map_init_js)} caracteres")
    payload = {'SEGMENTS_DATA': segments_geojson, 'SECTORS_DATA': sectors_geojson, 'FILTER_CONFIG': filter_options, 'STOPS_HISTORY': history_data, 'STOPS_OVERALL_PCT': overall_pct}
    secure_js = generate_secure_map_script(payload=payload, map_init_js=map_init_js, plotly_src='https://cdn.plot.ly/plotly-2.27.0.min.js')
    logger.info('Script seguro stops mobile gerado')
    return secure_js

