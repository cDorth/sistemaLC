"""
Visualização de mapas de paradas críticas (stops_mode).

Gera HTMLs interativos com Leaflet mostrando intensidade de paradas/lentidão
ao longo das rotas de coleta, com sidebar, filtros, popup detalhado e gráfico
de histórico.

Segue a mesma arquitetura de plotting.py, compartilhando html_templates.py
para sidebar base, logos e CSS global.
"""

import os
import sys
import json
import gzip
import base64
import functools
import logging
from pathlib import Path
from datetime import date
from typing import Optional, Sequence
import numpy as np
import pandas as pd
import geopandas as gpd
from project_heatmaps.config import BASEMAP_MOBILE_TETO_ARQUIVO_MB, COLORS, PATHS, simplify_contract_name
from project_heatmaps.stops_stats import load_stop_history
from project_heatmaps.basemap_cache import bloco_camada_base, caber_no_teto
from project_heatmaps.web_assets import leaflet_css_inline, leaflet_js_inline
from project_heatmaps.html_templates import get_styles
from project_heatmaps.parsers import sector_code_sort_key
from project_heatmaps.secure_integration import build_secure_stops_scripts, build_secure_stops_mobile_scripts
from project_heatmaps.ai_poison import inject_ai_poison
from project_heatmaps.watermark import get_html_watermark_overlay
logger = logging.getLogger(__name__)

def _prepare_stops_map_data(segments_map, sectors):
    """Prepara dados para visualização Leaflet."""
    seg = segments_map.copy()
    sec = sectors.copy()
    if seg.crs and seg.crs.to_epsg() != 4326:
        seg = seg.to_crs(4326)
    if sec.crs and sec.crs.to_epsg() != 4326:
        sec = sec.to_crs(4326)
    seg = seg[((~seg.geometry.is_empty) & seg.geometry.notna())].copy()
    sec = sec[((~sec.geometry.is_empty) & sec.geometry.notna())].copy()
    if 'stop_ratio' not in seg.columns:
        seg['stop_ratio'] = 0.0
    def _stop_color(ratio):
        if pd.isna(ratio):
            return '#808080'
        if ratio <= 0.2:
            return '#00ff00'
        if ratio <= 0.4:
            return '#ffff00'
        if ratio <= 0.6:
            return '#ffa500'
        if ratio <= 0.8:
            return '#ff0000'
        return '#4b0000'

    seg['color'] = seg['stop_ratio'].apply(_stop_color)
    def _stop_category(ratio):
        if pd.isna(ratio):
            return 'unknown'
        if ratio <= 0.2:
            return '0-20'
        if ratio <= 0.4:
            return '20-40'
        if ratio <= 0.6:
            return '40-60'
        if ratio <= 0.8:
            return '60-80'
        return '80-100'

    seg['category'] = seg['stop_ratio'].apply(_stop_category)
    seg['flag_no_stops'] = seg['total_stops'] == 0
    return (seg, sec)

def _segments_to_geojson_stops(segments, date_from, date_to):
    """Converte segmentos com dados de paradas para GeoJSON."""
    features = []
    date_label = date_from.strftime('%d/%m/%Y') if date_from == date_to else f"{date_from.strftime('%d/%m/%Y')} → {date_to.strftime('%d/%m/%Y')}"
    for idx_num, (idx, row) in enumerate(segments.iterrows()):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        ratio = float((row.get('stop_ratio', 0) or 0))
        pct = round((ratio * 100), 1)
        total_stops = int((row.get('total_stops', 0) or 0))
        total_time = int((row.get('total_stop_time_sec', 0) or 0))
        avg_time = int((row.get('avg_stop_time_sec', 0) or 0))
        n_days = int((row.get('n_days_with_stops', 0) or 0))
        peak = (row.get('peak_period') or None)
        period_counts = row.get('period_counts') if isinstance(row.get('period_counts'), dict) else {}
        period_times = row.get('period_times') if isinstance(row.get('period_times'), dict) else {}
        props = {'route_code': str((row.get('route_code_full', '') or '')), 'schedule': str((row.get('route_schedule_label', '') or '')), 'sector': str((row.get('sector_code', '') or '')), 'color': row.get('color', '#808080'), 'category': row.get('category', 'unknown'), 'date_label': date_label, 'total_days': int((row.get('total_days', 0) or 0)), 'stop_pct': pct, 'total_stops': total_stops, 'total_time': total_time, 'avg_time': avg_time, 'n_days': n_days, 'peak': peak, 'pc': period_counts, 'pt': period_times, 'flag_no_stops': bool(row.get('flag_no_stops', False)), 'fid': idx_num}
        try:
            features.append({'type': 'Feature', 'geometry': geom.__geo_interface__, 'properties': props})
        except Exception:
            pass
        continue
    return {'type': 'FeatureCollection', 'features': features}

def _sectors_to_geojson(sectors, active_days):
    """Converte setores para GeoJSON (idêntico ao plotting.py)."""
    from project_heatmaps.parsers import get_sc_base_prefix
    def _is_active(days_set):
        if (not isinstance(days_set, set) or not days_set):
            return False
        return len((days_set & active_days)) > 0

    filtered = sectors[sectors['weekday_set'].apply(_is_active)].copy()
    features = []
    for _, row in filtered.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        prefix = get_sc_base_prefix(str(row.get('sector_prefix', '')))
        color = COLORS.sector_colors.get(prefix, COLORS.default_sector_color)
        props = {'name': str(row.get('sector_name', '')), 'prefix': prefix, 'color': color, 'schedule': str((row.get('schedule_label', '') or ''))}
        try:
            features.append({'type': 'Feature', 'geometry': geom.__geo_interface__, 'properties': props})
        except Exception:
            continue
    return {'type': 'FeatureCollection', 'features': features}

def _compress_vehicle_details(segments):
    """Extrai vehicle_details, comprime com gzip, retorna base64."""
    data = {}
    for idx_num, (idx, row) in enumerate(segments.iterrows()):
        vd = row.get('vehicle_details')
        if isinstance(vd, list) and vd:
            data[str(idx_num)] = vd
    json_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
    compressed = gzip.compress(json_bytes)
    return base64.b64encode(compressed).decode('ascii')

def _build_stops_filters(segments):
    """Constrói opções de filtro para stops_mode."""
    filters = {'byRatio': [{'label': 'Todos', 'value': 'all'}, {'label': '80-100% (Muito crítico)', 'value': '80-100'}, {'label': '60-80% (Crítico)', 'value': '60-80'}, {'label': '40-60% (Lentidão)', 'value': '40-60'}, {'label': '20-40% (Leve)', 'value': '20-40'}, {'label': '0-20% (Fluido)', 'value': '0-20'}, {'label': 'Sem paradas', 'value': 'no_stops'}], 'byCode': [{'label': 'Todos os códigos', 'value': 'all'}]}
    if 'route_code_full' in segments.columns:
        codes = sorted(segments['route_code_full'].dropna().astype(str).unique().tolist(), key=sector_code_sort_key)
        codes = [c for c in codes if c.strip()]
        filters['byCode'].extend([{'label': f"Apenas {c}", 'value': c} for c in codes])
    return filters

def _prepare_stops_history(history_df, date_from, date_to, overall_stop_ratio):
    """Prepara dados de histórico de paradas para o gráfico."""
    dates = []
    pcts = []
    total_stops_list = []
    if history_df is not None or not history_df.empty:
        try:
            df = history_df.copy()
            df['date'] = pd.to_datetime(df['date'])
            df = df[(df['date'] >= pd.to_datetime(date_from) & df['date'] <= pd.to_datetime(date_to))].sort_values('date')
            dates = df['date'].dt.strftime('%Y-%m-%d').tolist()
            pcts = [float(x) for x in df['stop_pct'].tolist()]
            if 'total_stops' in df.columns:
                total_stops_list = [int(x) for x in df['total_stops'].tolist()]
        except Exception:
            pass
    overall_pct = round((overall_stop_ratio * 100), 2) if overall_stop_ratio is not None else None
    return {'dates': dates, 'pcts': pcts, 'total_stops': total_stops_list, 'period_from': date_from.strftime('%Y-%m-%d'), 'period_to': date_to.strftime('%Y-%m-%d'), 'overall': overall_pct}

def _get_stops_sidebar_template():
    """Retorna HTML da sidebar específica para stops_mode."""
    return """
    <div id='heatmap-sidebar' aria-hidden='false'>
        <div id='heatmap-sidebar-handle' title='Abrir / Fechar'>></div>
        <div class='sidebar-inner'>
            <div class='controls'>
                <h3>Filtros</h3>
                <label for='ratioFilterSelect'>Por intensidade</label>
                <select id='ratioFilterSelect'></select>
                <label for='codeFilterSelect'>Por código</label>
                <select id='codeFilterSelect'></select>
            </div>

            <div class='history'>
                <h3 id='history_tittle'>Histórico de Paradas</h3>
                <div class='sidebar-section'>
                    <div class='chart-wrapper'>
                        <div id='historyChart' class='small' aria-hidden='false'></div>
                    </div>
                </div>
                <div class="avg-box" id="avgBoxOverall">
                    <div class="avg-label" id="lblOverall">Intensidade Média de Paradas</div>
                    <div class="avg-bar">
                        <div class="avg-bar-fill" id="barOverall"></div>
                        <div class="avg-bar-text" id="txtOverall">—</div>
                    </div>
                </div>

                <div class="avg-box" id="avgBoxDynamic">
                    <div class="avg-label" id="lblDynamic">Intensidade dos Segmentos Visíveis</div>
                    <div class="avg-bar">
                        <div class="avg-bar-fill" id="barDynamic"></div>
                        <div class="avg-bar-text" id="txtDynamic">—</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """

