"""
Templates HTML, CSS e JavaScript para visualizações.

Este módulo contém todos os templates de interface para os mapas interativos.
Separado do plotting.py para melhor organização e manutenção.
"""

def get_styles():
    """Retorna CSS completo para o mapa."""
    return """
    <style>
        html, body {
            width: 100%;
            height: 100%;
            margin: 0;
            padding: 0;
            overflow: hidden;
            font-family: Arial, Helvetica, sans-serif;
        }

        .main-wrapper {
            display: flex;
            flex-direction: row;
            width: 100vw;
            height: 100vh;
        }

        .plot-container {
            flex: 1 1 auto;
            min-width: 0;
            height: 100%;
            position: relative;
        }

        .plot-wrapper {
            box-sizing: border-box;
        }

        .plot-wrapper, .js-plotly-plot {
            height: 100%;
            width: 100%;
        }

        .js-plotly-plot, .js-plotly-plot>div {
            height: 100% !important;
            width: 100% !important;
            min-height: 0;
        }

        .plotly-graph-div {
            display: flex;
            flex-direction: column;
            justify-content: stretch;
            height: 100%;
        }

        #logo_container {
            position: fixed;
            bottom: 0;
            left: 0;
            height: 22vh;
            z-index: 10000;
            pointer-events: none;
        }

        .logo-bar {
            height: 100%;
            display: flex;
            align-items: center;
            background: rgba(255, 255, 255, 0);
            padding: 0 16px;
            box-sizing: border-box;
        }

        .logo-left {
            flex: 0 0 auto;
            display: flex;
            align-items: center;
        }

        .logo-left img {
            margin-left: 3vh;
            height: 12.5vh;
        }

        .logo-right {
            flex: 1 1 auto;
            display: flex;
            justify-content: flex-end;
            align-items: center;
            margin-right: 2vw;
            gap: 10vw;
        }

        .logo-right img {
            height: 12vh;
            opacity: 0.9;
        }

        body.sidebar-open #logo_container {
            width: calc(100vw - 380px);
            right: 380px;
        }

        body.sidebar-closed #logo_container {
            width: calc(100vw - 1vw);
            right: 30px;
        }

        /* SIDEBAR */
        #heatmap-sidebar {
            position: fixed;
            top: 0;
            right: 0;
            width: 380px;
            height: 100vh;
            background: #fff;
            border-left: 1px solid #ddd;
            overflow: visible;
            padding: 12px;
            box-shadow: -2px 0 8px rgba(0, 0, 0, 0.06);
            box-sizing: border-box;
            z-index: 10000;
            transform: translateX(0);
            transition: transform .26s ease;
        }

        #heatmap-sidebar.closed {
            transform: translateX(380px);
            border-left: none;
        }

        #heatmap-sidebar-handle {
            position: absolute;
            left: -30px;
            top: 50%;
            transform: translateY(-50%);
            z-index: 10001;
            width: 28px;
            height: 68px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #fff;
            border: 1px solid #ccc;
            border-right: none;
            border-radius: 4px 0 0 4px;
            cursor: pointer;
            font-size: 15px;
            font-weight: 700;
            box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
        }

        #heatmap-sidebar .sidebar-inner {
            display: flex;
            flex-direction: column;
            gap: 10px;
            height: 100%;
            box-sizing: border-box;
        }

        #heatmap-sidebar .history {
            flex: 0 0 auto;
        }

        #heatmap-sidebar .advanced-config-trigger {
            flex: 0 0 auto;
            margin-top: 8px;
        }

        #advanced-config-btn {
            width: 100%;
            padding: 10px 12px;
            background: #1e1e1e;
            color: #fff;
            border: none;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: background 0.15s;
        }

        #advanced-config-btn:hover {
            background: #333;
        }

        /* POPOVER DE CONFIGURAÇÕES AVANÇADAS */
        #advanced-config-popover {
            position: fixed;
            top: 0;
            right: 0;
            width: 380px;
            height: 100vh;
            background: #fff;
            border-left: 1px solid #ddd;
            overflow: hidden;
            box-shadow: -2px 0 12px rgba(0, 0, 0, 0.12);
            box-sizing: border-box;
            z-index: 10003;
            transform: translateX(380px);
            transition: transform .26s ease;
            display: flex;
            flex-direction: column;
        }

        #advanced-config-popover.open {
            transform: translateX(0);
        }

        #advanced-config-popover .popover-header {
            position: sticky;
            top: 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 14px;
            border-bottom: 2px solid #eee;
            background: #fff;
            flex: 0 0 auto;
        }

        #advanced-config-popover .popover-header h3 {
            margin: 0;
            font-size: 14px;
            font-weight: 700;
            color: #111;
        }

        #advanced-config-close {
            background: none;
            border: 1px solid #ccc;
            border-radius: 4px;
            width: 28px;
            height: 28px;
            font-size: 18px;
            line-height: 1;
            cursor: pointer;
            color: #666;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all .15s;
        }

        #advanced-config-close:hover {
            background: #f5f5f5;
            color: #333;
            border-color: #999;
        }

        #advanced-config-popover .popover-body {
            flex: 1 1 auto;
            overflow-y: auto;
            padding: 12px 14px;
            box-sizing: border-box;
        }

        #advanced-config-popover .popover-section {
            margin-bottom: 18px;
        }

        #advanced-config-popover .popover-section h4 {
            margin: 0 0 8px 0;
            font-size: 12px;
            font-weight: 700;
            color: #444;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-bottom: 1px solid #eee;
            padding-bottom: 4px;
        }

        #advanced-config-popover select:disabled {
            background: #f5f5f5;
            color: #999;
            cursor: not-allowed;
        }

        #advanced-config-popover .section-hint {
            font-size: 11px;
            color: #888;
            font-style: italic;
            margin-top: -2px;
            margin-bottom: 6px;
        }

        .chart-wrapper {
            width: 100%;
            padding: 8px 0;
            max-width: 100%;
            box-sizing: border-box;
            overflow-x: hidden;
        }

        #historyChart {
            width: 100%;
            max-width: 100%;
            height: auto;
            min-height: 0;
            margin: 0;
            padding: 6px;
            box-sizing: border-box;
            border-radius: 4px;
            background: linear-gradient(to top, rgba(0, 102, 22, 0.678), rgba(0, 255, 42, 0.18));
            border: 1px solid #eee;
        }

        label {
            display: block;
            margin: 6px 0 4px 0;
            font-weight: 600;
            font-size: 12px;
            color: #333;
        }

        select {
            width: 100%;
            padding: 6px;
            margin-bottom: 8px;
            box-sizing: border-box;
            font-size: 13px;
        }

        .priority-control {
            margin-top: 8px;
            margin-bottom: 4px;
        }

        .priority-toggle {
            display: flex;
            width: 100%;
            border: 1px solid #cfcfcf;
            border-radius: 4px;
            overflow: hidden;
            box-sizing: border-box;
        }

        .priority-btn {
            flex: 1 1 0;
            padding: 6px 4px;
            background: #f7f7f7;
            border: none;
            border-right: 1px solid #cfcfcf;
            font-size: 12px;
            font-weight: 600;
            color: #555;
            cursor: pointer;
            transition: background 0.12s, color 0.12s;
        }

        .priority-btn:last-child {
            border-right: none;
        }

        .priority-btn:hover {
            background: #ececec;
        }

        .priority-btn.active {
            background: #1e1e1e;
            color: #fff;
        }

        .avg-box {
            margin-top: 8px;
        }

        .avg-label {
            font-size: 12px;
            font-weight: 600;
            color: #333;
            margin-bottom: 4px;
        }

        .avg-bar {
            position: relative;
            width: 100%;
            height: 22px;
            background: #eee;
            border-radius: 4px;
            overflow: hidden;
            border: 1px solid #ddd;
        }

        .avg-bar-fill {
            height: 100%;
            width: 0%;
            background: red;
            transition: width 0.4s ease, background-color 0.4s ease;
        }

        .avg-bar-text {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 700;
            color: #111;
            pointer-events: none;
        }

        h3 {
            margin: 6px 0 8px 0;
            font-size: 14px;
            font-weight: 700;
            color: #111;
        }

        .small {
            font-size: 12px;
            color: #444;
        }
        
        /* ==================== ESTILOS BASE DO GEOCODER ==================== */
        /* Estes estilos são usados quando o plugin leaflet-control-geocoder está ativo */
        .leaflet-control-geocoder {
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            border: none;
        }
        
        .leaflet-control-geocoder-form input {
            font-size: 14px;
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid #ddd;
        }
        
        .leaflet-control-geocoder-form input:focus {
            outline: none;
            border-color: #007bff;
            box-shadow: 0 0 0 2px rgba(0,123,255,0.25);
        }
        
        .leaflet-control-geocoder-icon {
            background-color: #fff;
            border-radius: 8px;
        }
        
        .leaflet-control-geocoder-alternatives {
            border-radius: 0 0 8px 8px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.15);
            max-height: 300px;
            overflow-y: auto;
        }
        
        .leaflet-control-geocoder-alternatives a {
            padding: 10px 12px;
            font-size: 13px;
        }
        
        .leaflet-control-geocoder-alternatives a:hover {
            background-color: #f0f0f0;
        }
        
        /* Marcador de busca */
        .search-marker-popup {
            font-size: 13px;
            line-height: 1.5;
        }
        
        .search-marker-popup strong {
            color: #007bff;
        }
    </style>
    """

