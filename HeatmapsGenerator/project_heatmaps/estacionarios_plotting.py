"""
Renderização do HTML do mapa de calor de Estacionários.

Saída: um arquivo HTML independente por (contrato, tipo de estacionário),
ex: HEATMAPS/Estacionarios_Caixas_Brooks_Cabo_de-...-a-....html.

O layout reusa estilos de plotting.py (mapa Leaflet + legenda + geocoder) mas
não inclui setores, rotas nem sidebar — apenas os pontos coloridos pela
intensidade de parada (mesmo cálculo dos pontos viciados).
"""

import base64
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional
import geopandas as gpd
from project_heatmaps.config import PATHS, simplify_contract_name
from project_heatmaps.basemap_cache import bloco_camada_base
from project_heatmaps.web_assets import leaflet_css_inline, leaflet_js_inline
from project_heatmaps.html_templates import get_styles
from project_heatmaps.plotting import get_leaflet_styles
from project_heatmaps.pontos_viciados import pontos_to_geojson
logger = logging.getLogger(__name__)

def _img_b64(path):
    try:
        base = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
        data = Path(os.path.join(base, path)).read_bytes()
        return base64.b64encode(data).decode('utf-8')
    except Exception:
        return ''

def _geocoder_html():
    return """
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

_LEGEND_HTML = """<div id="legend"><h4>📦 Tempo médio de parada</h4>
    <div class="legend-item"><div class="legend-color" style="background:#c0392b"></div><span>Mais alto</span></div>
    <div class="legend-item"><div class="legend-color" style="background:#e67e22"></div><span>Alto</span></div>
    <div class="legend-item"><div class="legend-color" style="background:#f1c40f"></div><span>Médio</span></div>
    <div class="legend-item"><div class="legend-color" style="background:#27ae60"></div><span>Baixo</span></div>
    <div class="legend-item"><div class="legend-color" style="background:#888888"></div><span>Sem paradas no período</span></div>