def _get_stops_leaflet_styles():
    """Retorna CSS do Leaflet + detail sidebar + geocoder."""
    from project_heatmaps.plotting import get_leaflet_styles
    return get_leaflet_styles()

def _get_stops_leaflet_scripts(segments_json, sectors_json, filters_json, history_json, overall_json, center_lat, center_lon, details_b64, basemap_geojson=None):
    """Retorna bloco <script> completo para stops_mode."""
    fontes_basemap = basemap_geojson if basemap_geojson is not None else sectors_json
    basemap_block = bloco_camada_base(*fontes_basemap)
    return f"\n    <!-- Leaflet JS -->\n    {leaflet_js_inline()}\n    <script src=\"https://cdn.plot.ly/plotly-2.27.0.min.js\"></script>\n\n    <script>\n        window.SEGMENTS = {segments_json};\n        window.VEHICLE_B64 = \"{details_b64}\";\n        window.VEHICLE_DATA = {{}};\n        window.SECTORS = {sectors_json};\n        window.FILTERS = {filters_json};\n        window.STOPS_HISTORY = {history_json};\n        window.STOPS_OVERALL = {overall_json};\n\n        (function() {{\n            try {{\n                var b64 = window.VEHICLE_B64;\n                var binary = atob(b64);\n                var bytes = new Uint8Array(binary.length);\n                for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);\n                var ds = new DecompressionStream('gzip');\n                var writer = ds.writable.getWriter();\n                writer.write(bytes);\n                writer.close();\n                new Response(ds.readable).text().then(function(text) {{\n                    window.VEHICLE_DATA = JSON.parse(text);\n                    console.log('[OK] Vehicle details descomprimidos');\n                }});\n            }} catch(e) {{\n                console.warn('Erro ao descomprimir vehicle_details:', e);\n            }}\n        }})();\n    </script>\n\n    <script>\n    (function() {{\n        'use strict';\n\n        var hist = window.STOPS_HISTORY || {{ dates: [], pcts: [] }};\n        var overallPct = window.STOPS_OVERALL;\n\n        // ==================== UTILITÁRIOS ====================\n        function getContrastColor(hex) {{\n            hex = hex.replace('#','');\n            var r = parseInt(hex.substr(0,2),16);\n            var g = parseInt(hex.substr(2,2),16);\n            var b = parseInt(hex.substr(4,2),16);\n            return (0.299*r + 0.587*g + 0.114*b)/255 > 0.5 ? '#000' : '#fff';\n        }}\n        function darkenColor(hex, pct) {{\n            hex = hex.replace('#','');\n            var r = Math.floor(parseInt(hex.substr(0,2),16)*(1-pct/100));\n            var g = Math.floor(parseInt(hex.substr(2,2),16)*(1-pct/100));\n            var b = Math.floor(parseInt(hex.substr(4,2),16)*(1-pct/100));\n            return '#' + [r,g,b].map(function(x){{ return x.toString(16).padStart(2,'0'); }}).join('');\n        }}\n        function fmtTime(sec) {{\n            if (!sec || sec <= 0) return '0s';\n            var h = Math.floor(sec/3600);\n            var m = Math.floor((sec%3600)/60);\n            var s = Math.floor(sec%60);\n            if (h > 0) return h + 'h ' + m + 'min';\n            if (m > 0) return m + 'min ' + s + 's';\n            return s + 's';\n        }}\n        function escapeHtml(str) {{\n            var d = document.createElement('div');\n            d.appendChild(document.createTextNode(str));\n            return d.innerHTML;\n        }}\n\n        // ==================== MAPA ====================\n        var map = L.map('leafletMap', {{\n            center: [{center_lat}, {center_lon}],\n            zoom: 12,\n            zoomControl: false,\n            preferCanvas: true,\n            zoomSnap: 0.25,\n            zoomDelta: 0.5,\n            wheelPxPerZoomLevel: 120\n        }});\n        // Mapa de fundo: fonte única em config.py (bloco BASEMAP) — o provedor\n        // não é nomeado aqui de propósito, já trocou 2x em 2 dias;\n        // segunda opção registrada lá: VersaTiles Colorful.\n        {basemap_block}\n\n        // ==================== GEOCODER (idêntico ao plotting.py) ====================\n        var searchMarker = null;\n        var searchTimeout = null;\n        var geocoderContainer = document.getElementById('geocoder-container');\n        var geocoderToggle = document.getElementById('geocoder-toggle');\n        var geocoderInput = document.getElementById('geocoder-input');\n        var geocoderResults = document.getElementById('geocoder-results');\n        var isGeoExpanded = false;\n\n        function positionLeftPanel() {{\n            var slider = document.querySelector('.zoom-slider-container');\n            var legend = document.getElementById('legend');\n            var gap = 10;\n            if (slider && geocoderContainer) {{\n                var sliderRect = slider.getBoundingClientRect();\n                geocoderContainer.style.top = (sliderRect.bottom + gap) + 'px';\n            }}\n            if (geocoderContainer && legend) {{\n                var geoRect = geocoderContainer.getBoundingClientRect();\n                legend.style.top = (geoRect.bottom + gap) + 'px';\n            }}\n        }}\n        setTimeout(positionLeftPanel, 200);\n        window.addEventListener('resize', positionLeftPanel);\n\n        if (window.MutationObserver) {{\n            var panelObserver = new MutationObserver(function() {{\n                setTimeout(positionLeftPanel, 50);\n            }});\n            if (geocoderContainer) {{\n                panelObserver.observe(geocoderContainer, {{ attributes: true, subtree: true }});\n            }}\n        }}\n\n        if (geocoderToggle) {{\n            geocoderToggle.addEventListener('click', function(e) {{\n                e.stopPropagation();\n                isGeoExpanded = !isGeoExpanded;\n                if (isGeoExpanded) {{\n                    geocoderContainer.classList.add('expanded');\n                    setTimeout(function(){{ geocoderInput.focus(); }}, 100);\n                }} else {{\n                    geocoderContainer.classList.remove('expanded');\n                    geocoderResults.classList.remove('active');\n                    geocoderInput.value = '';\n                }}\n            }});\n        }}\n\n        if (geocoderInput) {{\n            geocoderInput.addEventListener('input', function() {{\n                clearTimeout(searchTimeout);\n                var q = this.value.trim();\n                searchTimeout = setTimeout(function() {{\n                    if (q.length < 3) {{ geocoderResults.classList.remove('active'); return; }}\n                    fetch('https://nominatim.openstreetmap.org/search?format=json&q=' + encodeURIComponent(q) + '&countrycodes=br&addressdetails=1&limit=5&accept-language=pt-BR')\n                    .then(function(r){{ return r.json(); }})\n                    .then(function(data) {{\n                        geocoderResults.innerHTML = '';\n                        if (!data.length) {{ geocoderResults.innerHTML = '<div class=\"no-results\">Nenhum resultado</div>'; }}\n                        else {{ data.forEach(function(item) {{\n                            var div = document.createElement('div');\n                            div.className = 'result-item';\n                            div.textContent = item.display_name;\n                            div.onclick = function() {{\n                                if (searchMarker) map.removeLayer(searchMarker);\n                                searchMarker = L.marker([parseFloat(item.lat), parseFloat(item.lon)]).addTo(map);\n                                searchMarker.bindPopup(item.display_name).openPopup();\n                                searchMarker.on('click', function(){{ map.removeLayer(searchMarker); searchMarker=null; }});\n                                map.setView([parseFloat(item.lat), parseFloat(item.lon)], 17);\n                                isGeoExpanded = false;\n                                geocoderContainer.classList.remove('expanded');\n                                geocoderResults.classList.remove('active');\n                            }};\n                            geocoderResults.appendChild(div);\n                        }}); }}\n                        geocoderResults.classList.add('active');\n                    }}).catch(function(){{ }});\n                }}, 400);\n            }});\n            geocoderInput.addEventListener('keydown', function(e) {{\n                if (e.key === 'Escape') {{ isGeoExpanded=false; geocoderContainer.classList.remove('expanded'); geocoderResults.classList.remove('active'); }}\n            }});\n            document.addEventListener('click', function(e){{\n                if (!e.target.closest('#geocoder-container')) {{\n                    if (isGeoExpanded) {{ isGeoExpanded=false; geocoderContainer.classList.remove('expanded'); }}\n                    geocoderResults.classList.remove('active');\n                }}\n            }});\n        }}\n\n        // ==================== SETORES ====================\n        var sectorsLayer = null;\n        function renderSectors() {{\n            if (sectorsLayer) map.removeLayer(sectorsLayer);\n            sectorsLayer = L.geoJSON(window.SECTORS, {{\n                style: function(f) {{ return {{ color: f.properties.color, weight: 2, opacity: 0.8, fillColor: f.properties.color, fillOpacity: 0.15 }}; }},\n                onEachFeature: function(f, layer) {{\n                    var p = f.properties;\n                    var tc = getContrastColor(p.color);\n                    layer.bindPopup('<div class=\"popup-header\" style=\"background:'+p.color+';color:'+tc+';\">'+p.name+'</div><div class=\"popup-content\"><b>Tipo:</b> '+p.prefix+'<br><b>Agenda:</b> '+(p.schedule||'—')+'</div>');\n                    layer.bindTooltip('<div class=\"tooltip-inner\" style=\"background:'+p.color+';color:'+tc+';\">'+p.name+'</div>', {{ permanent:false, direction:'center', className:'colored-tooltip' }});\n                }}\n            }}).addTo(map);\n        }}\n\n        // ==================== SEGMENTOS (STOPS) ====================\n        var segmentsLayer = null;\n        var allSegmentFeatures = [];\n\n        function renderSegments() {{\n            if (segmentsLayer) map.removeLayer(segmentsLayer);\n            allSegmentFeatures = [];\n\n            segmentsLayer = L.geoJSON(window.SEGMENTS, {{\n                style: function(f) {{ return {{ color: f.properties.color, weight: 3, opacity: 0.9 }}; }},\n                onEachFeature: function(f, layer) {{\n                    var p = f.properties;\n                    var c = p.color;\n                    var tc = getContrastColor(c);\n                    var bc = darkenColor(c, 30);\n\n                    // ---- Popup ----\n                    var html = '<div class=\"popup-header\" style=\"background:'+c+';color:'+tc+';border-bottom:2px solid '+bc+';\">🛑 '+p.route_code+'</div>';\n                    html += '<div class=\"popup-content\">';\n                    html += '<b>Setor:</b> '+p.sector+'<br>';\n                    html += '<b>Agenda:</b> '+p.schedule+'<br>';\n                    html += '<b>Período:</b> '+p.date_label+'<br>';\n                    html += '<b>Intensidade:</b> '+p.stop_pct.toFixed(1)+'%<br>';\n                    html += '<b>Paradas:</b> '+p.total_stops+' sessões<br>';\n                    html += '<b>Tempo total:</b> '+fmtTime(p.total_time)+'<br>';\n                    if (p.avg_time > 0) html += '<b>Tempo médio:</b> '+fmtTime(p.avg_time)+'<br>';\n                    if (p.peak) html += '<b>Pico:</b> '+p.peak+'<br>';\n                    html += '</div>';\n                    layer.bindPopup(html);\n\n                    layer.on('popupopen', function() {{ openDetailSidebar(p); }});\n                    layer.on('popupclose', function() {{ closeDetailSidebar(); }});\n\n                    // ---- Tooltip ----\n                    var ttText = p.route_code + ' — ' + p.stop_pct.toFixed(0) + '%';\n                    layer.bindTooltip(\n                        '<div class=\"tooltip-inner\" style=\"background:'+c+';color:'+tc+';border:1px solid '+bc+';\">'+ttText+'</div>',\n                        {{ sticky:true, direction:'top', className:'colored-tooltip' }}\n                    );\n\n                    allSegmentFeatures.push({{ layer: layer, props: p }});\n                }}\n            }}).addTo(map);\n\n            if (segmentsLayer.getBounds().isValid()) {{\n                map.fitBounds(segmentsLayer.getBounds(), {{ padding: [50, 50] }});\n            }}\n        }}\n\n        // ==================== FILTROS ====================\n        var currentRatioFilter = 'all';\n        var currentCodeFilter = 'all';\n\n        function applyFilters() {{\n            allSegmentFeatures.forEach(function(item) {{\n                var p = item.props;\n                var visible = true;\n\n                if (currentRatioFilter !== 'all') {{\n                    if (currentRatioFilter === 'no_stops') {{\n                        visible = p.flag_no_stops;\n                    }} else {{\n                        visible = (p.category === currentRatioFilter);\n                    }}\n                }}\n                if (visible && currentCodeFilter !== 'all') {{\n                    visible = (p.route_code === currentCodeFilter);\n                }}\n\n                if (visible) {{\n                    if (!map.hasLayer(item.layer)) segmentsLayer.addLayer(item.layer);\n                }} else {{\n                    if (map.hasLayer(item.layer)) segmentsLayer.removeLayer(item.layer);\n                }}\n            }});\n            updateDynamicStats();\n        }}\n\n        function updateDynamicStats() {{\n            var sumRatio = 0;\n            var cnt = 0;\n            allSegmentFeatures.forEach(function(item) {{\n                if (map.hasLayer(item.layer)) {{\n                    sumRatio += (item.props.stop_pct || 0);\n                    cnt++;\n                }}\n            }});\n            var avg = cnt > 0 ? (sumRatio / cnt) : null;\n            updateBar('barDynamic', 'txtDynamic', avg);\n        }}\n\n        function populateFilters() {{\n            var rs = document.getElementById('ratioFilterSelect');\n            var cs = document.getElementById('codeFilterSelect');\n            if (!rs || !cs) return;\n            window.FILTERS.byRatio.forEach(function(f) {{\n                var o = document.createElement('option'); o.value = f.value; o.textContent = f.label; rs.appendChild(o);\n            }});\n            window.FILTERS.byCode.forEach(function(f) {{\n                var o = document.createElement('option'); o.value = f.value; o.textContent = f.label; cs.appendChild(o);\n            }});\n            rs.addEventListener('change', function() {{ currentRatioFilter = this.value; applyFilters(); }});\n            cs.addEventListener('change', function() {{ currentCodeFilter = this.value; applyFilters(); }});\n        }}\n\n        // ==================== SIDEBAR TOGGLE ====================\n        var handle = document.getElementById('heatmap-sidebar-handle');\n        var sidebar = document.getElementById('heatmap-sidebar');\n        var sidebarOpen = true;\n        function updateSidebarState() {{\n            if (sidebarOpen) {{\n                document.body.classList.remove('sidebar-closed');\n                document.body.classList.add('sidebar-open');\n                sidebar.classList.remove('closed');\n                handle.textContent = '>';\n            }} else {{\n                document.body.classList.remove('sidebar-open');\n                document.body.classList.add('sidebar-closed');\n                sidebar.classList.add('closed');\n                handle.textContent = '<';\n            }}\n            setTimeout(function(){{ map.invalidateSize(); }}, 300);\n        }}\n        if (handle) {{\n            document.body.classList.add('sidebar-open');\n            handle.addEventListener('click', function() {{\n                sidebarOpen = !sidebarOpen;\n                updateSidebarState();\n            }});\n        }}\n\n        // ==================== BARRAS DE MÉDIA ====================\n        function colorFromPercent(pct) {{\n            // Invertida: 0% = verde (bom), 100% = vermelho (ruim)\n            var hue = Math.max(0, 120 - (pct / 100) * 120);\n            return 'hsl(' + hue + ', 85%, 45%)';\n        }}\n        function updateBar(barId, txtId, pct) {{\n            var bar = document.getElementById(barId);\n            var txt = document.getElementById(txtId);\n            if (!bar || !txt || typeof pct !== 'number') {{\n                if (txt) txt.textContent = '—';\n                if (bar) bar.style.width = '0%';\n                return;\n            }}\n            pct = Math.max(0, Math.min(100, pct));\n            bar.style.width = pct.toFixed(1) + '%';\n            bar.style.backgroundColor = colorFromPercent(pct);\n            txt.textContent = pct.toFixed(1).replace('.', ',') + '%';\n        }}\n\n        // Média geral\n        updateBar('barOverall', 'txtOverall', overallPct);\n\n        // ==================== GRÁFICO DE HISTÓRICO ====================\n        if (hist.dates && hist.dates.length && window.Plotly) {{\n            var x = hist.dates;\n            var y = hist.pcts;\n            var hoverTexts = x.map(function(d, i) {{\n                var day = d.slice(8,10);\n                var pct = (typeof y[i]==='number') ? y[i].toFixed(1).replace('.',',')+'%' : '—';\n                return 'Dia '+day+' — '+pct;\n            }});\n            var xRange = (hist.period_from && hist.period_to) ? [hist.period_from, hist.period_to] : undefined;\n\n            Plotly.newPlot('historyChart', [{{\n                x: x, y: y,\n                mode: 'lines+markers',\n                fill: 'tozeroy',\n                fillcolor: 'rgba(255, 80, 50, 0.18)',\n                line: {{ color: 'rgb(220, 60, 30)', width: 2 }},\n                marker: {{ size: 6 }},\n                text: hoverTexts,\n                hoverinfo: 'text',\n                name: 'Intensidade'\n            }}], {{\n                height: 200,\n                margin: {{ l: 32, r: 32, t: 26, b: 36 }},\n                autosize: true,\n                xaxis: {{ type:'date', tickformat:'%d/%m', tickangle:35, fixedrange:true, range: xRange }},\n                yaxis: {{ range:[0,100], fixedrange:true }},\n                hoverlabel: {{ bgcolor:'rgba(0,0,0,0.75)', bordercolor:'#fff', font:{{size:12,color:'#fff'}} }},\n                plot_bgcolor: 'rgba(0,0,0,0)',\n                paper_bgcolor: 'rgba(0,0,0,0)'\n            }}, {{ displayModeBar: false }});\n        }}\n\n        // ==================== DETAIL SIDEBAR (STOPS) ====================\n        var detailSidebar = document.getElementById('detail-sidebar');\n        var detailBody = document.getElementById('detail-body');\n        var detailCloseBtn = document.getElementById('detail-close-btn');\n\n        function openDetailSidebar(props) {{\n            if (!detailSidebar || !detailBody) return;\n\n            var details = (window.VEHICLE_DATA && window.VEHICLE_DATA[String(props.fid)]) || [];\n            var html = '';\n\n            // Info do segmento\n            html += '<div class=\"detail-segment-info\" style=\"border-left-color:'+(props.color||'#ff0000')+';\">';\n            html += '<div class=\"route-code\">🛑 '+(props.route_code||'')+'</div>';\n            html += '<div class=\"meta\">';\n            html += '<b>Setor:</b> '+(props.sector||'')+'<br>';\n            html += '<b>Agenda:</b> '+(props.schedule||'')+'<br>';\n            html += '<b>Intensidade:</b> '+props.stop_pct.toFixed(1)+'%<br>';\n            html += '<b>Paradas:</b> '+props.total_stops+' sessões<br>';\n            html += '<b>Tempo total:</b> '+fmtTime(props.total_time)+'<br>';\n            if (props.avg_time > 0) html += '<b>Tempo médio:</b> '+fmtTime(props.avg_time)+'<br>';\n\n            // Período de pico\n            if (props.peak) {{\n                html += '<br><b>⏰ Período mais frequente:</b> '+props.peak+'<br>';\n                var pt = props.pt || {{}};\n                var pc = props.pc || {{}};\n                var periods = ['Madrugada','Manhã','Tarde','Noite'];\n                html += '<div style=\"margin-top:6px;font-size:11px;\">';\n                periods.forEach(function(per) {{\n                    var cnt = pc[per] || 0;\n                    var time = pt[per] || 0;\n                    if (cnt > 0) {{\n                        var bold = (per === props.peak) ? 'font-weight:700;' : '';\n                        html += '<div style=\"'+bold+'margin:2px 0;\">'+per+': '+cnt+' sessões, '+fmtTime(time)+'</div>';\n                    }}\n                }});\n                html += '</div>';\n            }}\n            html += '</div></div>';\n\n            // Lista de veículos\n            if (!details || details.length === 0) {{\n                html += '<div class=\"detail-no-data\">Nenhuma parada registrada neste segmento.</div>';\n            }} else {{\n                html += '<div style=\"margin-bottom:8px;font-size:12px;color:#666;font-weight:600;\">Veículos (por tempo de parada):</div>';\n                for (var i = 0; i < details.length; i++) {{\n                    html += buildVehicleStopCard(details[i], i);\n                }}\n            }}\n\n            detailBody.innerHTML = html;\n            detailSidebar.classList.add('open');\n\n            var headers = detailBody.querySelectorAll('.detail-vehicle-header');\n            headers.forEach(function(h) {{\n                h.addEventListener('click', function() {{\n                    this.nextElementSibling.classList.toggle('expanded');\n                    this.classList.toggle('active');\n                }});\n            }});\n            if (headers.length > 0) headers[0].click();\n        }}\n\n        function closeDetailSidebar() {{\n            if (detailSidebar) detailSidebar.classList.remove('open');\n        }}\n        if (detailCloseBtn) {{\n            detailCloseBtn.addEventListener('click', function() {{\n                closeDetailSidebar();\n                map.closePopup();\n            }});\n        }}\n\n        function buildVehicleStopCard(v, idx) {{\n            var html = '<div class=\"detail-vehicle-card\">';\n            html += '<div class=\"detail-vehicle-header\">';\n            html += '<span class=\"detail-vehicle-name\"><span class=\"vehicle-icon\">&#x1F69A;</span> '+escapeHtml(v.v||'Desconhecido')+'</span>';\n            html += '<span style=\"display:flex;align-items:center;gap:6px;\">';\n            html += '<span class=\"detail-vehicle-badge\">'+fmtTime(v.t)+'</span>';\n            html += '<span class=\"detail-vehicle-expand-icon\">▼</span>';\n            html += '</span></div>';\n\n            html += '<div class=\"detail-vehicle-body\">';\n            html += '<div class=\"detail-summary-row\"><span class=\"label\">Sessões de parada</span><span>'+v.n+'</span></div>';\n            html += '<div class=\"detail-summary-row\"><span class=\"label\">Tempo total parado</span><span>'+fmtTime(v.t)+'</span></div>';\n            html += '<div class=\"detail-summary-row\"><span class=\"label\">Maior parada</span><span>'+fmtTime(v.mx);\n            if (v.mxd) html += ' ('+v.mxd;\n            if (v.mxt) html += ' '+v.mxt;\n            if (v.mxd) html += ')';\n            html += '</span></div>';\n            html += '</div></div>';\n            return html;\n        }}\n\n        // ==================== ZOOM SLIDER ====================\n        var ZoomSlider = L.Control.extend({{\n            options: {{ position: 'topleft' }},\n            onAdd: function(mapRef) {{\n                var container = L.DomUtil.create('div', 'zoom-slider-container');\n                L.DomEvent.disableClickPropagation(container);\n                L.DomEvent.disableScrollPropagation(container);\n\n                var btnPlus = L.DomUtil.create('button', 'zoom-slider-btn', container);\n                btnPlus.innerHTML = '+';\n                btnPlus.title = 'Mais zoom';\n\n                var track = L.DomUtil.create('div', 'zoom-slider-track', container);\n                var fill = L.DomUtil.create('div', 'zoom-slider-fill', track);\n                var thumb = L.DomUtil.create('div', 'zoom-slider-thumb', track);\n\n                var btnMinus = L.DomUtil.create('button', 'zoom-slider-btn', container);\n                btnMinus.innerHTML = '−';\n                btnMinus.title = 'Menos zoom';\n\n                var minZ = mapRef.getMinZoom() || 3;\n                var maxZ = mapRef.getMaxZoom() || 19;\n                var isDragging = false;\n\n                function updateSlider() {{\n                    var pct = (mapRef.getZoom() - minZ) / (maxZ - minZ);\n                    pct = Math.max(0, Math.min(1, pct));\n                    thumb.style.bottom = (pct * 100) + '%';\n                    fill.style.height = (pct * 100) + '%';\n                }}\n\n                function zoomFromY(e) {{\n                    var rect = track.getBoundingClientRect();\n                    var y = (e.touches && e.touches[0]) ? e.touches[0].clientY : e.clientY;\n                    var pct = 1 - ((y - rect.top) / rect.height);\n                    pct = Math.max(0, Math.min(1, pct));\n                    mapRef.setZoom(minZ + pct * (maxZ - minZ));\n                }}\n\n                btnPlus.onclick = function() {{ mapRef.zoomIn(0.5); }};\n                btnMinus.onclick = function() {{ mapRef.zoomOut(0.5); }};\n\n                track.addEventListener('mousedown', function(e) {{ isDragging = true; zoomFromY(e); }});\n                document.addEventListener('mousemove', function(e) {{ if (isDragging) zoomFromY(e); }});\n                document.addEventListener('mouseup', function() {{ isDragging = false; }});\n\n                track.addEventListener('touchstart', function(e) {{ isDragging = true; zoomFromY(e); e.preventDefault(); }}, {{ passive: false }});\n                document.addEventListener('touchmove', function(e) {{ if (isDragging) zoomFromY(e); }});\n                document.addEventListener('touchend', function() {{ isDragging = false; }});\n\n                mapRef.on('zoomend zoom', updateSlider);\n                setTimeout(updateSlider, 100);\n\n                return container;\n            }}\n        }});\n        map.addControl(new ZoomSlider());\n\n        // ==================== INICIALIZAÇÃO ====================\n        renderSectors();\n        renderSegments();\n        populateFilters();\n        updateDynamicStats();\n\n        setTimeout(positionLeftPanel, 300);\n\n        console.log('🛑 Mapa de Paradas Leaflet inicializado!');\n        console.log('Segmentos:', window.SEGMENTS.features.length);\n        console.log('Setores:', window.SECTORS.features.length);\n    }})();\n    </script>\n    "