def get_sidebar_template():
    """Retorna HTML da sidebar + popover de configurações avançadas."""
    return """
    <div id='heatmap-sidebar' aria-hidden='false'>
        <div id='heatmap-sidebar-handle' title='Abrir / Fechar'>></div>
        <div class='sidebar-inner'>
            <div class='history'>
                <h3 id='history_tittle'>Histórico de Coleta (Mês)</h3>
                <div class='sidebar-section'>
                    <div class='chart-wrapper'>
                        <div id='historyChart' class='small' aria-hidden='false'></div>
                    </div>
                </div>
                <div class="avg-box" id="avgBoxMonthTotal">
                    <div class="avg-label" id="lblMonthTotal">Média de Coleta do <b>Mês</b></div>
                    <div class="avg-bar">
                        <div class="avg-bar-fill" id="barMonthTotal"></div>
                        <div class="avg-bar-text" id="txtMonthTotal">—</div>
                    </div>
                </div>

                <div class="avg-box" id="avgBoxMonthUntil">
                    <div class="avg-label" id="lblMonthUntil">Média de Coleta do <b>Mês</b> (Até Dia —)</div>
                    <div class="avg-bar">
                        <div class="avg-bar-fill" id="barMonthUntil"></div>
                        <div class="avg-bar-text" id="txtMonthUntil">—</div>
                    </div>
                </div>

                <div class="avg-box" id="avgBoxDay">
                    <div class="avg-label" id="lblDay">Média de Coleta do <b>Dia</b></div>
                    <div class="avg-bar">
                        <div class="avg-bar-fill" id="barDay"></div>
                        <div class="avg-bar-text" id="txtDay">—</div>
                    </div>
                </div>

                <div class="avg-box" id="avgBoxManual">
                    <div class="avg-label" id="lblManual">Média de <u>Coleta + Manual</u> do <b>Dia</b></div>
                    <div class="avg-bar">
                        <div class="avg-bar-fill" id="barManual"></div>
                        <div class="avg-bar-text" id="txtManual">—</div>
                    </div>
                </div>
            </div>

            <div class='advanced-config-trigger'>
                <button type='button' id='advanced-config-btn' title='Abrir configurações avançadas'>
                    <span>⚙</span>
                    <span>Configurações Avançadas</span>
                </button>
            </div>
        </div>
    </div>

    <div id='advanced-config-popover' aria-hidden='true'>
        <div class='popover-header'>
            <h3>⚙ Configurações Avançadas</h3>
            <button type='button' id='advanced-config-close' title='Fechar'>&times;</button>
        </div>
        <div class='popover-body'>
            <div class='popover-section'>
                <h4>Filtros de coleta</h4>
                <label for='ratioFilterSelect'>Por coleta</label>
                <select id='ratioFilterSelect'></select>
                <label for='codeFilterSelect'>Por setor</label>
                <select id='codeFilterSelect'></select>
            </div>

            <div class='popover-section'>
                <h4>Carros</h4>
                <label for='vehicleFilterSelect'>Veículo no mapa</label>
                <select id='vehicleFilterSelect'></select>
                <div class='section-hint' id='vehicleFilterHint'>Filtra segmentos por veículo que passou no período.</div>
            </div>

            <div class='popover-section' id='visualizations-section' style='display:none;'>
                <h4>Visualizações</h4>
                <div id='priority-control' class='priority-control' style='display:none;'>
                    <label>🌟 Ruas prioritárias</label>
                    <div class='priority-toggle' role='radiogroup' aria-label='Modo de destaque'>
                        <button type='button' class='priority-btn active' data-mode='highlight' title='Destacar com halo'>Destacar</button>
                        <button type='button' class='priority-btn' data-mode='dim' title='Esmaecer demais ruas'>Esmaecer</button>
                        <button type='button' class='priority-btn' data-mode='only' title='Mostrar somente prioritárias'>Apenas</button>
                    </div>
                </div>
                <div id='pontos-viciados-control' class='priority-control' style='display:none;'>
                    <label>📍 Pontos viciados</label>
                    <div class='priority-toggle' role='radiogroup' aria-label='Visibilidade dos pontos viciados'>
                        <button type='button' class='priority-btn active' data-pv-mode='show' title='Exibir pontos viciados no mapa'>Mostrar</button>
                        <button type='button' class='priority-btn' data-pv-mode='hide' title='Ocultar pontos viciados do mapa'>Ocultar</button>
                    </div>
                </div>
                <div id='sector-labels-control' class='priority-control' style='display:none;'>
                    <label>🏷 Códigos dos setores</label>
                    <div class='priority-toggle' role='radiogroup' aria-label='Código do setor sobre o mapa'>
                        <button type='button' class='priority-btn active' data-sl-mode='show' title='Escrever o código de cada setor sobre a área dele'>Mostrar</button>
                        <button type='button' class='priority-btn' data-sl-mode='hide' title='Sem códigos sobre o mapa'>Ocultar</button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """

