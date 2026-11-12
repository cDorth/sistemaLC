"""
Servidor Web e Interface SPA do sitemaLCv1 (app.py)
Desenvolvido para Locar Saneamento Ambiental.

Interface 100% clean, executiva e profissional no padrão Verde Locar (#008844),
com filtros integrados por Setor, Tipo de Coleta e Veículos, além de rótulos dos setores no mapa.
Headers anti-cache habilitados para evitar páginas antigas no navegador.
"""

import os
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import database as db
from engine import HeatmapEngine

engine = HeatmapEngine()

HTML_PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8" />
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
    <meta http-equiv="Pragma" content="no-cache" />
    <meta http-equiv="Expires" content="0" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>LOCAR - Monitoramento de Rotas e Heatmap Viário</title>
    <!-- Leaflet CSS -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <!-- Google Fonts Inter -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    
    <style>
        :root {
            --locar-green: #008844;
            --locar-green-dark: #005C2E;
            --locar-green-light: #E8F5E9;
            --locar-green-hover: #007338;
            --gray-bg: #F8FAFC;
            --gray-card: #FFFFFF;
            --gray-border: #E2E8F0;
            --text-dark: #1E293B;
            --text-muted: #64748B;
            --danger-red: #E53935;
            --warning-amber: #FBC02D;
            --manual-orange: #FB8C00;
            --collected-green: #00A859;
            --card-shadow: 0 2px 4px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.03);
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: var(--gray-bg);
            color: var(--text-dark);
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        /* HEADER */
        header {
            background-color: #FFFFFF;
            border-bottom: 2px solid var(--locar-green);
            padding: 9px 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            z-index: 1000;
        }
        .header-brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .logo-box {
            background: linear-gradient(135deg, var(--locar-green), var(--locar-green-dark));
            color: white;
            font-weight: 800;
            font-size: 1.15rem;
            letter-spacing: 1px;
            padding: 5px 12px;
            border-radius: 5px;
        }
        .header-title h1 {
            font-size: 1.0rem;
            font-weight: 700;
            color: var(--text-dark);
        }
        .header-title p {
            font-size: 0.74rem;
            color: var(--text-muted);
        }

        .header-status {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .badge-status {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 11px;
            border-radius: 9999px;
            font-size: 0.74rem;
            font-weight: 600;
            background-color: var(--locar-green-light);
            color: var(--locar-green-dark);
        }
        .badge-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--locar-green);
        }

        /* TOOLBAR */
        .toolbar {
            background: #FFFFFF;
            border-bottom: 1px solid var(--gray-border);
            padding: 8px 20px;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 14px;
            z-index: 999;
        }
        .filter-group {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .filter-group label {
            font-size: 0.75rem;
            font-weight: 700;
            color: var(--text-muted);
            text-transform: uppercase;
        }
        select, input[type="date"] {
            border: 1px solid var(--gray-border);
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 0.82rem;
            font-family: inherit;
            color: var(--text-dark);
            background-color: #FFFFFF;
            outline: none;
            transition: border-color 0.15s;
        }
        select:focus, input[type="date"]:focus {
            border-color: var(--locar-green);
        }

        .btn-locar {
            background-color: var(--locar-green);
            color: #FFFFFF;
            border: none;
            border-radius: 6px;
            padding: 7px 16px;
            font-size: 0.82rem;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: background-color 0.15s, transform 0.05s;
        }
        .btn-locar:hover { background-color: var(--locar-green-hover); }
        .btn-locar:active { transform: scale(0.98); }

        .btn-outline {
            background-color: transparent;
            color: var(--text-dark);
            border: 1px solid var(--gray-border);
            border-radius: 6px;
            padding: 7px 12px;
            font-size: 0.82rem;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.15s;
        }
        .btn-outline:hover {
            border-color: var(--text-muted);
            background-color: var(--gray-bg);
        }

        /* STATS BAR */
        .stats-bar {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 10px;
            padding: 8px 20px;
            background-color: #FAFAFA;
            border-bottom: 1px solid var(--gray-border);
        }
        .stat-card {
            background: #FFFFFF;
            border: 1px solid var(--gray-border);
            border-radius: 6px;
            padding: 6px 12px;
            display: flex;
            flex-direction: column;
            box-shadow: var(--card-shadow);
        }
        .stat-card.destaque { border-left: 4px solid var(--locar-green); }
        .stat-card.alerta { border-left: 4px solid var(--danger-red); }
        .stat-title {
            font-size: 0.68rem;
            font-weight: 700;
            color: var(--text-muted);
            text-transform: uppercase;
        }
        .stat-value {
            font-size: 1.15rem;
            font-weight: 700;
            color: var(--text-dark);
            margin-top: 1px;
        }
        .stat-sub {
            font-size: 0.68rem;
            color: var(--text-muted);
        }

        /* MAIN CONTAINER */
        .main-container {
            flex: 1;
            display: flex;
            position: relative;
            overflow: hidden;
        }
        #map {
            flex: 1;
            height: 100%;
            background-color: #FFFFFF;
        }

        /* SIDEBAR DIREITA (ESTILO OFICIAL LOCAR / PASTA 5) */
        .sidebar {
            width: 340px;
            background-color: #FFFFFF;
            border-left: 1px solid var(--gray-border);
            display: flex;
            flex-direction: column;
            z-index: 500;
            box-shadow: -2px 0 6px rgba(0,0,0,0.03);
            overflow-y: auto;
        }
        .sidebar-section {
            padding: 14px 16px;
            border-bottom: 1px solid var(--gray-border);
        }
        .sidebar-section h4 {
            font-size: 0.76rem;
            font-weight: 800;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.6px;
            margin-bottom: 12px;
        }
        .sidebar-field {
            margin-bottom: 12px;
        }
        .sidebar-field:last-child {
            margin-bottom: 0;
        }
        .sidebar-field label {
            display: block;
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--text-dark);
            margin-bottom: 5px;
        }
        .sidebar-field select {
            width: 100%;
            padding: 8px 12px;
            font-size: 0.84rem;
            border: 1px solid var(--gray-border);
            border-radius: 6px;
            background-color: #FFFFFF;
            color: var(--text-dark);
            cursor: pointer;
        }
        .sidebar-field select:focus {
            border-color: var(--locar-green);
            outline: none;
        }
        .section-hint {
            font-size: 0.72rem;
            color: var(--text-muted);
            font-style: italic;
            margin-top: 5px;
            line-height: 1.3;
        }

        /* BOTOES TOGGLE VISUALIZACOES (MOSTRAR / OCULTAR) */
        .toggle-group {
            display: flex;
            border: 1px solid var(--gray-border);
            border-radius: 6px;
            overflow: hidden;
            margin-top: 6px;
        }
        .toggle-btn {
            flex: 1;
            padding: 7px 12px;
            font-size: 0.8rem;
            font-weight: 700;
            border: none;
            cursor: pointer;
            background: #FFFFFF;
            color: var(--text-muted);
            transition: all 0.15s;
        }
        .toggle-btn.active {
            background: #1E293B;
            color: #FFFFFF;
        }

        /* LEGENDA COMPACTA */
        .legend-compact {
            display: flex;
            flex-direction: column;
            gap: 6px;
            margin-top: 6px;
        }
        .legend-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 0.76rem;
            padding: 5px 8px;
            background: var(--gray-bg);
            border-radius: 4px;
        }
        .legend-color-dot {
            width: 10px;
            height: 10px;
            border-radius: 2px;
            display: inline-block;
            margin-right: 6px;
        }

        /* RÓTULOS DOS SETORES SOBRE O MAPA (EX: SCID 15, SCID 20) */
        .sector-badge-label {
            background: rgba(255, 255, 255, 0.95);
            border: 2px solid #E91E63;
            color: #0F172A;
            font-weight: 800;
            font-size: 14px;
            padding: 3px 8px;
            border-radius: 6px;
            box-shadow: 0 3px 6px rgba(0,0,0,0.25);
            text-align: center;
            white-space: nowrap;
            pointer-events: none;
            letter-spacing: 0.5px;
        }

        /* MODAL / DRAWER DE DETALHES */
        #detailDrawer {
            position: absolute;
            bottom: 20px;
            left: 20px;
            width: 320px;
            background: #FFFFFF;
            border-radius: 8px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.15);
            border: 1px solid var(--gray-border);
            z-index: 1000;
            padding: 14px 16px;
            display: none;
        }
        .drawer-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--gray-border);
            padding-bottom: 6px;
            margin-bottom: 8px;
        }
        .drawer-header h5 {
            font-size: 0.82rem;
            font-weight: 700;
            color: var(--locar-green-dark);
        }
        .drawer-close {
            background: none;
            border: none;
            font-size: 1.1rem;
            cursor: pointer;
            color: var(--text-muted);
        }

        /* SPINNER */
        #loadingOverlay {
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(255, 255, 255, 0.75);
            backdrop-filter: blur(2px);
            z-index: 2000;
            display: none;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            gap: 10px;
        }
        .spinner {
            width: 40px;
            height: 40px;
            border: 4px solid var(--gray-border);
            border-top-color: var(--locar-green);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .loading-text {
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--locar-green-dark);
        }
    </style>