def export_stops_map_html(contract_name, segments_map, sectors, date_from, date_to, overall_stop_ratio=None, day_and_night_mode=False, secure_mode=True):
    """
    Exporta mapa HTML interativo de paradas críticas.

    Args:
        contract_name: Nome do contrato
        segments_map: Segmentos com dados de paradas
        sectors: Setores filtrados por agenda
        date_from: Data inicial
        date_to: Data final
        overall_stop_ratio: Ratio médio (0-1)
        day_and_night_mode: Se é modo agregado
        secure_mode: Se True, criptografa dados no HTML

    Returns:
        Path do arquivo salvo ou None
    """
    def _img_b64(path):
        try:
            base = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
            data = Path(os.path.join(base, path)).read_bytes()
            return base64.b64encode(data).decode('utf-8')
        except Exception:
            return ''

    logo_main = _img_b64('logo_DIE.png')
    logo_1 = _img_b64('logo_locar.png')
    simple_name, display_name = simplify_contract_name(contract_name)
    seg, sec = _prepare_stops_map_data(segments_map, sectors)
    if seg.empty:
        logger.warning(f"[WARN] Nenhum segmento para stops map de {contract_name}")
        return None
    active_days = {d.weekday() for d in pd.date_range(date_from, date_to, freq='D')}
    segments_geojson = _segments_to_geojson_stops(seg, date_from, date_to)
    sectors_geojson = _sectors_to_geojson(sec, active_days)
    if secure_mode:
        vehicle_data = {}
        for idx_num, (idx, row) in enumerate(seg.iterrows()):
            vd = row.get('vehicle_details')
            if isinstance(vd, list) and vd:
                vehicle_data[str(idx_num)] = vd
            continue
    else:
        details_b64 = _compress_vehicle_details(seg)
    filter_options = _build_stops_filters(seg)
    history_df = load_stop_history(contract_name, day_and_night_mode)
    history_data = _prepare_stops_history(history_df, date_from, date_to, overall_stop_ratio)
    bounds = seg.total_bounds
    center_lat = ((bounds[1] + bounds[3]) / 2)
    center_lon = ((bounds[0] + bounds[2]) / 2)
    date_str = f"do dia {date_from.strftime('%d/%m/%Y')}" if date_from == date_to else f"de {date_from.strftime('%d/%m/%Y')} à {date_to.strftime('%d/%m/%Y')}"
    title = f"Mapa de Paradas Críticas — {display_name} {date_str}"
    legend_html = """<div id="legend"><h4>🛑 Intensidade de Paradas</h4>
        <div class="legend-item"><div class="legend-color" style="background:#4b0000"></div><span>80-100% Muito crítico</span></div>
        <div class="legend-item"><div class="legend-color" style="background:#ff0000"></div><span>60-80% Crítico</span></div>
        <div class="legend-item"><div class="legend-color" style="background:#ffa500"></div><span>40-60% Lentidão</span></div>
        <div class="legend-item"><div class="legend-color" style="background:#ffff00"></div><span>20-40% Leve</span></div>
        <div class="legend-item"><div class="legend-color" style="background:#00ff00"></div><span>0-20% Fluido</span></div>
    </div>"""
    geocoder_html = """
        <div id="geocoder-container">
            <button id="geocoder-toggle" title="Buscar endereço">
                <svg id="geocoder-icon-search" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                </svg>
            </button>
            <div id="geocoder-input-wrapper">
                <input type="text" id="geocoder-input" placeholder="Buscar endereço..." autocomplete="off">
            </div>
            <div id="geocoder-results"></div>
        </div>
    """
    leaflet_map_html = f"\n        {leaflet_css_inline()}\n        <div id=\"leafletMap\"></div>\n        <div id=\"map-title\">{title}</div>\n        {legend_html}\n        {geocoder_html}\n    "
    if secure_mode:
        scripts_html = build_secure_stops_scripts(segments_geojson=segments_geojson, sectors_geojson=sectors_geojson, filter_options=filter_options, history_data=history_data, overall_value=history_data.get('overall'), center_lat=center_lat, center_lon=center_lon, vehicle_data=vehicle_data, get_stops_scripts_fn=_get_stops_leaflet_scripts)
    else:
        def _sanitize(obj):
            if isinstance(obj, str):
                try:
                    obj.encode('utf-8')
                    return obj
                except (UnicodeEncodeError, UnicodeDecodeError):
                    return obj.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_sanitize(v) for v in obj]
            return obj

        try:
            segments_json = json.dumps(segments_geojson, ensure_ascii=False)
        except (UnicodeEncodeError, UnicodeDecodeError):
            segments_json = json.dumps(_sanitize(segments_geojson), ensure_ascii=False)
        sectors_json = json.dumps(sectors_geojson, ensure_ascii=False)
        filters_json = json.dumps(filter_options, ensure_ascii=False)
        history_json = json.dumps(history_data, ensure_ascii=False)
        overall_json = json.dumps(history_data.get('overall'))
        scripts_html = _get_stops_leaflet_scripts(segments_json, sectors_json, filters_json, history_json, overall_json, center_lat, center_lon, details_b64)
    out_file = f"Paradas_{simple_name}_de-{date_from.strftime('%d.%m.%Y')}-a-{date_to.strftime('%d.%m.%Y')}.html"
    html = f"\n<!DOCTYPE html>\n<html lang='pt-BR'>\n<head>\n    <meta charset='utf-8' />\n    <meta name='viewport' content='width=device-width,initial-scale=1'>\n    <title>{title}</title>\n    {get_styles()}\n    {_get_stops_leaflet_styles()}\n</head>\n<body class='sidebar-open legend-large'>\n    <div class=\"main-wrapper\">\n        <div class=\"plot-container\">\n            <div id=\"mapPlot\" class=\"plot-wrapper\">\n                {leaflet_map_html}\n            </div>\n        </div>\n    </div>\n\n    {_get_stops_sidebar_template()}\n\n    <div id=\"detail-sidebar\">\n        <div class=\"detail-header\">\n            <h3 id=\"detail-title\">Detalhes de Paradas</h3>\n            <button class=\"detail-close-btn\" id=\"detail-close-btn\" title=\"Fechar\">&times;</button>\n        </div>\n        <div class=\"detail-body\" id=\"detail-body\">\n            <div class=\"detail-no-data\">Clique em um segmento para ver detalhes de paradas.</div>\n        </div>\n    </div>\n\n    <div id='logo_container'>\n        <div class=\"logo-bar\">\n            <div class=\"logo-left\">\n                <img src=\"data:image/webp;base64,{logo_main}\">\n            </div>\n            <div class=\"logo-right\">\n                <img src=\"data:image/webp;base64,{logo_1}\">\n            </div>\n        </div>\n    </div>\n\n    {scripts_html}\n</body>\n</html>\n    "
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
    logger.info(f"[{contract_name}] 🛑 Mapa de paradas salvo: {out_path}")
    return out_path