def get_scripts(filter_config_json, history_json, overall_json):
    """
    Retorna JavaScript completo.
    
    VERSÃO OTIMIZADA com:
    - Filtros combinados (ratio + código simultâneos)
    - Estatísticas dinâmicas baseadas em filtros ativos
    - Cache de visibilidade otimizado
    """
    return f"\n    <script>\n        window.HEATMAP_FILTER_CONFIG = {filter_config_json};\n        window.HEATMAP_HISTORY = {history_json};\n        window.HEATMAP_OVERALL = {overall_json};\n    </script>\n\n    <script>\n        (function () {{\n            'use strict';\n            \n            var cfg = window.HEATMAP_FILTER_CONFIG || {{ ratioFilters: [], codeFilters: [], tracesCount: 0 }};\n            var hist = window.HEATMAP_HISTORY || {{ dates: [], pcts: [], month_start: null, month_end: null }};\n            var overall = window.HEATMAP_OVERALL;\n\n            console.log('[OPT] Filter config loaded:', cfg);\n            console.log('[OPT] Dynamic stats enabled:', cfg.enableDynamicStats);\n            console.log('[OPT] Combined filters enabled:', cfg.enableCombinedFilters);\n\n            // ============================================\n            // ESTADO DA APLICAÇÃO\n            // ============================================\n            \n            var State = {{\n                currentRatioFilter: 0,  // Índice do filtro ativo\n                currentCodeFilter: 0,   // Índice do filtro ativo\n                lastVisible: null,\n            }};\n\n            function getPlot() {{\n                var container = document.getElementById('mapPlot');\n                if (!container) return null;\n                return container.querySelector('.js-plotly-plot');\n            }}\n            var plotContainer = null;\n            let plotHeightFixed = false;\n\n            function forceFullHeightPlot() {{\n                var plot = getPlot();\n                if (!plot || !window.Plotly) return;\n                if (plotHeightFixed) return;\n\n                var wrapper = plot.closest('.plot-wrapper');\n                if (!wrapper) return;\n\n                var h = wrapper.clientHeight;\n                var w = wrapper.clientWidth;\n\n                if (h > 0 && w > 0) {{\n                    Plotly.relayout(plot, {{ height: h, width: w }});\n                    plotHeightFixed = true;\n                }}\n            }}\n\n            function arraysEqual(a, b) {{\n                if (!a || !b || a.length !== b.length) return false;\n                for (var i = 0; i < a.length; i++) {{\n                    if (a[i] !== b[i]) return false;\n                }}\n                return true;\n            }}\n\n            // ============================================\n            // CÁLCULO DINÂMICO DE ESTATÍSTICAS\n            // ============================================\n            \n            function calculateDynamicStats(visibleArray) {{\n                if (!cfg.enableDynamicStats || !cfg.traceToMetadata) {{\n                    return overall;\n                }}\n                \n                var totalVisited = 0;\n                var totalExpected = 0;\n                var count = 0;\n                \n                visibleArray.forEach(function(isVisible, idx) {{\n                    if (!isVisible) return;\n                    \n                    // Usa o mapeamento correto trace_index - metadata\n                    var meta = cfg.traceToMetadata[idx.toString()];\n                    if (!meta) return;\n                    \n                    totalVisited += (meta.visited || 0);\n                    totalExpected += (meta.total || 0);\n                    count++;\n                }});\n                \n                if (totalExpected === 0 || count === 0) {{\n                    return null;\n                }}\n                \n                var ratio = totalVisited / totalExpected;\n                console.log('[OPT] Dynamic stats: ' + (ratio * 100).toFixed(1) + '% from ' + count + ' visible traces');\n                console.log('[OPT]   - Visited: ' + totalVisited + ', Expected: ' + totalExpected);\n                return ratio;\n            }}\n            \n            // ============================================\n            // FILTROS COMBINADOS\n            // ============================================\n            \n            function applyCombinedFilters(ratioIdx, codeIdx) {{\n                console.log('[OPT] Applying filters: ratio=' + ratioIdx + ', code=' + codeIdx);\n                \n                if (!cfg.enableCombinedFilters) {{\n                    // Modo legado: apenas um filtro por vez\n                    applyLegacyFilter(ratioIdx, codeIdx);\n                    return;\n                }}\n                \n                // NOVO: Combina ambos os filtros\n                var ratioFilter = cfg.ratioFilters[ratioIdx];\n                var codeFilter = cfg.codeFilters[codeIdx];\n                \n                if (!ratioFilter || !codeFilter) return;\n                \n                var visibleArray = new Array(cfg.tracesCount);\n                \n                // Itera por cada trace\n                for (var i = 0; i < cfg.tracesCount; i++) {{\n                    // Verifica se trace passa no filtro de ratio\n                    var passesRatio = ratioFilter.visible[i];\n                    \n                    // Verifica se trace passa no filtro de código\n                    var passesCode = codeFilter.visible[i];\n                    \n                    // Trace visível apenas se passa AMBOS os filtros\n                    visibleArray[i] = passesRatio && passesCode;\n                }}\n                \n                // Aplica visibilidade\n                applyVisible(visibleArray);\n                \n                // Atualiza estatística dinâmica\n                var dynamicRatio = calculateDynamicStats(visibleArray);\n                updateOverallDisplay(dynamicRatio);\n            }}\n            \n            function applyLegacyFilter(ratioIdx, codeIdx) {{\n                // Modo legado: usa apenas um filtro\n                var activeIdx = (ratioIdx === 0) ? codeIdx : ratioIdx;\n                var activeFilters = (ratioIdx === 0) ? cfg.codeFilters : cfg.ratioFilters;\n                \n                if (!activeFilters || !activeFilters[activeIdx]) return;\n                \n                applyVisible(activeFilters[activeIdx].visible);\n            }}\n\n            function applyVisible(visibleArr) {{\n                var plot = getPlot();\n                if (!plot || !window.Plotly) return;\n\n                if (State.lastVisible && arraysEqual(State.lastVisible, visibleArr)) {{\n                    return;  // Cache: evita update desnecessário\n                }}\n\n                State.lastVisible = visibleArr.slice();\n                Plotly.update(plot, {{ visible: visibleArr }});\n            }}\n            \n            // ============================================\n            // ATUALIZAÇÃO DE UI\n            // ============================================\n            \n            function updateOverallDisplay(ratio) {{\n                // Atualiza barra \"Média de Coleta do Dia\"\n                var barDay = document.getElementById('barDay');\n                var txtDay = document.getElementById('txtDay');\n                \n                if (!barDay || !txtDay) return;\n                \n                if (ratio !== null && typeof ratio === 'number') {{\n                    var pct = ratio * 100;\n                    barDay.style.width = pct.toFixed(1) + '%';\n                    barDay.style.backgroundColor = colorFromPercent(pct);\n                    txtDay.textContent = pct.toFixed(1).replace('.', ',') + '%';\n                }} else {{\n                    barDay.style.width = '0%';\n                    txtDay.textContent = '—';\n                }}\n            }}\n\n            function fillSelect(id, items) {{\n                var sel = document.getElementById(id);\n                if (!sel) {{\n                    console.error('Select not found:', id);\n                    return;\n                }}\n                if (!items || !items.length) {{\n                    console.warn('No items for select:', id);\n                    return;\n                }}\n                \n                sel.innerHTML = '';\n                items.forEach(function (it, i) {{\n                    var opt = document.createElement('option');\n                    opt.value = i;\n                    opt.textContent = it.label || 'Opção ' + (i + 1);\n                    sel.appendChild(opt);\n                }});\n                \n                console.log('[OPT] Filled select', id, 'with', items.length, 'items');\n            }}\n\n            // Filtros\n            if (cfg.ratioFilters && cfg.ratioFilters.length) {{\n                console.log('[OPT] Setting up ratio filters...');\n                fillSelect('ratioFilterSelect', cfg.ratioFilters);\n                var ratioSel = document.getElementById('ratioFilterSelect');\n\n                ratioSel.addEventListener('change', function () {{\n                    var idx = Number(this.value) || 0;\n                    State.currentRatioFilter = idx;\n                    console.log('[OPT] Ratio filter changed to:', idx, cfg.ratioFilters[idx].label);\n                    \n                    if (cfg.enableCombinedFilters) {{\n                        // Mantém filtro de código ativo\n                        applyCombinedFilters(State.currentRatioFilter, State.currentCodeFilter);\n                    }} else {{\n                        // Reseta filtro de código\n                        applyCombinedFilters(idx, 0);\n                        var codeSel = document.getElementById('codeFilterSelect');\n                        if (codeSel) codeSel.selectedIndex = 0;\n                        State.currentCodeFilter = 0;\n                    }}\n                }});\n\n                ratioSel.selectedIndex = 0;\n            }} else {{\n                console.error('[OPT] No ratio filters found!');\n            }}\n\n            if (cfg.codeFilters && cfg.codeFilters.length) {{\n                console.log('[OPT] Setting up code filters...');\n                fillSelect('codeFilterSelect', cfg.codeFilters);\n                var codeSel = document.getElementById('codeFilterSelect');\n\n                codeSel.addEventListener('change', function () {{\n                    var idx = Number(this.value) || 0;\n                    State.currentCodeFilter = idx;\n                    console.log('[OPT] Code filter changed to:', idx, cfg.codeFilters[idx].label);\n                    \n                    if (cfg.enableCombinedFilters) {{\n                        // Mantém filtro de ratio ativo\n                        applyCombinedFilters(State.currentRatioFilter, State.currentCodeFilter);\n                    }} else {{\n                        // Reseta filtro de ratio\n                        applyCombinedFilters(0, idx);\n                        var ratioSel = document.getElementById('ratioFilterSelect');\n                        if (ratioSel) ratioSel.selectedIndex = 0;\n                        State.currentRatioFilter = 0;\n                    }}\n                }});\n\n                codeSel.selectedIndex = 0;\n            }} else {{\n                console.error('[OPT] No code filters found!');\n            }}\n\n            // Aplica visibilidade inicial\n            if (cfg.ratioFilters && cfg.ratioFilters.length) {{\n                console.log('[OPT] Applying initial visibility...');\n                applyCombinedFilters(0, 0);\n            }}\n\n            // Gráfico de histórico\n            if (hist.dates && hist.dates.length && window.Plotly) {{\n                var x = hist.dates;\n                var y = hist.pcts;\n                var hover_texts = x.map(function (d, i) {{\n                    var day = (d && d.length >= 10) ? d.slice(8, 10) : (d || '');\n                    var pct = (typeof y[i] === 'number') ? (y[i].toFixed(2).replace('.', ',') + '%') : (String(y[i]) || '—');\n                    return 'Dia ' + day + ' - ' + pct;\n                }});\n\n                var xRange = undefined;\n                if (hist.mode === 'range' && hist.period_from && hist.period_to) {{\n                    xRange = [hist.period_from, hist.period_to];\n                }} else if (hist.mode === 'single' && hist.month_start && hist.month_end) {{\n                    xRange = [hist.month_start, hist.month_end];\n                }}\n\n                var trace = {{\n                    x: x, y: y,\n                    mode: 'lines+markers',\n                    fill: 'tozeroy',\n                    fillcolor: 'rgba(0, 150, 255, 0.25)',\n                    line: {{ color: 'rgb(0, 120, 220)', width: 2 }},\n                    marker: {{ size: 6 }},\n                    text: hover_texts,\n                    hoverinfo: 'text',\n                    name: 'Média'\n                }};\n\n                var layout = {{\n                    height: 200,\n                    margin: {{ l: 32, r: 32, t: 26, b: 36 }},\n                    autosize: true,\n                    xaxis: {{\n                        type: 'date',\n                        tickformat: '%d/%m',\n                        tickangle: 35,\n                        automargin: false,\n                        fixedrange: true,\n                        range: xRange,\n                    }},\n                    yaxis: {{\n                        range: [0, 100],\n                        fixedrange: true,\n                        automargin: false\n                    }},\n                    hoverlabel: {{\n                        bgcolor: 'rgba(0, 0, 0, 0.75)',\n                        bordercolor: '#ffffff',\n                        font: {{ size: 12, color: '#ffffff' }},\n                        align: 'left'\n                    }},\n                    plot_bgcolor: 'rgba(0,0,0,0)',\n                    paper_bgcolor: 'rgba(0,0,0,0)',\n                }};\n\n                Plotly.newPlot('historyChart', [trace], layout, {{ displayModeBar: false }});\n            }}\n\n            // Barras de média\n            function colorFromPercent(pct) {{\n                pct = Math.max(0, Math.min(100, pct));\n                var hue = (pct / 100) * 120;\n                return 'hsl(' + hue + ', 85%, 45%)';\n            }}\n\n            function updateAvgBar(barId, textId, valuePct) {{\n                var bar = document.getElementById(barId);\n                var txt = document.getElementById(textId);\n\n                if (!bar || !txt || typeof valuePct !== 'number') {{\n                    if (txt) txt.textContent = '—';\n                    if (bar) bar.style.width = '0%';\n                    return;\n                }}\n\n                var pct = Math.max(0, Math.min(100, valuePct));\n                bar.style.width = pct.toFixed(1) + '%';\n                bar.style.backgroundColor = colorFromPercent(pct);\n                txt.textContent = pct.toFixed(1).replace('.', ',') + '%';\n            }}\n\n            // Atualiza médias\n            (function () {{\n                var isRange = hist.mode === 'range';\n                var isSingle = hist.mode === 'single';\n\n                if (isRange) {{\n                    var sum = 0, cnt = 0;\n                    hist.pcts.forEach(function (v) {{\n                        if (typeof v === 'number') {{\n                            sum += v;\n                            cnt++;\n                        }}\n                    }});\n                    var avg = cnt > 0 ? (sum / cnt) : null;\n\n                    document.getElementById('history_tittle').textContent = 'Histórico de Coleta (Período)';\n                    document.getElementById('lblMonthTotal').innerHTML = 'Média de Coleta do Período';\n                    updateAvgBar('barMonthTotal', 'txtMonthTotal', avg);\n\n                    var dayBox = document.getElementById('avgBoxDay');\n                    if (dayBox) dayBox.style.display = 'none';\n                    var untilBox = document.getElementById('avgBoxMonthUntil');\n                    if (untilBox) untilBox.style.display = 'none';\n                    var manualBox = document.getElementById('avgBoxManual');\n                    if (manualBox) manualBox.style.display = 'none';\n                }}\n\n                if (isSingle) {{\n                    var currentDateStr = hist.current_day;\n                    if (!currentDateStr || !hist.dates.includes(currentDateStr)) {{\n                        currentDateStr = hist.dates.filter(function (d) {{ return typeof d === 'string'; }}).sort().slice(-1)[0] || null;\n                    }}\n                    var currentDate = currentDateStr ? new Date(currentDateStr + \"T00:00:00\") : null;\n                    var dayLabel = currentDate ? currentDate.toLocaleDateString('pt-BR', {{ day: '2-digit', month: '2-digit' }}) : '—';\n\n                    var dayPct = (cfg.enableDynamicStats && typeof overall === 'number') ? overall * 100 : null;\n                    \n                    if (dayPct !== null) {{\n                        document.getElementById('lblDay').innerHTML = 'Média de Coleta do <b>Dia</b>';\n                        updateAvgBar('barDay', 'txtDay', dayPct);\n                    }} else {{\n                        updateAvgBar('barDay', 'txtDay', null);\n                    }}\n\n                    // Elimina redundância mensal\n                    var lastHistDate = hist.dates.length > 0 ? hist.dates[hist.dates.length - 1] : null;\n                    var isLastDay = (lastHistDate === currentDateStr);\n\n                    var sum = 0, cnt = 0;\n                    hist.pcts.forEach(function (v) {{\n                        if (typeof v === 'number') {{\n                            sum += v;\n                            cnt += 1;\n                        }}\n                    }});\n\n                    var sumU = 0, cntU = 0;\n                    hist.dates.forEach(function (d, i) {{\n                        if (d <= currentDateStr && typeof hist.pcts[i] === 'number') {{\n                            sumU += hist.pcts[i];\n                            cntU += 1;\n                        }}\n                    }});\n\n                    if (isLastDay) {{\n                        if (cnt > 0) {{\n                            document.getElementById('lblMonthTotal').innerHTML = 'Média de Coleta do <b>Mês</b>';\n                            updateAvgBar('barMonthTotal', 'txtMonthTotal', sum / cnt);\n                        }} else {{\n                            updateAvgBar('barMonthTotal', 'txtMonthTotal', null);\n                        }}\n                        var untilBox = document.getElementById('avgBoxMonthUntil');\n                        if (untilBox) untilBox.style.display = 'none';\n                    }} else {{\n                        var monthTotalBox = document.getElementById('avgBoxMonthTotal');\n                        if (monthTotalBox) monthTotalBox.style.display = 'none';\n                        if (cntU > 0) {{\n                            document.getElementById('lblMonthUntil').innerHTML = 'Média de Coleta do <b>Mês</b> (Até Dia ' + dayLabel + ')';\n                            updateAvgBar('barMonthUntil', 'txtMonthUntil', sumU / cntU);\n                        }} else {{\n                            updateAvgBar('barMonthUntil', 'txtMonthUntil', null);\n                        }}\n                    }}\n                    \n                    // Barra manual: escondida neste fluxo (sem dados manual no Plotly legacy)\n                    var manualBox = document.getElementById('avgBoxManual');\n                    if (manualBox) manualBox.style.display = 'none';\n                }}\n                }}\n            }})();\n\n            // Controle da sidebar\n            var handle = document.getElementById('heatmap-sidebar-handle');\n            var sidebar = document.getElementById('heatmap-sidebar');\n            var open = true;\n\n            function initPlotContainer() {{\n                plotContainer = document.querySelector('.plot-container');\n                if (!plotContainer) {{\n                    var p = document.getElementsByClassName('js-plotly-plot');\n                    if (p && p.length) {{\n                        plotContainer = p[0].parentElement;\n                    }}\n                }}\n            }}\n\n            function doResizePlot(plotDiv) {{\n                if (!plotDiv || !window.Plotly) return;\n                try {{ Plotly.Plots.resize(plotDiv); }} catch (e) {{ }}\n            }}\n\n            function updateState() {{\n                if (open) {{\n                    document.body.classList.remove('sidebar-closed');\n                    document.body.classList.add('sidebar-open');\n                    sidebar.classList.remove('closed');\n                    handle.textContent = '>';\n                }} else {{\n                    document.body.classList.remove('sidebar-open');\n                    document.body.classList.add('sidebar-closed');\n                    sidebar.classList.add('closed');\n                    handle.textContent = '<';\n                }}\n\n                var plotDiv = getPlot();\n                if (plotDiv) {{\n                    try {{ plotDiv.style.width = '100%'; }} catch (e) {{ }}\n                    doResizePlot(plotDiv);\n                    setTimeout(function () {{ doResizePlot(plotDiv); }}, 300);\n                }}\n            }}\n\n            if (handle) {{\n                document.body.classList.add('sidebar-open');\n                initPlotContainer();\n                handle.addEventListener('click', function () {{\n                    open = !open;\n                    updateState();\n                    setTimeout(doResizePlot, 300);\n                }});\n            }}\n\n            window.addEventListener('load', function () {{\n                setTimeout(function () {{\n                    initPlotContainer();\n                    updateState();\n                    forceFullHeightPlot();\n                }}, 100);\n            }});\n        }})();\n    </script>\n    "