</head>
<body>

    <!-- HEADER -->
    <header>
        <div class="header-brand">
            <div class="logo-box">LOCAR</div>
            <div class="header-title">
                <h1>SISTEMA LC v1 · Auditoria de Coleta e Heatmap</h1>
                <p>Monitoramento operacional integrado via Inlog API</p>
            </div>
        </div>
        <div class="header-status">
            <div class="badge-status" id="inlogStatusBadge">
                <span class="badge-dot"></span>
                <span id="inlogStatusText">CONECTANDO INLOG...</span>
            </div>
        </div>
    </header>

    <!-- TOOLBAR -->
    <div class="toolbar">
        <div class="filter-group">
            <label for="selectCidade">Cidade / Contrato:</label>
            <select id="selectCidade">
                <option value="jaboatao">Jaboatão dos Guararapes</option>
                <option value="cabo">Cabo de Santo Agostinho</option>
                <option value="olinda">Olinda</option>
                <option value="paulista">Paulista</option>
                <option value="caruaru">Caruaru</option>
                <option value="cuiaba">Cuiabá</option>
                <option value="garanhuns">Garanhuns</option>
                <option value="pesqueira">Pesqueira</option>
                <option value="vitoria">Vitória de Santo Antão</option>
                <option value="canela">Canela</option>
                <option value="jequie">Jequié</option>
                <option value="novo hamburgo">Novo Hamburgo</option>
            </select>
        </div>

        <div class="filter-group">
            <label for="inputData">Data do Turno:</label>
            <input type="date" id="inputData" value="2026-11-05" />
        </div>

        <button class="btn-locar" id="btnConsultar">
            <svg width="15" height="15" fill="currentColor" viewBox="0 0 16 16"><path d="M11.534 7h3.932a.25.25 0 0 1 .192.41l-1.966 2.36a.25.25 0 0 1-.384 0l-1.966-2.36a.25.25 0 0 1 .192-.41zm-11 2h3.932a.25.25 0 0 0 .192-.41L2.692 6.23a.25.25 0 0 0-.384 0L.342 8.59A.25.25 0 0 0 .534 9z"/><path fill-rule="evenodd" d="M8 3c-1.552 0-2.94.707-3.857 1.818a.5.5 0 1 1-.771-.636A6.002 6.002 0 0 1 13.917 7H12.9A5.002 5.002 0 0 0 8 3zM3.1 9a5.002 5.002 0 0 0 8.757 2.182.5.5 0 1 1 .771.636A6.002 6.002 0 0 1 2.083 9H3.1z"/></svg>
            Sincronizar Inlog & Auditar
        </button>

        <button class="btn-outline" id="btnBaixarCsv">
            <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16"><path d="M.5 9.9a.5.5 0 0 1 .5.5v2.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2.5a.5.5 0 0 1 1 0v2.5a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2v-2.5a.5.5 0 0 1 .5-.5z"/><path d="M7.646 11.854a.5.5 0 0 0 .708 0l3-3a.5.5 0 0 0-.708-.708L8.5 10.293V1.5a.5.5 0 0 0-1 0v8.793L5.354 8.146a.5.5 0 1 0-.708.708l3 3z"/></svg>
            Baixar Áreas Vermelhas (CSV)
        </button>
    </div>

    <!-- STATS BAR -->
    <div class="stats-bar">
        <div class="stat-card destaque">
            <span class="stat-title" id="lblKpiCobertura">Cobertura Viária</span>
            <span class="stat-value" id="kpiCobertura">-- %</span>
            <span class="stat-sub" id="kpiExtensao">-- km coletados</span>
        </div>
        <div class="stat-card">
            <span class="stat-title" id="lblKpiMalha">Extensão da Malha</span>
            <span class="stat-value" id="kpiTotalKm">-- km</span>
            <span class="stat-sub" id="kpiTotalVias">-- vias cadastradas</span>
        </div>
        <div class="stat-card alerta">
            <span class="stat-title" id="lblKpiOmitidas">Ruas Omitidas (Vermelhas)</span>
            <span class="stat-value" id="kpiOmitidas" style="color: var(--danger-red);">-- vias</span>
            <span class="stat-sub" id="kpiOmitidasKm">-- km pendentes</span>
        </div>
        <div class="stat-card">
            <span class="stat-title" id="lblKpiVeiculos">Veículos em Operação</span>
            <span class="stat-value" id="kpiCarros">--</span>
            <span class="stat-sub">Placas identificadas na Inlog</span>
        </div>
    </div>

    <!-- MAIN CONTAINER -->
    <div class="main-container">
        <div id="map"></div>

        <!-- SIDEBAR DIREITA (ESTILO OFICIAL LOCAR / PASTA 5) -->
        <div class="sidebar">
            <!-- 1. FILTROS DE COLETA -->
            <div class="sidebar-section">
                <h4>Filtros de Coleta</h4>
                <div class="sidebar-field">
                    <label for="filterColeta">Por coleta</label>
                    <select id="filterColeta">
                        <option value="all">Todos</option>
                        <option value="collected">Coletadas</option>
                        <option value="not_collected">Não coletadas (Crítica)</option>
                        <option value="possible_gps_gap">Possível falha de GPS</option>
                        <option value="possible_manual">Possível coleta manual</option>
                    </select>
                </div>
                <div class="sidebar-field">
                    <label for="filterSetor">Por setor</label>
                    <select id="filterSetor">
                        <option value="all">Todos os setores</option>
                    </select>
                </div>
            </div>

            <!-- 2. CARROS -->
            <div class="sidebar-section">
                <h4>Carros</h4>
                <div class="sidebar-field">
                    <label for="filterVeiculo">Veículo no mapa</label>
                    <select id="filterVeiculo">
                        <option value="all">Todos os veículos</option>
                    </select>
                    <div class="section-hint">Filtra segmentos por veículo que passou no período.</div>
                </div>
            </div>

            <!-- 3. VISUALIZAÇÕES -->
            <div class="sidebar-section">
                <h4>Visualizações</h4>
                <div class="sidebar-field">
                    <label>🏷 Códigos dos setores</label>
                    <div class="toggle-group">
                        <button type="button" class="toggle-btn active" id="btnShowLabels">Mostrar</button>
                        <button type="button" class="toggle-btn" id="btnHideLabels">Ocultar</button>
                    </div>
                </div>
            </div>

            <!-- 4. LEGENDA RESUMIDA -->
            <div class="sidebar-section">
                <h4>Legenda de Status</h4>
                <div class="legend-compact">
                    <div class="legend-row">
                        <span><span class="legend-color-dot" style="background:#00A859;"></span> Coletada</span>
                        <strong id="legColetada">--</strong>
                    </div>
                    <div class="legend-row">
                        <span><span class="legend-color-dot" style="background:#E53935;"></span> Não coletada (Omissão)</span>
                        <strong id="legNaoColetada" style="color:var(--danger-red);">--</strong>
                    </div>
                    <div class="legend-row">
                        <span><span class="legend-color-dot" style="background:#FB8C00;"></span> Coleta Manual</span>
                        <strong id="legManual">--</strong>
                    </div>
                    <div class="legend-row">
                        <span><span class="legend-color-dot" style="background:#FBC02D;"></span> Falha de GPS</span>
                        <strong id="legGps">--</strong>
                    </div>
                </div>
            </div>
        </div>

        <!-- DRAWER DE DETALHES DO SEGMENTO CLICADO -->
        <div id="detailDrawer">
            <div class="drawer-header">
                <h5>Detalhes do Segmento</h5>
                <button class="drawer-close" id="btnCloseDrawer">&times;</button>
            </div>
            <div style="font-size: 0.78rem; line-height: 1.6;" id="detailContent"></div>
        </div>

        <!-- LOADING OVERLAY -->
        <div id="loadingOverlay">
            <div class="spinner"></div>
            <div class="loading-text" id="loadingText">Consultando API Inlog & Processando Rotas...</div>
        </div>
    </div>

    <!-- Leaflet JS -->
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        let map;
        let geojsonLayer = null;
        let sectorLabelsLayer = null;
        let currentAudit = null;
        let currentFeatures = [];
        let setoresList = [];
        let showSectorLabels = true;

        function initMap() {
            map = L.map('map', {
                preferCanvas: true,
                zoomControl: true
            }).setView([-8.1930, -34.9364], 12);

            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap · Locar Saneamento',
                maxZoom: 19
            }).addTo(map);

            sectorLabelsLayer = L.layerGroup().addTo(map);
        }

        async function carregarAuditoria(forcar = false) {
            const cidade = document.getElementById('selectCidade').value;
            const data = document.getElementById('inputData').value;
            
            document.getElementById('loadingOverlay').style.display = 'flex';
            document.getElementById('loadingText').innerText = `Buscando Inlog (${cidade.toUpperCase()}) em ${data}...`;

            try {
                // Adiciona timestamp para impedir cache do navegador
                const url = `/api/auditoria?cidade=${cidade}&data=${data}&force=${forcar}&_t=${Date.now()}`;
                const resp = await fetch(url);
                const dataRes = await resp.json();

                if (dataRes.erro) {
                    alert('Aviso: ' + dataRes.erro);
                    document.getElementById('loadingOverlay').style.display = 'none';
                    return;
                }

                currentAudit = dataRes;
                currentFeatures = dataRes.geojson.features;
                setoresList = dataRes.setores || [];

                popularSelectsFiltros(dataRes);
                renderizarResultado();
            } catch (err) {
                console.error(err);
                alert('Erro de comunicação com o servidor.');
            } finally {
                document.getElementById('loadingOverlay').style.display = 'none';
            }
        }

        function popularSelectsFiltros(dataRes) {
            const selectSetor = document.getElementById('filterSetor');
            const selectVeiculo = document.getElementById('filterVeiculo');

            // 1. Popula Select de Setores
            const setorAtual = selectSetor.value;
            selectSetor.innerHTML = '<option value="all">Todos os setores</option>';
            setoresList.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.codigo;
                opt.textContent = `Apenas ${s.codigo}`;
                selectSetor.appendChild(opt);
            });
            if ([...selectSetor.options].some(o => o.value === setorAtual)) {
                selectSetor.value = setorAtual;
            }

            // 2. Popula Select de Veículos
            const veiculoAtual = selectVeiculo.value;
            selectVeiculo.innerHTML = '<option value="all">Todos os veículos</option>';
            const placas = dataRes.resumo.placas_ativas || [];
            placas.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p;
                opt.textContent = `Apenas ${p}`;
                selectVeiculo.appendChild(opt);
            });
            if ([...selectVeiculo.options].some(o => o.value === veiculoAtual)) {
                selectVeiculo.value = veiculoAtual;
            }
        }

        function renderizarResultado() {
            const r = currentAudit.resumo;
            document.getElementById('inlogStatusText').innerText = r.status_inlog;

            // Renderiza rótulos dos setores no mapa
            renderizarRotulosSetores();

            // Aplica filtros e desenha as vias no mapa
            aplicarFiltros();
        }

        function renderizarRotulosSetores() {
            sectorLabelsLayer.clearLayers();
            if (!showSectorLabels || !setoresList) return;

            setoresList.forEach(s => {
                if (s.centro && s.centro[0] !== 0) {
                    const icon = L.divIcon({
                        className: 'sector-badge-label',
                        html: s.codigo,
                        iconSize: null
                    });
                    const marker = L.marker(s.centro, { icon: icon });
                    sectorLabelsLayer.addLayer(marker);
                }
            });
        }

        function aplicarFiltros() {
            const filtroColeta = document.getElementById('filterColeta').value;
            const filtroSetor = document.getElementById('filterSetor').value;
            const filtroVeiculo = document.getElementById('filterVeiculo').value;

            let featuresFiltradas = [];
            let totalM = 0, coletadoM = 0, omissaoM = 0;
            let cCol = 0, cOmi = 0, cMan = 0, cGps = 0;
            let veiculosNoFiltro = new Set();

            currentFeatures.forEach(f => {
                const p = f.properties;

                // 1. Filtro por Coleta
                if (filtroColeta !== 'all' && p.status !== filtroColeta) {
                    return;
                }

                // 2. Filtro por Setor
                if (filtroSetor !== 'all' && p.sector !== filtroSetor) {
                    return;
                }

                // 3. Filtro por Veículo
                if (filtroVeiculo !== 'all') {
                    if (!p.vehicles || !p.vehicles.includes(filtroVeiculo)) {
                        return;
                    }
                }

                featuresFiltradas.push(f);
                const len = p.len_m || 0;
                totalM += len;

                if (p.status === 'collected') {
                    coletadoM += len;
                    cCol++;
                } else if (p.status === 'not_collected') {
                    omissaoM += len;
                    cOmi++;
                } else if (p.status === 'possible_manual') {
                    coletadoM += len;
                    cMan++;
                } else if (p.status === 'possible_gps_gap') {
                    cGps++;
                }

                if (p.vehicles) p.vehicles.forEach(v => veiculosNoFiltro.add(v));
            });

            // Atualiza KPIs conforme os filtros selecionados
            const pct = totalM > 0 ? (coletadoM / totalM * 100) : 0;
            document.getElementById('kpiCobertura').innerText = pct.toFixed(2) + ' %';
            document.getElementById('kpiExtensao').innerText = (coletadoM / 1000).toFixed(2) + ' km coletados';
            document.getElementById('kpiTotalKm').innerText = (totalM / 1000).toFixed(2) + ' km';
            document.getElementById('kpiTotalVias').innerText = featuresFiltradas.length + ' trechos filtrados';
            document.getElementById('kpiOmitidas').innerText = cOmi + ' vias';
            document.getElementById('kpiOmitidasKm').innerText = (omissaoM / 1000).toFixed(2) + ' km pendentes';
            document.getElementById('kpiCarros').innerText = veiculosNoFiltro.size;

            // Atualiza contadores da legenda
            document.getElementById('legColetada').innerText = cCol;
            document.getElementById('legNaoColetada').innerText = cOmi;
            document.getElementById('legManual').innerText = cMan;
            document.getElementById('legGps').innerText = cGps;

            // Renderiza no mapa
            if (geojsonLayer) map.removeLayer(geojsonLayer);

            geojsonLayer = L.geoJSON({ type: 'FeatureCollection', features: featuresFiltradas }, {
                style: function(feat) {
                    const p = feat.properties;
                    let cor = '#00A859'; // Verde oficial Locar
                    if (p.status === 'not_collected') cor = '#E53935'; // Vermelho omissao
                    else if (p.status === 'possible_manual') cor = '#FB8C00'; // Laranja manual
                    else if (p.status === 'possible_gps_gap') cor = '#FBC02D'; // Amarelo gap

                    return {
                        color: cor,
                        weight: 4,
                        opacity: 0.9,
                        lineCap: 'round',
                        lineJoin: 'round'
                    };
                },
                onEachFeature: function(feat, layer) {
                    layer.on('click', () => abrirDetalhesSegmento(feat.properties));
                }
            }).addTo(map);

            // Se filtrou por setor específico, dá zoom na área do setor
            if (filtroSetor !== 'all') {
                const sInfo = setoresList.find(s => s.codigo === filtroSetor);
                if (sInfo && sInfo.centro && sInfo.centro[0] !== 0) {
                    map.setView(sInfo.centro, 14, { animate: true });
                }
            }
        }

        function abrirDetalhesSegmento(p) {
            const drawer = document.getElementById('detailDrawer');
            const content = document.getElementById('detailContent');
            const carros = (p.vehicles && p.vehicles.length) ? p.vehicles.join(', ') : 'Nenhum';

            let statusFormatado = 'COLETADA';
            let statusColor = '#00A859';
            if (p.status === 'not_collected') { statusFormatado = 'NÃO COLETADA (OMISSÃO)'; statusColor = '#E53935'; }
            else if (p.status === 'possible_manual') { statusFormatado = 'COLETA MANUAL'; statusColor = '#FB8C00'; }
            else if (p.status === 'possible_gps_gap') { statusFormatado = 'FALHA DE GPS'; statusColor = '#FBC02D'; }

            content.innerHTML = `
                <div style="margin-bottom:6px;"><b style="font-size:0.9rem; color:var(--text-dark);">${p.street_name}</b></div>
                <div><b>Setor:</b> ${p.sector}</div>
                <div><b>Extensão:</b> ${p.len_m} metros</div>
                <div><b>Situação:</b> <span style="font-weight:700; color:${statusColor};">${statusFormatado}</span></div>
                <div><b>Veículos Inlog:</b> ${carros}</div>
            `;
            drawer.style.display = 'block';
        }

        // EVENTOS DE INTERAÇÃO
        document.getElementById('filterColeta').addEventListener('change', aplicarFiltros);
        document.getElementById('filterSetor').addEventListener('change', aplicarFiltros);
        document.getElementById('filterVeiculo').addEventListener('change', aplicarFiltros);

        document.getElementById('btnShowLabels').addEventListener('click', function() {
            showSectorLabels = true;
            this.classList.add('active');
            document.getElementById('btnHideLabels').classList.remove('active');
            renderizarRotulosSetores();
        });

        document.getElementById('btnHideLabels').addEventListener('click', function() {
            showSectorLabels = false;
            this.classList.add('active');
            document.getElementById('btnShowLabels').classList.remove('active');
            renderizarRotulosSetores();
        });

        document.getElementById('btnCloseDrawer').addEventListener('click', () => {
            document.getElementById('detailDrawer').style.display = 'none';
        });

        document.getElementById('btnConsultar').addEventListener('click', () => carregarAuditoria(true));
        document.getElementById('selectCidade').addEventListener('change', () => carregarAuditoria(false));
        document.getElementById('inputData').addEventListener('change', () => carregarAuditoria(false));
        document.getElementById('btnBaixarCsv').addEventListener('click', () => {
            if (!currentAudit || !currentAudit.resumo) return alert('Nenhuma auditoria processada para exportar.');
            window.location.href = `/api/exportar-csv?id=${currentAudit.resumo.id}`;
        });

        window.onload = () => {
            initMap();
            carregarAuditoria(false);
        };
    </script>