def export_stops_mobile_map_html(contract_name, segments_map, sectors, date_from, date_to, overall_stop_ratio=None, day_and_night_mode=False, secure_mode=True):
    """
    Exporta mapa HTML de paradas otimizado para mobile.

    Diferenças do desktop:
    - Geometrias simplificadas para performance
    - Sidebar no bottom (touch-friendly)
    - Sem detail sidebar (popup apenas)
    - Sem compressão de vehicle_details (leve)
    - Controles maiores para toque
    """
    from project_heatmaps.plotting_mobile_enhanced import simplify_geometries_for_mobile
    logger.info(f"[MOBILE] Gerando mapa mobile de paradas para {contract_name}...")
    simple_name, display_name = simplify_contract_name(contract_name)
    active_days = {d.weekday() for d in pd.date_range(date_from, date_to, freq='D')}
    def _is_active(days_set):
        if (not isinstance(days_set, set) or not days_set):
            return False
        return len((days_set & active_days)) > 0

    sectors_filtered = sectors[sectors['weekday_set'].apply(_is_active)].copy()
    seg_prepared, _ = _prepare_stops_map_data(segments_map, sectors)
    seg, sec = simplify_geometries_for_mobile(seg_prepared, sectors_filtered)
    if seg.empty:
        logger.warning(f"[MOBILE] Sem segmentos após simplificação para {contract_name}")
        return None
    segments_geojson = _segments_to_geojson_stops(seg, date_from, date_to)
    sectors_geojson = _sectors_to_geojson(sec, active_days)
    filter_options = _build_stops_filters(seg)
    history_df = load_stop_history(contract_name, day_and_night_mode)
    history_data = _prepare_stops_history(history_df, date_from, date_to, overall_stop_ratio)
    date_str = f"{date_from.strftime('%d/%m/%Y')}" if date_from == date_to else f"{date_from.strftime('%d/%m/%Y')} — {date_to.strftime('%d/%m/%Y')}"
    overall_pct = round((overall_stop_ratio * 100), 1) if overall_stop_ratio is not None else None
    def _sanitize(obj):
        if isinstance(obj, str):
            try:
                obj.encode('utf-8')
                return obj
            except (UnicodeEncodeError, UnicodeDecodeError):
                return obj.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(v) for v in obj]
        return obj

    try:
        seg_json = json.dumps(segments_geojson, ensure_ascii=False)
    except (UnicodeEncodeError, UnicodeDecodeError):
        seg_json = json.dumps(_sanitize(segments_geojson), ensure_ascii=False)
    sec_json = json.dumps(sectors_geojson, ensure_ascii=False)
    filt_json = json.dumps(filter_options, ensure_ascii=False)
    hist_json = json.dumps(history_data, ensure_ascii=False)
    overall_pct_json = json.dumps(overall_pct)
    out_file = f"Paradas_{simple_name}_mobile_de-{date_from.strftime('%d.%m.%Y')}-a-{date_to.strftime('%d.%m.%Y')}.html"
    out_path = PATHS.heatmaps_dir / out_file
    def _montar(orcamento_mb):
        html_content = _build_stops_mobile_html(seg_json=seg_json, sec_json=sec_json, filt_json=filt_json, hist_json=hist_json, overall_pct_json=overall_pct_json, display_name=display_name, date_str=date_str, basemap_orcamento_mb=orcamento_mb)
        if secure_mode:
            secure_scripts = build_secure_stops_mobile_scripts(segments_geojson=segments_geojson, sectors_geojson=sectors_geojson, filter_options=filter_options, history_data=history_data, overall_pct=overall_pct, build_stops_mobile_html_fn=functools.partial(_build_stops_mobile_html, basemap_orcamento_mb=orcamento_mb))
            marker = '<!-- Leaflet JS -->'
            marker_pos = html_content.find(marker)
            if marker_pos == -1:
                logger.warning('[MOBILE] WARN: marker Leaflet não encontrado, usando legacy')
            else:
                html_shell = html_content[:marker_pos]
                html_content = (((html_shell + """
    """) + secure_scripts) + """
</body>
</html>""")
        if secure_mode:
            html_content = inject_ai_poison(html_content, contract_name=contract_name)
        if secure_mode:
            _wm = get_html_watermark_overlay()
            if '</body>' in html_content.lower():
                _idx = html_content.lower().rfind('</body>')
                html_content = (((html_content[:_idx] + _wm) + """
""") + html_content[_idx:])
            else:
                html_content += _wm
        return html_content

    html_content = caber_no_teto(_montar, BASEMAP_MOBILE_TETO_ARQUIVO_MB, rotulo=out_file)
    with open(out_path, 'w', encoding='utf-8', errors='xmlcharrefreplace', newline='') as f:
        f.write(html_content)
    logger.info(f"[MOBILE] 🛑 Mapa mobile de paradas salvo: {out_path}")
    return out_path