</div>"""

def _leaflet_script(points_json, center_lat, center_lon):
    """Retorna o <script> que renderiza os pontos no Leaflet."""
    basemap_block = bloco_camada_base(points_json)
    return f"\n    {leaflet_js_inline()}\n\n    <script>\n    (function() {{\n        'use strict';\n\n        var POINTS = {points_json};\n\n        var map = L.map('leafletMap', {{\n            center: [{center_lat}, {center_lon}],\n            zoom: 13,\n            zoomControl: false,\n            preferCanvas: true,\n            zoomSnap: 0.25,\n            zoomDelta: 0.5,\n            wheelPxPerZoomLevel: 120\n        }});\n        // Mapa de fundo: fonte única em config.py (bloco BASEMAP) — o provedor\n        // não é nomeado aqui de propósito, já trocou 2x em 2 dias;\n        // segunda opção registrada lá: VersaTiles Colorful.\n        {basemap_block}\n\n        function darkenColor(hex, pct) {{\n            hex = hex.replace('#','');\n            var r = Math.floor(parseInt(hex.substr(0,2),16)*(1-pct/100));\n            var g = Math.floor(parseInt(hex.substr(2,2),16)*(1-pct/100));\n            var b = Math.floor(parseInt(hex.substr(4,2),16)*(1-pct/100));\n            return '#' + [r,g,b].map(function(x){{ return x.toString(16).padStart(2,'0'); }}).join('');\n        }}\n        function getContrastColor(hex) {{\n            hex = hex.replace('#','');\n            var r = parseInt(hex.substr(0,2),16);\n            var g = parseInt(hex.substr(2,2),16);\n            var b = parseInt(hex.substr(4,2),16);\n            return (0.299*r + 0.587*g + 0.114*b)/255 > 0.5 ? '#000' : '#fff';\n        }}\n        function fmtSec(sec) {{\n            if (sec === null || sec === undefined || isNaN(sec)) return '—';\n            var s = Math.round(sec);\n            var m = Math.floor(s / 60);\n            var r = s % 60;\n            if (m > 0) return m + 'min ' + (r > 0 ? r + 's' : '');\n            return r + 's';\n        }}\n        function escapeHtml(str) {{\n            var d = document.createElement('div');\n            d.appendChild(document.createTextNode(str || ''));\n            return d.innerHTML;\n        }}\n\n        var pontosLayer = L.geoJSON(POINTS, {{\n            pointToLayer: function(feature, latlng) {{\n                var p = feature.properties;\n                var fill = p.color || '#888888';\n                return L.circleMarker(latlng, {{\n                    radius:      11,\n                    fillColor:   fill,\n                    color:       darkenColor(fill, 35),\n                    weight:      2.5,\n                    opacity:     1,\n                    fillOpacity: 0.88\n                }});\n            }},\n            onEachFeature: function(feature, layer) {{\n                var p = feature.properties;\n                var fill = p.color || '#888888';\n                var tc = getContrastColor(fill);\n                var bc = darkenColor(fill, 35);\n\n                layer.bindTooltip(\n                    '<div class=\"tooltip-inner\" style=\"background:'+bc+';color:#fff;\">'\n                    + escapeHtml(p.name) + '</div>',\n                    {{ permanent: false, direction: 'top', className: 'colored-tooltip' }}\n                );\n\n                var stopLine = (p.mean_stop_sec !== null && p.mean_stop_sec !== undefined)\n                    ? '<b>Tempo médio de parada:</b> ' + fmtSec(p.mean_stop_sec)\n                      + ' (' + p.total_stops + ' registro' + (p.total_stops !== 1 ? 's' : '') + ')<br>'\n                    : '<b>Sem paradas registradas no período</b><br>';\n\n                var vehiclesLine = '';\n                if (p.vehicles && p.vehicles.length > 0) {{\n                    vehiclesLine = '<b>Veículos:</b> ' + p.vehicles.join(', ') + '<br>';\n                }}\n\n                var dvRows = '';\n                if (p.date_vehicle_list && p.date_vehicle_list.length > 0) {{\n                    dvRows = '<table style=\"width:100%;border-collapse:collapse;font-size:11px;margin-top:6px;\">'\n                        + '<thead><tr style=\"background:#f5f5f5;\">'\n                        + '<th style=\"padding:3px 5px;text-align:left;border-bottom:1px solid #ddd;\">Data</th>'\n                        + '<th style=\"padding:3px 5px;text-align:left;border-bottom:1px solid #ddd;\">Veículo</th>'\n                        + '<th style=\"padding:3px 5px;text-align:left;border-bottom:1px solid #ddd;\">Duração</th>'\n                        + '</tr></thead><tbody>';\n                    p.date_vehicle_list.forEach(function(d) {{\n                        dvRows += '<tr>'\n                            + '<td style=\"padding:2px 5px;border-bottom:1px solid #f0f0f0;\">' + escapeHtml(d.date_str || '—') + '</td>'\n                            + '<td style=\"padding:2px 5px;border-bottom:1px solid #f0f0f0;\">' + escapeHtml(d.vehicle || '—') + '</td>'\n                            + '<td style=\"padding:2px 5px;border-bottom:1px solid #f0f0f0;\">' + fmtSec(d.duration_sec) + '</td>'\n                            + '</tr>';\n                    }});\n                    dvRows += '</tbody></table>';\n                }}\n\n                var popup = '<div class=\"popup-header\" style=\"background:'+fill\n                    + ';color:'+tc+';border-bottom:2px solid '+bc+';\">'\n                    + '📦 ' + escapeHtml(p.name) + '</div>'\n                    + '<div class=\"popup-content\">'\n                    + stopLine\n                    + vehiclesLine\n                    + dvRows\n                    + '</div>';\n\n                layer.bindPopup(popup, {{ maxWidth: 340 }});\n            }}\n        }}).addTo(map);\n\n        if (POINTS.features && POINTS.features.length > 0 && pontosLayer.getBounds().isValid()) {{\n            map.fitBounds(pontosLayer.getBounds(), {{ padding: [60, 60] }});\n        }}\n\n        // ===== Geocoder =====\n        var searchMarker = null;\n        var searchTimeout = null;\n        var geocoderContainer = document.getElementById('geocoder-container');\n        var geocoderToggle    = document.getElementById('geocoder-toggle');\n        var geocoderInput     = document.getElementById('geocoder-input');\n        var geocoderResults   = document.getElementById('geocoder-results');\n        var isGeoExpanded = false;\n\n        function positionLeftPanel() {{\n            var legend = document.getElementById('legend');\n            var gap = 10;\n            if (geocoderContainer) {{\n                geocoderContainer.style.top = '20px';\n            }}\n            if (geocoderContainer && legend) {{\n                var geoRect = geocoderContainer.getBoundingClientRect();\n                legend.style.top = (geoRect.bottom + gap) + 'px';\n            }}\n        }}\n        setTimeout(positionLeftPanel, 200);\n        window.addEventListener('resize', positionLeftPanel);\n\n        if (geocoderToggle) {{\n            geocoderToggle.addEventListener('click', function(e) {{\n                e.stopPropagation();\n                isGeoExpanded = !isGeoExpanded;\n                if (isGeoExpanded) {{\n                    geocoderContainer.classList.add('expanded');\n                    setTimeout(function(){{ geocoderInput.focus(); }}, 100);\n                }} else {{\n                    geocoderContainer.classList.remove('expanded');\n                    geocoderResults.classList.remove('active');\n                    geocoderInput.value = '';\n                }}\n            }});\n        }}\n        if (geocoderInput) {{\n            geocoderInput.addEventListener('input', function() {{\n                clearTimeout(searchTimeout);\n                var q = this.value.trim();\n                searchTimeout = setTimeout(function() {{\n                    if (q.length < 3) {{ geocoderResults.classList.remove('active'); return; }}\n                    fetch('https://nominatim.openstreetmap.org/search?format=json&q=' + encodeURIComponent(q) + '&countrycodes=br&addressdetails=1&limit=5&accept-language=pt-BR')\n                    .then(function(r){{ return r.json(); }})\n                    .then(function(data) {{\n                        geocoderResults.innerHTML = '';\n                        if (!data.length) {{ geocoderResults.innerHTML = '<div class=\"no-results\">Nenhum resultado</div>'; }}\n                        else {{ data.forEach(function(item) {{\n                            var div = document.createElement('div');\n                            div.className = 'result-item';\n                            div.textContent = item.display_name;\n                            div.onclick = function() {{\n                                if (searchMarker) map.removeLayer(searchMarker);\n                                searchMarker = L.marker([parseFloat(item.lat), parseFloat(item.lon)]).addTo(map);\n                                searchMarker.bindPopup(item.display_name).openPopup();\n                                searchMarker.on('click', function(){{ map.removeLayer(searchMarker); searchMarker=null; }});\n                                map.setView([parseFloat(item.lat), parseFloat(item.lon)], 17);\n                                isGeoExpanded = false;\n                                geocoderContainer.classList.remove('expanded');\n                                geocoderResults.classList.remove('active');\n                            }};\n                            geocoderResults.appendChild(div);\n                        }}); }}\n                        geocoderResults.classList.add('active');\n                    }}).catch(function(){{ }});\n                }}, 400);\n            }});\n            geocoderInput.addEventListener('keydown', function(e) {{\n                if (e.key === 'Escape') {{ isGeoExpanded=false; geocoderContainer.classList.remove('expanded'); geocoderResults.classList.remove('active'); }}\n            }});\n            document.addEventListener('click', function(e){{\n                if (!e.target.closest('#geocoder-container')) {{\n                    if (isGeoExpanded) {{ isGeoExpanded=false; geocoderContainer.classList.remove('expanded'); }}\n                    geocoderResults.classList.remove('active');\n                }}\n            }});\n        }}\n\n        // Controles padrão de zoom\n        L.control.zoom({{ position: 'topleft' }}).addTo(map);\n\n        console.log('📦 Mapa de Estacionários inicializado. Pontos:', POINTS.features.length);\n    }})();\n    </script>\n    "

def export_estacionarios_map_html(contract_base, tipo, pontos, date_from, date_to):
    """
    Gera HTML do mapa de Estacionários para um (contrato, tipo).

    Args:
        contract_base: Nome do contrato (ex: "CABO")
        tipo: Tipo de estacionário (ex: "Caixas Brooks")
        pontos: GeoDataFrame de pontos já com stats (mean_stop_sec, total_stops, etc.)
        date_from / date_to: Período exibido no título e nome do arquivo

    Returns:
        Path do HTML salvo ou None se não houver pontos.
    """
    if pontos is None or pontos.empty:
        logger.warning('[Estacionarios] %s/%s: sem pontos para gerar mapa.', contract_base, tipo)
        return None
    if pontos.crs and pontos.crs.to_epsg() != 4326:
        pontos = pontos.to_crs(4326)
    geojson = pontos_to_geojson(pontos)
    if not geojson.get('features'):
        logger.warning('[Estacionarios] %s/%s: geojson vazio.', contract_base, tipo)
        return None
    bounds = pontos.total_bounds
    center_lat = ((bounds[1] + bounds[3]) / 2)
    center_lon = ((bounds[0] + bounds[2]) / 2)
    simple_contract, display_contract = simplify_contract_name(contract_base)
    safe_tipo = tipo.replace(' ', '_')
    date_str = f"do dia {date_from.strftime('%d/%m/%Y')}" if date_from == date_to else f"de {date_from.strftime('%d/%m/%Y')} à {date_to.strftime('%d/%m/%Y')}"
    title = f"Estacionários — {tipo} — {display_contract} {date_str}"
    points_json = json.dumps(geojson, ensure_ascii=False)
    leaflet_map_html = f"\n        {leaflet_css_inline()}\n        <div id=\"leafletMap\"></div>\n        <div id=\"map-title\">{title}</div>\n        {_LEGEND_HTML}\n        {_geocoder_html()}\n    "
    scripts_html = _leaflet_script(points_json, center_lat, center_lon)
    logo_main = _img_b64('logo_DIE.png')
    logo_1 = _img_b64('logo_locar.png')
    out_file = f"Estacionarios_{safe_tipo}_{simple_contract}_de-{date_from.strftime('%d.%m.%Y')}-a-{date_to.strftime('%d.%m.%Y')}.html"
    html = f"\n<!DOCTYPE html>\n<html lang='pt-BR'>\n<head>\n    <meta charset='utf-8' />\n    <meta name='viewport' content='width=device-width,initial-scale=1'>\n    <title>{out_file}</title>\n    {get_styles()}\n    {get_leaflet_styles()}\n</head>\n<body>\n    <div class=\"main-wrapper\">\n        <div class=\"plot-container\">\n            <div id=\"mapPlot\" class=\"plot-wrapper\">\n                {leaflet_map_html}\n            </div>\n        </div>\n    </div>\n\n    <div id='logo_container'>\n        <div class=\"logo-bar\">\n            <div class=\"logo-left\">\n                <img src=\"data:image/webp;base64,{logo_main}\">\n            </div>\n            <div class=\"logo-right\">\n                <img src=\"data:image/webp;base64,{logo_1}\">\n            </div>\n        </div>\n    </div>\n\n    {scripts_html}\n</body>\n</html>\n    "
    out_path = PATHS.heatmaps_dir / out_file
    with open(out_path, 'w', encoding='utf-8', errors='xmlcharrefreplace') as f:
        f.write(html)
    logger.info('[Estacionarios] %s/%s → %s', contract_base, tipo, out_path)
    return out_path