</body>
</html>
"""

class LocarRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Interface Web Principal SPA (com headers anti-cache forçados)
        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        # 2. Endpoint da Auditoria
        if path == "/api/auditoria":
            cidade = query.get("cidade", ["jaboatao"])[0]
            data_iso = query.get("data", ["2026-11-05"])[0]
            force = query.get("force", ["false"])[0].lower() == "true"

            resultado = engine.processar_ou_obter_auditoria(cidade, data_iso, forcar_reprocessamento=force)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(resultado, ensure_ascii=False).encode("utf-8"))
            return

        # 3. Exportação de CSV de Vias Omitidas (Streaming direto em memória)
        if path == "/api/exportar-csv":
            audit_id = int(query.get("id", [0])[0])
            csv_str, filename = engine.gerar_csv_omissoes_string(audit_id)
            
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8-sig")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(csv_str.encode("utf-8-sig"))
            return

        # 4. Status da Inlog
        if path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            status_data = {
                "inlog_configurado": engine.inlog.is_configured(),
                "base_url": engine.inlog.base_url
            }
            self.wfile.write(json.dumps(status_data).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

def run(port: int = 8080):
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, LocarRequestHandler)
    print("=" * 70)
    print(f" LOCAR SANEAMENTO AMBIENTAL - SISTEMA LC v1")
    print(f" Servidor iniciado com sucesso em: http://127.0.0.1:{port}")
    print(f" Banco SQLite ativo: {db.DB_PATH}")
    print("=" * 70)
    httpd.serve_forever()

if __name__ == "__main__":
    porta = int(os.getenv("PORT", 8080))
    run(porta)