def _build_stops_mobile_html(seg_json, sec_json, filt_json, hist_json, overall_pct_json, display_name, date_str, basemap_geojson=None, basemap_orcamento_mb=None):
    """Gera HTML completo mobile para stops_mode."""
    fontes_basemap = basemap_geojson if basemap_geojson is not None else sec_json
    basemap_block = bloco_camada_base(*fontes_basemap, orcamento_mb=basemap_orcamento_mb)
    return f"<!DOCTYPE html>\n<html lang=\"pt-BR\">\n<head>\n    <meta charset=\"utf-8\">\n    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no\">\n    <title>Paradas Mobile — {display_name}</title>\n    {leaflet_css_inline()}\n    <style>\n        * {{ margin:0; padding:0; box-sizing:border-box; }}\n        html, body {{ width:100%; height:100%; overflow:hidden; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}\n        #map {{ width:100%; height:100%; }}\n\n        /* Sidebar mobile — bottom sheet */\n        #sidebar {{\n            position:fixed; bottom:0; left:0; right:0;\n            background:#fff; border-top:2px solid #ddd;\n            box-shadow:0 -2px 10px rgba(0,0,0,.1);\n            transition:transform .3s ease; z-index:1000;\n            max-height:70vh; overflow-y:auto;\n        }}\n        #sidebar.collapsed {{ transform:translateY(calc(100% - 50px)); }}\n        #sidebar-handle {{\n            height:50px; background:#f8f8f8; border-bottom:1px solid #ddd;\n            display:flex; align-items:center; justify-content:center;\n            cursor:pointer; user-select:none; font-weight:bold; color:#666;\n            -webkit-tap-highlight-color:transparent;\n        }}\n        #sidebar-handle::before {{ content:'▲'; font-size:20px; transition:transform .3s ease; }}\n        #sidebar.collapsed #sidebar-handle::before {{ transform:rotate(180deg); }}\n        #sidebar-content {{ padding:15px; }}\n\n        .filter-group {{ margin-bottom:16px; }}\n        .filter-group label {{ display:block; margin-bottom:6px; font-weight:600; font-size:14px; color:#333; }}\n        .filter-group select {{ width:100%; padding:10px; font-size:16px; border:1px solid #ddd; border-radius:5px; background:#fff; }}\n\n        .stats-card {{\n            background:linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);\n            color:#fff; padding:15px; border-radius:8px; margin-bottom:15px;\n        }}\n        .stats-card h3 {{ font-size:14px; margin-bottom:8px; opacity:.9; }}\n        .stats-card .value {{ font-size:32px; font-weight:bold; }}\n\n        #historyChart {{\n            width:100%; min-height:150px; margin-top:15px;\n            padding:10px; background:#f9f9f9; border-radius:8px; border:1px solid #e0e0e0; overflow:hidden;\n        }}\n\n        /* Legenda — posicionada na lateral esquerda via JS */\n        #legend {{\n            position:absolute; left:10px;\n            background:#fff; padding:10px; border-radius:8px;\n            box-shadow:0 2px 8px rgba(0,0,0,.2); font-size:12px; z-index:999; max-width:160px;\n        }}\n        #legend h4 {{ margin-bottom:8px; font-size:13px; }}\n        .legend-item {{ display:flex; align-items:center; margin-bottom:5px; }}\n        .legend-color {{ width:20px; height:3px; margin-right:8px; }}\n        .legend-label {{ font-size:11px; }}\n\n        /* Loading */\n        #loading {{\n            position:fixed; top:0; left:0; right:0; bottom:0;\n            background:rgba(255,255,255,.9); display:flex; align-items:center; justify-content:center;\n            z-index:9999; font-size:18px; font-weight:bold;\n        }}\n        #loading.hidden {{ display:none; }}\n\n        /* Touch-friendly zoom — Zoom Slider */\n        .leaflet-control-zoom {{ display:none !important; }}\n\n        .zoom-slider-container {{\n            background:rgba(255,255,255,.95); border-radius:8px;\n            box-shadow:0 2px 8px rgba(0,0,0,.2); padding:6px;\n            display:flex; flex-direction:column; align-items:center; gap:4px;\n        }}\n        .zoom-slider-btn {{\n            width:40px; height:40px; border:none; background:transparent;\n            font-size:22px; font-weight:bold; cursor:pointer; color:#333;\n            border-radius:4px; display:flex; align-items:center; justify-content:center;\n            user-select:none; -webkit-user-select:none; -webkit-tap-highlight-color:transparent;\n        }}\n        .zoom-slider-btn:active {{ background:#ddd; }}\n        .zoom-slider-track {{\n            width:10px; height:140px; background:#ddd; border-radius:5px;\n            position:relative; cursor:pointer;\n        }}\n        .zoom-slider-fill {{\n            position:absolute; bottom:0; width:100%; background:#4A90D9;\n            border-radius:5px; pointer-events:none; transition:height 0.15s ease;\n        }}\n        .zoom-slider-thumb {{\n            position:absolute; left:50%; width:22px; height:22px;\n            background:#fff; border:2px solid #4A90D9; border-radius:50%;\n            transform:translate(-50%, 50%); pointer-events:none;\n            box-shadow:0 1px 4px rgba(0,0,0,.2); transition:bottom 0.15s ease;\n        }}\n\n        /* Geocoder mobile */\n        #geocoder-container {{\n            position:absolute; left:10px; z-index:2000; display:flex; align-items:center; gap:0;\n        }}\n        #geocoder-toggle {{\n            width:44px; height:44px; background:rgba(255,255,255,.95); border:none; border-radius:8px;\n            box-shadow:0 2px 6px rgba(0,0,0,.2); cursor:pointer; display:flex; align-items:center; justify-content:center;\n            -webkit-tap-highlight-color:transparent;\n        }}\n        #geocoder-toggle:active {{ background:#f0f0f0; }}\n        #geocoder-toggle svg {{ width:22px; height:22px; color:#555; }}\n        #geocoder-input-wrapper {{\n            display:none; background:rgba(255,255,255,.95); border-radius:0 8px 8px 0;\n            box-shadow:0 2px 6px rgba(0,0,0,.2); margin-left:-4px; overflow:hidden;\n        }}\n        #geocoder-container.expanded #geocoder-input-wrapper {{ display:block; }}\n        #geocoder-container.expanded #geocoder-toggle {{ border-radius:8px 0 0 8px; }}\n        #geocoder-input {{\n            width:calc(100vw - 200px); max-width:220px; padding:12px 14px; font-size:16px;\n            border:none; outline:none; background:transparent; -webkit-appearance:none;\n        }}\n        #geocoder-input::placeholder {{ color:#999; }}\n        #geocoder-results {{\n            position:absolute; top:100%; left:0; width:calc(100vw - 80px); max-width:300px;\n            background:#fff; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,.2);\n            max-height:50vh; overflow-y:auto; display:none; z-index:2001; margin-top:4px;\n            -webkit-overflow-scrolling:touch;\n        }}\n        #geocoder-results.active {{ display:block; }}\n        #geocoder-results .result-item {{\n            padding:14px 16px; cursor:pointer; border-bottom:1px solid #eee; font-size:14px; color:#333; line-height:1.4;\n        }}\n        #geocoder-results .result-item:active {{ background:#e0e0e0; }}\n        #geocoder-results .result-item:last-child {{ border-bottom:none; }}\n        #geocoder-results .no-results {{ padding:14px 16px; color:#999; font-style:italic; text-align:center; }}\n    </style>\n</head>\n<body>\n    <div id=\"loading\">Carregando mapa de paradas...</div>\n    <div id=\"map\"></div>\n\n    <div id=\"legend\">\n        <h4>🛑 Intensidade</h4>\n        <div class=\"legend-item\"><div class=\"legend-color\" style=\"background:#4b0000\"></div><div class=\"legend-label\">80-100% Crítico</div></div>\n        <div class=\"legend-item\"><div class=\"legend-color\" style=\"background:#ff0000\"></div><div class=\"legend-label\">60-80%</div></div>\n        <div class=\"legend-item\"><div class=\"legend-color\" style=\"background:#ffa500\"></div><div class=\"legend-label\">40-60%</div></div>\n        <div class=\"legend-item\"><div class=\"legend-color\" style=\"background:#ffff00\"></div><div class=\"legend-label\">20-40%</div></div>\n        <div class=\"legend-item\"><div class=\"legend-color\" style=\"background:#00ff00\"></div><div class=\"legend-label\">0-20% Fluido</div></div>\n    </div>\n\n    <div id=\"geocoder-container\">\n        <button id=\"geocoder-toggle\" title=\"Buscar endereço\">\n            <svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\">\n                <circle cx=\"11\" cy=\"11\" r=\"8\"></circle><line x1=\"21\" y1=\"21\" x2=\"16.65\" y2=\"16.65\"></line>\n            </svg>\n        </button>\n        <div id=\"geocoder-input-wrapper\">\n            <input type=\"text\" id=\"geocoder-input\" placeholder=\"Buscar endereço...\" autocomplete=\"off\">\n        </div>\n        <div id=\"geocoder-results\"></div>\n    </div>\n\n    <div id=\"sidebar\" class=\"collapsed\">\n        <div id=\"sidebar-handle\">Paradas e Filtros</div>\n        <div id=\"sidebar-content\">\n            <div class=\"stats-card\">\n                <h3>Intensidade Geral de Paradas</h3>\n                <div class=\"value\" id=\"overall-stat\">—</div>\n            </div>\n            <div class=\"filter-group\">\n                <label for=\"ratioFilter\">Por intensidade:</label>\n                <select id=\"ratioFilter\"></select>\n            </div>\n            <div class=\"filter-group\">\n                <label for=\"codeFilter\">Por código:</label>\n                <select id=\"codeFilter\"></select>\n            </div>\n            <h3 style=\"margin:20px 0 10px 0;\">Histórico</h3>\n            <div id=\"historyChart\"></div>\n        </div>\n    </div>\n\n    <!-- Leaflet JS -->\n    {leaflet_js_inline()}\n    <script src=\"https://cdn.plot.ly/plotly-2.27.0.min.js\"></script>\n\n    <script>\n        window.SEGMENTS_DATA = {seg_json};\n        window.SECTORS_DATA = {sec_json};\n        window.FILTER_CONFIG = {filt_json};\n        window.STOPS_HISTORY = {hist_json};\n        window.STOPS_OVERALL_PCT = {overall_pct_json};\n    </script>\n\n    <script>\n    (function() {{\n        'use strict';\n\n        function fmtTime(sec) {{\n            if (!sec || sec <= 0) return '0s';\n            var h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = Math.floor(sec%60);\n            if (h > 0) return h+'h '+m+'min';\n            if (m > 0) return m+'min '+s+'s';\n            return s+'s';\n        }}\n\n        // ==================== MAPA ====================\n        var map = L.map('map', {{ zoomControl:false, attributionControl:false, zoomSnap:0.25, zoomDelta:0.5, wheelPxPerZoomLevel:120 }});\n        // Mapa de fundo: fonte única em config.py (bloco BASEMAP) — o provedor\n        // não é nomeado aqui de propósito, já trocou 2x em 2 dias;\n        // segunda opção registrada lá: VersaTiles Colorful.\n        {basemap_block}\n\n        // ==================== GEOCODER ====================\n        var searchMarker = null, searchTimeout = null, isExpanded = false;\n        var geocoderContainer = document.getElementById('geocoder-container');\n        var geocoderToggle = document.getElementById('geocoder-toggle');\n        var geocoderInput = document.getElementById('geocoder-input');\n        var geocoderResults = document.getElementById('geocoder-results');\n\n        function positionLeftPanel() {{\n            var slider = document.querySelector('.zoom-slider-container');\n            var legend = document.getElementById('legend');\n            var gap = 10;\n            if (slider && geocoderContainer) {{\n                var sliderRect = slider.getBoundingClientRect();\n                geocoderContainer.style.top = (sliderRect.bottom + gap) + 'px';\n            }}\n            if (geocoderContainer && legend) {{\n                var geoRect = geocoderContainer.getBoundingClientRect();\n                legend.style.top = (geoRect.bottom + gap) + 'px';\n            }}\n        }}\n        setTimeout(positionLeftPanel, 200);\n        window.addEventListener('resize', positionLeftPanel);\n\n        if (window.MutationObserver) {{\n            var panelObserver = new MutationObserver(function() {{\n                setTimeout(positionLeftPanel, 50);\n            }});\n            if (geocoderContainer) {{\n                panelObserver.observe(geocoderContainer, {{ attributes: true, subtree: true }});\n            }}\n        }}\n\n        if (geocoderToggle) {{\n            geocoderToggle.addEventListener('click', function(e) {{\n                e.stopPropagation();\n                isExpanded = !isExpanded;\n                if (isExpanded) {{\n                    geocoderContainer.classList.add('expanded');\n                    setTimeout(function(){{ geocoderInput.focus(); }}, 100);\n                }} else {{\n                    geocoderContainer.classList.remove('expanded');\n                    geocoderResults.classList.remove('active');\n                    geocoderInput.value = '';\n                }}\n            }});\n        }}\n        if (geocoderInput) {{\n            geocoderInput.addEventListener('input', function() {{\n                clearTimeout(searchTimeout);\n                var q = this.value.trim();\n                searchTimeout = setTimeout(function() {{\n                    if (q.length < 3) {{ geocoderResults.classList.remove('active'); return; }}\n                    fetch('https://nominatim.openstreetmap.org/search?format=json&q='+encodeURIComponent(q)+'&countrycodes=br&addressdetails=1&limit=5&accept-language=pt-BR')\n                    .then(function(r){{ return r.json(); }})\n                    .then(function(data) {{\n                        geocoderResults.innerHTML = '';\n                        if (!data.length) {{ geocoderResults.innerHTML = '<div class=\"no-results\">Nenhum resultado</div>'; }}\n                        else {{ data.forEach(function(item) {{\n                            var div = document.createElement('div');\n                            div.className = 'result-item';\n                            div.textContent = item.display_name;\n                            div.onclick = function() {{\n                                if (searchMarker) map.removeLayer(searchMarker);\n                                searchMarker = L.marker([parseFloat(item.lat), parseFloat(item.lon)]).addTo(map);\n                                searchMarker.bindPopup(item.display_name).openPopup();\n                                searchMarker.on('click', function(){{ map.removeLayer(searchMarker); searchMarker=null; }});\n                                map.setView([parseFloat(item.lat), parseFloat(item.lon)], 17);\n                                isExpanded = false;\n                                geocoderContainer.classList.remove('expanded');\n                                geocoderResults.classList.remove('active');\n                                geocoderInput.value = '';\n                                geocoderInput.blur();\n                            }};\n                            geocoderResults.appendChild(div);\n                        }}); }}\n                        geocoderResults.classList.add('active');\n                    }}).catch(function(){{}});\n                }}, 400);\n            }});\n            geocoderInput.addEventListener('keydown', function(e) {{\n                if (e.key === 'Escape') {{ isExpanded=false; geocoderContainer.classList.remove('expanded'); geocoderResults.classList.remove('active'); }}\n            }});\n            document.addEventListener('click', function(e) {{\n                if (!e.target.closest('#geocoder-container')) {{\n                    if (isExpanded) {{ isExpanded=false; geocoderContainer.classList.remove('expanded'); }}\n                    geocoderResults.classList.remove('active');\n                }}\n            }});\n            document.addEventListener('touchstart', function(e) {{\n                if (!e.target.closest('#geocoder-container')) {{\n                    if (isExpanded) {{ isExpanded=false; geocoderContainer.classList.remove('expanded'); }}\n                    geocoderResults.classList.remove('active');\n                }}\n            }});\n        }}\n\n        // ==================== SETORES ====================\n        var sectorsLayer = L.layerGroup().addTo(map);\n        var segmentsLayer = L.layerGroup().addTo(map);\n        var allSegLayers = [];\n\n        function renderSectors() {{\n            sectorsLayer.clearLayers();\n            window.SECTORS_DATA.features.forEach(function(f) {{\n                var p = f.properties, g = f.geometry, layer;\n                if (g.type === 'Polygon') {{\n                    layer = L.polygon(g.coordinates[0].map(function(c){{ return [c[1],c[0]]; }}), {{\n                        color:p.color, weight:2, opacity:.8, fillColor:p.color, fillOpacity:.1\n                    }});\n                }} else if (g.type === 'MultiPolygon') {{\n                    layer = L.polygon(g.coordinates.map(function(poly){{ return poly[0].map(function(c){{ return [c[1],c[0]]; }}); }}), {{\n                        color:p.color, weight:2, opacity:.8, fillColor:p.color, fillOpacity:.1\n                    }});\n                }} else {{ return; }}\n                if (layer) {{\n                    layer.bindPopup('<strong>'+p.name+'</strong><br>Tipo: '+p.prefix);\n                    layer.addTo(sectorsLayer);\n                }}\n            }});\n        }}\n\n        // ==================== SEGMENTOS (STOPS) ====================\n        var currentRatioFilter = 'all';\n        var currentCodeFilter = 'all';\n\n        function renderSegments() {{\n            segmentsLayer.clearLayers();\n            allSegLayers = [];\n\n            window.SEGMENTS_DATA.features.forEach(function(f) {{\n                var p = f.properties, g = f.geometry;\n\n                // Filtros\n                if (currentRatioFilter !== 'all') {{\n                    if (currentRatioFilter === 'no_stops') {{\n                        if (!p.flag_no_stops) return;\n                    }} else {{\n                        if (p.category !== currentRatioFilter) return;\n                    }}\n                }}\n                if (currentCodeFilter !== 'all' && p.route_code !== currentCodeFilter) return;\n\n                var coords;\n                if (g.type === 'LineString') {{\n                    coords = [g.coordinates.map(function(c){{ return [c[1],c[0]]; }})];\n                }} else if (g.type === 'MultiLineString') {{\n                    coords = g.coordinates.map(function(line){{ return line.map(function(c){{ return [c[1],c[0]]; }}); }});\n                }} else {{ return; }}\n\n                var popup = '<strong>🛑 '+p.route_code+'</strong><br>'+\n                    '<b>Setor:</b> '+p.sector+'<br>'+\n                    '<b>Intensidade:</b> '+p.stop_pct.toFixed(1)+'%<br>'+\n                    '<b>Paradas:</b> '+p.total_stops+' sessões<br>'+\n                    '<b>Tempo total:</b> '+fmtTime(p.total_time)+'<br>'+\n                    (p.avg_time > 0 ? '<b>Tempo médio:</b> '+fmtTime(p.avg_time)+'<br>' : '')+\n                    (p.peak ? '<b>Pico:</b> '+p.peak : '');\n\n                coords.forEach(function(lc) {{\n                    var layer = L.polyline(lc, {{ color:p.color, weight:3, opacity:.8 }})\n                        .bindPopup(popup)\n                        .addTo(segmentsLayer);\n                    allSegLayers.push(layer);\n                }});\n            }});\n\n            if (allSegLayers.length > 0) {{\n                map.fitBounds(L.featureGroup(allSegLayers).getBounds(), {{ padding:[20,20] }});\n            }}\n        }}\n\n        // ==================== FILTROS ====================\n        function populateFilters() {{\n            var rs = document.getElementById('ratioFilter');\n            var cs = document.getElementById('codeFilter');\n\n            window.FILTER_CONFIG.byRatio.forEach(function(f) {{\n                var o = document.createElement('option'); o.value=f.value; o.textContent=f.label; rs.appendChild(o);\n            }});\n            window.FILTER_CONFIG.byCode.forEach(function(f) {{\n                var o = document.createElement('option'); o.value=f.value; o.textContent=f.label; cs.appendChild(o);\n            }});\n\n            rs.addEventListener('change', function() {{\n                currentRatioFilter = this.value;\n                renderSegments();\n            }});\n            cs.addEventListener('change', function() {{\n                currentCodeFilter = this.value;\n                renderSegments();\n            }});\n        }}\n\n        // ==================== SIDEBAR ====================\n        var sidebar = document.getElementById('sidebar');\n        var sidebarHandle = document.getElementById('sidebar-handle');\n        if (sidebarHandle) {{\n            sidebarHandle.addEventListener('click', function() {{\n                sidebar.classList.toggle('collapsed');\n            }});\n        }}\n\n        // ==================== STATS ====================\n        var overallPct = window.STOPS_OVERALL_PCT;\n        var overallEl = document.getElementById('overall-stat');\n        if (overallEl && typeof overallPct === 'number') {{\n            overallEl.textContent = overallPct.toFixed(1).replace('.',',') + '%';\n        }}\n\n        // ==================== GRÁFICO ====================\n        var hist = window.STOPS_HISTORY || {{ dates:[], pcts:[] }};\n        if (hist.dates && hist.dates.length && window.Plotly) {{\n            var hoverTexts = hist.dates.map(function(d,i) {{\n                var day = d.slice(8,10);\n                var pct = (typeof hist.pcts[i]==='number') ? hist.pcts[i].toFixed(1).replace('.',',')+' %' : '—';\n                return 'Dia '+day+' — '+pct;\n            }});\n            var xRange = (hist.period_from && hist.period_to) ? [hist.period_from, hist.period_to] : undefined;\n\n            Plotly.newPlot('historyChart', [{{\n                x:hist.dates, y:hist.pcts,\n                mode:'lines+markers', fill:'tozeroy',\n                fillcolor:'rgba(255,80,50,0.18)',\n                line:{{ color:'rgb(220,60,30)', width:2 }},\n                marker:{{ size:5 }},\n                text:hoverTexts, hoverinfo:'text', name:'Intensidade'\n            }}], {{\n                height:180, margin:{{ l:28, r:20, t:20, b:32 }}, autosize:true,\n                xaxis:{{ type:'date', tickformat:'%d/%m', tickangle:35, fixedrange:true, range:xRange }},\n                yaxis:{{ range:[0,100], fixedrange:true }},\n                hoverlabel:{{ bgcolor:'rgba(0,0,0,.75)', bordercolor:'#fff', font:{{size:12,color:'#fff'}} }},\n                plot_bgcolor:'rgba(0,0,0,0)', paper_bgcolor:'rgba(0,0,0,0)'\n            }}, {{ displayModeBar:false, responsive:true }});\n        }}\n\n        // ==================== ZOOM SLIDER ====================\n        var ZoomSlider = L.Control.extend({{\n            options: {{ position: 'topleft' }},\n            onAdd: function(mapRef) {{\n                var container = L.DomUtil.create('div', 'zoom-slider-container');\n                L.DomEvent.disableClickPropagation(container);\n                L.DomEvent.disableScrollPropagation(container);\n\n                var btnPlus = L.DomUtil.create('button', 'zoom-slider-btn', container);\n                btnPlus.innerHTML = '+'; btnPlus.title = 'Mais zoom';\n\n                var track = L.DomUtil.create('div', 'zoom-slider-track', container);\n                var fill = L.DomUtil.create('div', 'zoom-slider-fill', track);\n                var thumb = L.DomUtil.create('div', 'zoom-slider-thumb', track);\n\n                var btnMinus = L.DomUtil.create('button', 'zoom-slider-btn', container);\n                btnMinus.innerHTML = '−'; btnMinus.title = 'Menos zoom';\n\n                var minZ = mapRef.getMinZoom() || 3;\n                var maxZ = mapRef.getMaxZoom() || 19;\n                var isDragging = false;\n\n                function updateSlider() {{\n                    var pct = (mapRef.getZoom() - minZ) / (maxZ - minZ);\n                    pct = Math.max(0, Math.min(1, pct));\n                    thumb.style.bottom = (pct * 100) + '%';\n                    fill.style.height = (pct * 100) + '%';\n                }}\n\n                function zoomFromY(e) {{\n                    var rect = track.getBoundingClientRect();\n                    var y = (e.touches && e.touches[0]) ? e.touches[0].clientY : e.clientY;\n                    var pct = 1 - ((y - rect.top) / rect.height);\n                    pct = Math.max(0, Math.min(1, pct));\n                    mapRef.setZoom(minZ + pct * (maxZ - minZ));\n                }}\n\n                btnPlus.onclick = function() {{ mapRef.zoomIn(0.5); }};\n                btnMinus.onclick = function() {{ mapRef.zoomOut(0.5); }};\n\n                track.addEventListener('mousedown', function(e) {{ isDragging = true; zoomFromY(e); }});\n                document.addEventListener('mousemove', function(e) {{ if (isDragging) zoomFromY(e); }});\n                document.addEventListener('mouseup', function() {{ isDragging = false; }});\n\n                track.addEventListener('touchstart', function(e) {{ isDragging = true; zoomFromY(e); e.preventDefault(); }}, {{ passive: false }});\n                document.addEventListener('touchmove', function(e) {{ if (isDragging) zoomFromY(e); }});\n                document.addEventListener('touchend', function() {{ isDragging = false; }});\n\n                mapRef.on('zoomend zoom', updateSlider);\n                setTimeout(updateSlider, 100);\n\n                return container;\n            }}\n        }});\n        map.addControl(new ZoomSlider());\n\n        // ==================== INIT ====================\n        renderSectors();\n        renderSegments();\n        populateFilters();\n\n        setTimeout(positionLeftPanel, 300);\n\n        document.getElementById('loading').classList.add('hidden');\n        console.log('🛑 Mobile stops map OK — segs:'+window.SEGMENTS_DATA.features.length+', secs:'+window.SECTORS_DATA.features.length);\n    }})();\n    </script>\n</body>\n</html>"

