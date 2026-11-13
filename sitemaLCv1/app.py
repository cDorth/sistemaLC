"""
Servidor Web e Interface SPA do sitemaLCv1 (app.py)
Desenvolvido para Locar Saneamento Ambiental.

Recursos:
1. Ruas Prioritárias com contorno nítido e elegante em Azul Safira (#1D4ED8), eliminando o laranja e borrões opacos.
2. Controle para Mostrar/Ocultar o destaque de prioridade com 1 clique.
3. Ruas normais limpas, nítidas e finas no padrão verde/vermelho.
4. Rótulos de setores sutis e modo de edição GIS integrado.
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
    <title>LOCAR - Monitoramento de Rotas, Auditoria & Projeto GIS</title>
    <!-- Leaflet CSS -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <!-- Google Fonts Inter -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    
    <style>
        :root {
            --locar-green: #008844;
            --locar-green-dark: #005C2E;
            --locar-green-light: #E8F5E9;
            --locar-green-hover: #007338;
            --edit-amber: #D97706;
            --edit-amber-dark: #92400E;
            --priority-blue: #1D4ED8;
            --gray-bg: #F8FAFC;
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
            justify-content: space-between;
            gap: 12px;
            z-index: 999;
        }
        .toolbar-left, .toolbar-right {
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
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
        select, input[type="date"], input[type="text"] {
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
        select:focus, input:focus { border-color: var(--locar-green); }

        .btn-locar {
            background-color: var(--locar-green);
            color: #FFFFFF;
            border: none;
            border-radius: 6px;
            padding: 7px 14px;
            font-size: 0.82rem;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.15s;
        }
        .btn-locar:hover { background-color: var(--locar-green-hover); }

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
        .btn-outline:hover { background-color: var(--gray-bg); }

        /* BOTAO MODO EDICAO DO PROJETO */
        .btn-edit-mode {
            background-color: #FEF3C7;
            color: var(--edit-amber-dark);
            border: 1px solid #FCD34D;
            border-radius: 6px;
            padding: 7px 14px;
            font-size: 0.82rem;
            font-weight: 700;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.15s;
        }
        .btn-edit-mode:hover { background-color: #FDE68A; }
        .btn-edit-mode.active {
            background-color: var(--edit-amber);
            color: #FFFFFF;
            border-color: var(--edit-amber-dark);
            box-shadow: 0 0 0 2px rgba(217, 119, 6, 0.3);
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
        .stat-sub { font-size: 0.68rem; color: var(--text-muted); }

        /* MAIN AREA */
        .main-container {
            flex: 1;
            display: flex;
            position: relative;
            overflow: hidden;
        }
        #map { flex: 1; height: 100%; }

        /* BARRA DE INSTRUÇÃO DE DESENHO FLUTUANTE */
        #drawInstructionBox {
            position: absolute;
            top: 14px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(15, 23, 42, 0.95);
            backdrop-filter: blur(4px);
            color: #FFFFFF;
            padding: 8px 18px;
            border-radius: 30px;
            font-size: 0.82rem;
            font-weight: 600;
            box-shadow: 0 6px 18px rgba(0,0,0,0.25);
            z-index: 1000;
            display: none;
            align-items: center;
            gap: 12px;
            border: 1px solid rgba(255,255,255,0.2);
        }
        #drawInstructionBox button {
            background: #E2E8F0;
            border: none;
            border-radius: 14px;
            padding: 3px 10px;
            font-size: 0.74rem;
            font-weight: 700;
            cursor: pointer;
            color: #0F172A;
        }
        #drawInstructionBox button:hover { background: #FFFFFF; }

        /* TOOLBAR GIS FLUTUANTE ESTILO QGIS */
        #gisToolbar {
            position: absolute;
            top: 14px;
            left: 56px;
            background: #FFFFFF;
            border: 1px solid var(--gray-border);
            border-radius: 8px;
            padding: 5px;
            box-shadow: 0 6px 16px rgba(0,0,0,0.12);
            z-index: 999;
            display: none;
            gap: 6px;
        }
        .gis-tool-btn {
            background: #F1F5F9;
            border: 1px solid #CBD5E1;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--text-dark);
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.15s;
        }
        .gis-tool-btn:hover { background: #E2E8F0; }
        .gis-tool-btn.active {
            background: var(--edit-amber);
            color: white;
            border-color: var(--edit-amber-dark);
        }

        /* SIDEBAR DIREITA */
        .sidebar {
            width: 350px;
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
        .sidebar-field { margin-bottom: 12px; }
        .sidebar-field:last-child { margin-bottom: 0; }
        .sidebar-field label {
            display: block;
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--text-dark);
            margin-bottom: 4px;
        }
        .sidebar-field select, .sidebar-field input {
            width: 100%;
            padding: 7px 10px;
            font-size: 0.82rem;
        }
        .section-hint {
            font-size: 0.72rem;
            color: var(--text-muted);
            font-style: italic;
            margin-top: 5px;
        }

        .toggle-group {
            display: flex;
            border: 1px solid var(--gray-border);
            border-radius: 6px;
            overflow: hidden;
            margin-top: 6px;
        }
        .toggle-btn {
            flex: 1;
            padding: 6px 12px;
            font-size: 0.78rem;
            font-weight: 700;
            border: none;
            cursor: pointer;
            background: #FFFFFF;
            color: var(--text-muted);
            transition: all 0.15s;
        }
        .toggle-btn.active { background: #1E293B; color: #FFFFFF; }

        /* RÓTULOS DOS SETORES: SUTIS (HOVER ONLY) */
        .sector-badge-container {
            position: relative;
            cursor: pointer;
            pointer-events: auto !important;
        }
        .sector-dot-marker {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background-color: var(--locar-green-dark);
            border: 2px solid #FFFFFF;
            box-shadow: 0 1px 4px rgba(0,0,0,0.3);
            opacity: 0.45;
            transition: all 0.2s ease;
        }
        .sector-pill-label {
            position: absolute;
            bottom: 14px;
            left: 50%;
            transform: translateX(-50%) scale(0.85);
            background: rgba(15, 23, 42, 0.92);
            backdrop-filter: blur(4px);
            color: #FFFFFF;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.4px;
            padding: 3px 9px;
            border-radius: 9999px;
            white-space: nowrap;
            box-shadow: 0 4px 12px rgba(0,0,0,0.25);
            opacity: 0;
            pointer-events: none;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            border: 1px solid rgba(255,255,255,0.15);
        }
        .sector-badge-container:hover .sector-dot-marker {
            opacity: 1;
            transform: scale(1.3);
            background-color: var(--locar-green);
        }
        .sector-badge-container:hover .sector-pill-label {
            opacity: 1;
            transform: translateX(-50%) scale(1);
        }
        .sector-badge-container.destaque .sector-dot-marker {
            opacity: 1;
            background-color: var(--locar-green);
            transform: scale(1.3);
        }
        .sector-badge-container.destaque .sector-pill-label {
            opacity: 1;
            transform: translateX(-50%) scale(1);
            background: var(--locar-green-dark);
            border-color: #6EE7B7;
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
            width: 40px; height: 40px;
            border: 4px solid var(--gray-border);
            border-top-color: var(--locar-green);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .loading-text { font-size: 0.85rem; font-weight: 600; color: var(--locar-green-dark); }
        /* BADGE SOMENTE LEITURA */
        .badge-readonly {
            background: #EFF6FF;
            color: #1D4ED8;
            border: 1px solid #BFDBFE;
            border-radius: 6px;
            padding: 6px 12px;
            font-size: 0.78rem;
            font-weight: 700;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        /* MULTI-SELECT DE SETORES */
        .multi-select-container { position: relative; width: 100%; }
        .multi-select-trigger {
            width: 100%;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 7px 10px;
            background: #FFFFFF;
            border: 1px solid var(--gray-border);
            border-radius: 6px;
            font-size: 0.82rem;
            font-family: inherit;
            color: var(--text-dark);
            cursor: pointer;
            text-align: left;
            transition: all 0.15s;
        }
        .multi-select-trigger:hover { border-color: var(--locar-green); }
        .multi-select-trigger.active { border-color: var(--locar-green); box-shadow: 0 0 0 2px rgba(0, 136, 68, 0.15); }
        .trigger-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 220px; font-weight: 500; }
        .sector-count-pill {
            font-size: 0.70rem;
            font-weight: 700;
            padding: 2px 7px;
            border-radius: 12px;
            background: #E2E8F0;
            color: var(--text-dark);
        }
        .sector-count-pill.active { background: var(--locar-green-light); color: var(--locar-green-dark); }
        .multi-select-dropdown {
            position: absolute;
            top: calc(100% + 4px);
            left: 0;
            right: 0;
            background: #FFFFFF;
            border: 1px solid var(--gray-border);
            border-radius: 8px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.15);
            z-index: 1500;
            overflow: hidden;
        }
        .multi-select-header {
            padding: 8px;
            background: #F8FAFC;
            border-bottom: 1px solid var(--gray-border);
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .multi-select-actions {
            display: flex;
            gap: 6px;
        }
        .btn-chip {
            background: #FFFFFF;
            border: 1px solid var(--gray-border);
            border-radius: 4px;
            padding: 3px 8px;
            font-size: 0.72rem;
            font-weight: 600;
            cursor: pointer;
            color: var(--text-dark);
            transition: all 0.1s;
        }
        .btn-chip:hover { background: var(--gray-bg); border-color: var(--locar-green); }
        .multi-select-search {
            width: 100%;
            padding: 5px 8px;
            font-size: 0.78rem;
            border: 1px solid var(--gray-border);
            border-radius: 4px;
            outline: none;
            box-sizing: border-box;
        }
        .multi-select-search:focus { border-color: var(--locar-green); }
        .multi-select-list {
            max-height: 220px;
            overflow-y: auto;
            padding: 4px 0;
        }
        .multi-select-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 10px;
            cursor: pointer;
            font-size: 0.80rem;
            transition: background 0.1s;
            user-select: none;
        }
        .multi-select-item:hover { background: #F1F5F9; }
        .multi-select-item.selected { background: #F0FDF4; }
        .multi-select-item input[type="checkbox"] { width: 14px; height: 14px; cursor: pointer; accent-color: var(--locar-green); margin: 0; }
        .sector-item-info { display: flex; justify-content: space-between; width: 100%; align-items: center; }
        .sector-code { font-weight: 600; color: var(--text-dark); }
        .sector-meta { font-size: 0.70rem; color: var(--text-muted); }

        /* MODAL DE COMPARTILHAMENTO */
        .modal-backdrop {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(15, 23, 42, 0.6);
            backdrop-filter: blur(4px);
            z-index: 3000;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }
        .modal-card {
            background: #FFFFFF;
            border-radius: 12px;
            width: 100%;
            max-width: 540px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.25);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            animation: modalFadeIn 0.2s ease-out;
        }
        @keyframes modalFadeIn {
            from { opacity: 0; transform: translateY(-10px) scale(0.98); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        .modal-header {
            padding: 16px 20px;
            background: #F8FAFC;
            border-bottom: 1px solid var(--gray-border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .modal-icon-badge {
            width: 34px; height: 34px;
            background: #DCFCE7;
            color: var(--locar-green-dark);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
        }
        .modal-title { font-size: 0.95rem; font-weight: 700; color: var(--text-dark); margin: 0; }
        .modal-subtitle { font-size: 0.74rem; color: var(--text-muted); margin: 2px 0 0 0; }
        .modal-close-btn {
            background: none; border: none; font-size: 22px; cursor: pointer; color: var(--text-muted); line-height: 1;
        }
        .modal-close-btn:hover { color: var(--danger-red); }
        .modal-body { padding: 18px 20px; max-height: calc(85vh - 120px); overflow-y: auto; }
        .modal-footer {
            padding: 12px 20px;
            background: #F8FAFC;
            border-top: 1px solid var(--gray-border);
            display: flex;
            justify-content: flex-end;
            gap: 10px;
        }
        .security-callout {
            background: #EFF6FF;
            border-left: 4px solid #3B82F6;
            border-radius: 6px;
            padding: 10px 12px;
            margin-bottom: 14px;
        }
        .modal-form-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }
        .share-sectors-summary {
            background: #F8FAFC;
            border: 1px solid var(--gray-border);
            border-radius: 6px;
            padding: 8px 10px;
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--text-dark);
        }
        .cities-checkbox-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 6px;
            background: #F8FAFC;
            border: 1px solid var(--gray-border);
            border-radius: 6px;
            padding: 8px;
            max-height: 110px;
            overflow-y: auto;
        }
        .city-checkbox-item {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.75rem;
            cursor: pointer;
        }
        .city-checkbox-item input { accent-color: var(--locar-green); width: 13px; height: 13px; }
        .share-input-group {
            display: flex;
            gap: 8px;
            margin-top: 4px;
        }
        .share-url-input {
            flex: 1;
            font-family: monospace;
            font-size: 0.76rem;
            background: #F1F5F9;
            color: #0F172A;
            border: 1px solid var(--gray-border);
            border-radius: 6px;
            padding: 7px 10px;
        }
        .locar-toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: #0F172A;
            color: #FFFFFF;
            padding: 10px 18px;
            border-radius: 8px;
            font-size: 0.82rem;
            font-weight: 600;
            box-shadow: 0 10px 25px rgba(0,0,0,0.3);
            z-index: 4000;
            display: none;
            align-items: center;
            gap: 8px;
            animation: toastIn 0.25s ease-out;
        }
        @keyframes toastIn {
            from { transform: translateY(20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
    </style>
</head>
<body>

    <!-- HEADER -->
    <header>
        <div class="header-brand">
            <div class="logo-box">LOCAR</div>
            <div class="header-title">
                <h1>SISTEMA LC v1 · Auditoria de Coleta & Edição do Projeto</h1>
                <p>Gestão das Malhas de Projeto por Cidade e Auditoria via Inlog</p>
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
        <div class="toolbar-left">
            <div class="filter-group">
                <label for="selectCidade">Projeto / Cidade:</label>
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

            <div class="filter-group" id="groupDataTurno">
                <label for="inputData">Data Turno:</label>
                <input type="date" id="inputData" value="2026-11-05" />
            </div>

            <button class="btn-locar" id="btnConsultar">
                <svg width="15" height="15" fill="currentColor" viewBox="0 0 16 16"><path d="M11.534 7h3.932a.25.25 0 0 1 .192.41l-1.966 2.36a.25.25 0 0 1-.384 0l-1.966-2.36a.25.25 0 0 1 .192-.41zm-11 2h3.932a.25.25 0 0 0 .192-.41L2.692 6.23a.25.25 0 0 0-.384 0L.342 8.59A.25.25 0 0 0 .534 9z"/><path fill-rule="evenodd" d="M8 3c-1.552 0-2.94.707-3.857 1.818a.5.5 0 1 1-.771-.636A6.002 6.002 0 0 1 13.917 7H12.9A5.002 5.002 0 0 0 8 3zM3.1 9a5.002 5.002 0 0 0 8.757 2.182.5.5 0 1 1 .771.636A6.002 6.002 0 0 1 2.083 9H3.1z"/></svg>
                Sincronizar Inlog & Auditar
            </button>

            <button class="btn-outline" id="btnBaixarCsv">
                <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16"><path d="M.5 9.9a.5.5 0 0 1 .5.5v2.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2.5a.5.5 0 0 1 1 0v2.5a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2v-2.5a.5.5 0 0 1 .5-.5z"/><path d="M7.646 11.854a.5.5 0 0 0 .708 0l3-3a.5.5 0 0 0-.708-.708L8.5 10.293V1.5a.5.5 0 0 0-1 0v8.793L5.354 8.146a.5.5 0 1 0-.708.708l3 3z"/></svg>
                Baixar Omissões (CSV)
            </button>
        </div>

        <div class="toolbar-right">
            <!-- BOTÃO GERAR LINK DE VISUALIZAÇÃO PERSONALIZADO -->
            <button class="btn-outline" id="btnShareLink" style="gap:6px;">
                <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16"><path d="M4.715 6.542 3.343 7.914a3 3 0 1 0 4.243 4.243l1.828-1.829A3 3 0 0 0 8.586 5.5L8 6.086a1.002 1.002 0 0 0-.154.199 2 2 0 0 1 .861 3.337L7.336 10.994a2 2 0 0 1-2.828-2.828l1.372-1.372a.5.5 0 0 0-.708-.708z"/><path d="M6.586 4.672A3 3 0 0 0 7.414 9.5l.775-.776a2 2 0 0 1-.896-3.346L9.12 3.55a2 2 0 1 1 2.83 2.83l-.793.792c.112.42.155.855.128 1.287l1.372-1.372a3 3 0 0 0-4.243-4.243L6.586 4.672z"/></svg>
                <span id="textShareLink">Gerar Link Personalizado</span>
            </button>

            <!-- BADGE MODO SOMENTE LEITURA (VISÍVEL APENAS QUANDO READONLY) -->
            <div class="badge-readonly" id="badgeReadOnly" style="display:none;">
                🔒 Modo Visualização (Somente Leitura)
            </div>

            <!-- BOTÃO MODO DE EDIÇÃO DO PROJETO -->
            <button class="btn-edit-mode" id="btnToggleEditMode">
                <svg width="15" height="15" fill="currentColor" viewBox="0 0 16 16"><path d="M15.502 1.94a.5.5 0 0 1 0 .706L14.459 3.69l-2-2L13.502.646a.5.5 0 0 1 .707 0l1.293 1.293zm-1.75 2.456-2-2L4.939 9.21a.5.5 0 0 0-.121.196l-.805 2.414a.25.25 0 0 0 .316.316l2.414-.805a.5.5 0 0 0 .196-.12l6.813-6.814z"/><path fill-rule="evenodd" d="M1 13.5A1.5 1.5 0 0 0 2.5 15h11a1.5 1.5 0 0 0 1.5-1.5v-6a.5.5 0 0 0-1 0v6a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5v-11a.5.5 0 0 1 .5-.5H9a.5.5 0 0 0 0-1H2.5A1.5 1.5 0 0 0 1 2.5v11z"/></svg>
                <span id="textEditMode">Modo de Edição do Projeto (QGIS Web)</span>
            </button>
        </div>
    </div>

    <!-- STATS BAR -->
    <div class="stats-bar">
        <div class="stat-card destaque">
            <span class="stat-title">Cobertura do Projeto</span>
            <span class="stat-value" id="kpiCobertura">-- %</span>
            <span class="stat-sub" id="kpiExtensao">-- km coletados</span>
        </div>
        <div class="stat-card">
            <span class="stat-title">Extensão Total do Projeto</span>
            <span class="stat-value" id="kpiTotalKm">-- km</span>
            <span class="stat-sub" id="kpiTotalVias">-- vias cadastradas</span>
        </div>
        <div class="stat-card alerta">
            <span class="stat-title">Ruas Omitidas (Vermelhas)</span>
            <span class="stat-value" id="kpiOmitidas" style="color: var(--danger-red);">-- vias</span>
            <span class="stat-sub" id="kpiOmitidasKm">-- km a repassar</span>
        </div>
        <div class="stat-card">
            <span class="stat-title">Veículos em Operação</span>
            <span class="stat-value" id="kpiCarros">--</span>
            <span class="stat-sub">Placas ativas na Inlog</span>
        </div>
    </div>

    <!-- MAIN CONTAINER -->
    <div class="main-container">
        <!-- MAPA -->
        <div id="map"></div>

        <!-- BARRA DE INSTRUÇÃO DE DESENHO FLUTUANTE -->
        <div id="drawInstructionBox">
            <span id="drawInstructionText">📍 1º Clique: Marque o INÍCIO da nova rua (pode clicar em cima de uma rua existente).</span>
            <button id="btnCancelDrawing">Cancelar</button>
        </div>

        <!-- BARRA FLUTUANTE DE FERRAMENTAS GIS (QGIS) -->
        <div id="gisToolbar">
            <button class="gis-tool-btn active" id="toolSelect" title="Selecionar trecho para inspecionar/editar no projeto">
                🔍 Selecionar
            </button>
            <button class="gis-tool-btn" id="toolDrawLine" title="Traçar nova rua no projeto (Clique inicial, depois clique final)">
                ➕ Desenhar Rua
            </button>
            <button class="gis-tool-btn" id="toolDelete" title="Excluir trecho selecionado do projeto">
                🗑 Apagar Rua
            </button>
            <button class="gis-tool-btn" id="toolSyncInlog" style="background:#DCFCE7; color:var(--locar-green-dark); border-color:#86EFAC;" title="Grava alterações na Inlog e atualiza malha">
                💾 Salvar na Inlog
            </button>
        </div>

        <!-- SIDEBAR DIREITA -->
        <div class="sidebar">
            <!-- PAINEL 1: FILTROS DE AUDITORIA (MODO NORMAL) -->
            <div id="panelAuditoria">
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
                    <div class="sidebar-field" style="position:relative;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                            <label style="margin-bottom:0;">Por setor (Multi-seleção):</label>
                            <span class="sector-count-pill" id="sectorSelectedCount">Todos</span>
                        </div>
                        <div class="multi-select-container" id="multiSetorContainer">
                            <button type="button" class="multi-select-trigger" id="btnMultiSetorTrigger">
                                <span class="trigger-text" id="multiSetorLabel">Todos os setores</span>
                                <svg class="chevron-icon" width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
                                    <path d="M1.646 4.646a.5.5 0 0 1 .708 0L8 10.293l5.646-5.647a.5.5 0 0 1 .708.708l-6 6a.5.5 0 0 1-.708 0l-6-6a.5.5 0 0 1 0-.708z"/>
                                </svg>
                            </button>
                            <div class="multi-select-dropdown" id="multiSetorDropdown" style="display:none;">
                                <div class="multi-select-header">
                                    <div class="multi-select-actions">
                                        <button type="button" class="btn-chip" id="btnSelectAllSectors">Marcar Todos</button>
                                        <button type="button" class="btn-chip" id="btnClearSectors">Desmarcar Todos</button>
                                    </div>
                                    <input type="text" id="inputSearchSector" class="multi-select-search" placeholder="🔍 Filtrar setor por nome/código..." />
                                </div>
                                <div class="multi-select-list" id="multiSetorList">
                                    <!-- Inserido dinamicamente via JavaScript -->
                                </div>
                            </div>
                        </div>
                        <div class="section-hint">Marque um, vários ou todos os setores para filtrar o mapa e os dados.</div>
                    </div>
                </div>

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

                <div class="sidebar-section">
                    <h4>Visualizações</h4>
                    
                    <!-- 1. CÓDIGOS DOS SETORES -->
                    <div class="sidebar-field">
                        <label>🏷 Códigos dos setores (Sutis / Hover)</label>
                        <div class="toggle-group">
                            <button type="button" class="toggle-btn active" id="btnShowLabels">Mostrar</button>
                            <button type="button" class="toggle-btn" id="btnHideLabels">Ocultar</button>
                        </div>
                        <div class="section-hint">Passe o mouse sobre os pontos para revelar os nomes dos setores.</div>
                    </div>

                    <!-- 2. DESTAQUE DE RUAS PRIORITÁRIAS (CONTORNO AZUL SAFIRA) -->
                    <div class="sidebar-field" style="margin-top:14px;">
                        <label>🌟 Ruas Prioritárias (Vias Principais)</label>
                        <div class="toggle-group">
                            <button type="button" class="toggle-btn active" id="btnShowPriority">Destacar (Azul)</button>
                            <button type="button" class="toggle-btn" id="btnHidePriority">Ocultar</button>
                        </div>
                        <div class="section-hint">Contorno azul cobalto nítido nas avenidas principais do projeto, sem interferir no status da coleta.</div>
                    </div>
                </div>

                <div class="sidebar-section">
                    <h4>Legenda do Mapa de Calor</h4>
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
                        <div class="legend-row" style="border-top:1px dashed var(--gray-border); padding-top:6px; margin-top:2px;">
                            <span><span class="legend-color-dot" style="background:#1D4ED8;"></span> Rua Prioritária (Borda Azul)</span>
                            <strong id="legPrioritaria" style="color:#1D4ED8;">--</strong>
                        </div>
                    </div>
                </div>
            </div>

            <!-- PAINEL 2: EDITOR DO PROJETO DA CIDADE (MODO EDICAO) -->
            <div id="panelEdicaoGIS" style="display:none;">
                <div class="sidebar-section" style="background:#FEF3C7; border-left:4px solid var(--edit-amber);">
                    <h4 style="color:var(--edit-amber-dark); margin-bottom:4px;">✏ Edição do Projeto da Cidade</h4>
                    <p style="font-size:0.75rem; color:#78350F;">
                        As alterações feitas aqui modificam o <b>Projeto Oficial da Cidade</b>. Novos trechos passam a ser cobrados e auditados no Mapa de Calor.
                    </p>
                </div>

                <div class="sidebar-section">
                    <h4>Atributos do Trecho no Projeto</h4>
                    
                    <div class="sidebar-field">
                        <label>ID do Segmento no Projeto:</label>
                        <input type="text" id="editId" readonly style="background:#F1F5F9; color:#64748B;" value="Nenhum selecionado" />
                    </div>

                    <div class="sidebar-field">
                        <label for="editNomeRua">Nome do Logradouro / Rua:</label>
                        <input type="text" id="editNomeRua" placeholder="Ex: Rua Nova Esperança" />
                    </div>

                    <div class="sidebar-field">
                        <label for="editSetor">Setor do Projeto:</label>
                        <select id="editSetor"></select>
                    </div>

                    <div class="sidebar-field">
                        <label for="editTipo">Tipo de Coleta no Projeto:</label>
                        <select id="editTipo">
                            <option value="COLETA">COLETA REGULAR (Mandatory)</option>
                            <option value="MANUAL">COLETA MANUAL (Beco/Escadaria)</option>
                            <option value="DESLOCAMENTO">DESLOCAMENTO (Acesso)</option>
                        </select>
                    </div>

                    <div class="sidebar-field" style="display:flex; align-items:center; gap:8px; margin-top:8px;">
                        <input type="checkbox" id="editIsPriority" style="width:auto;" />
                        <label for="editIsPriority" style="margin-bottom:0; cursor:pointer;">🌟 Rua Prioritária (Avenida / Comércio)</label>
                    </div>

                    <div class="sidebar-field" style="margin-top:10px;">
                        <label>Extensão Calculada:</label>
                        <div style="font-size:1.0rem; font-weight:700; color:var(--text-dark);" id="editExtensao">0.0 metros</div>
                    </div>

                    <div style="display:flex; flex-direction:column; gap:8px; margin-top:16px;">
                        <button class="btn-locar" id="btnSalvarTrecho" style="width:100%; justify-content:center; background-color:var(--locar-green);">
                            💾 Salvar no Projeto da Cidade
                        </button>
                        <button class="btn-outline" id="btnExcluirTrecho" style="width:100%; justify-content:center; color:var(--danger-red); border-color:#FECACA;">
                            🗑 Excluir este Trecho do Projeto
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <!-- LOADING OVERLAY -->
        <div id="loadingOverlay">
            <div class="spinner"></div>
            <div class="loading-text" id="loadingText">Carregando...</div>
        </div>
    </div>

    <!-- MODAL DE COMPARTILHAMENTO DE LINK PERSONALIZADO (READ-ONLY) -->
    <div id="modalShareLink" class="modal-backdrop" style="display:none;">
        <div class="modal-card">
            <div class="modal-header">
                <div style="display:flex; align-items:center; gap:10px;">
                    <div class="modal-icon-badge">🔗</div>
                    <div>
                        <h3 class="modal-title">Gerar Link Personalizado de Visualização</h3>
                        <p class="modal-subtitle">Crie um link seguro para visualização com filtros pré-selecionados</p>
                    </div>
                </div>
                <button type="button" class="modal-close-btn" id="btnCloseModalShare">&times;</button>
            </div>
            
            <div class="modal-body">
                <div class="security-callout">
                    <div style="font-weight:700; color:#1D4ED8; display:flex; align-items:center; gap:6px;">
                        <span>🔒 Modo Somente Leitura Garantido</span>
                    </div>
                    <p style="margin:4px 0 0 0; font-size:0.75rem; color:#1E293B; line-height:1.4;">
                        Quem acessar este link poderá <b>visualizar</b> o mapa de calor, alternar filtros de coleta e setores, mas o <b>Modo de Edição do Projeto estará 100% oculto e bloqueado</b>.
                    </p>
                </div>

                <div class="modal-form-grid">
                    <div class="sidebar-field">
                        <label>Cidade Inicial do Link:</label>
                        <select id="modalShareCidade"></select>
                    </div>
                    <div class="sidebar-field">
                        <label>Data do Turno:</label>
                        <input type="date" id="modalShareData" />
                    </div>
                </div>

                <div class="sidebar-field" style="margin-top:10px;">
                    <label>Setores incluídos na visão inicial:</label>
                    <div id="modalShareSetoresResumo" class="share-sectors-summary">
                        Todos os setores da cidade selecionada
                    </div>
                </div>

                <div class="sidebar-field" style="margin-top:10px;">
                    <label>Filtro de Coleta Inicial:</label>
                    <select id="modalShareColeta">
                        <option value="all">Todos os Trechos</option>
                        <option value="not_collected">Apenas Ruas Omitidas (Críticas)</option>
                        <option value="collected">Apenas Ruas Coletadas</option>
                        <option value="possible_manual">Apenas Coleta Manual</option>
                        <option value="possible_gps_gap">Apenas Falhas de GPS</option>
                    </select>
                </div>

                <div class="sidebar-field" style="margin-top:10px;">
                    <label>Cidades permitidas para navegação no link:</label>
                    <div id="modalShareCidadesCheckboxList" class="cities-checkbox-grid">
                        <!-- Checkboxes de cidades gerados via JS -->
                    </div>
                    <div class="section-hint">Você pode restringir para apenas 1 cidade ou marcar mais de uma (ex: Jaboatão e Cabo).</div>
                </div>

                <div class="sidebar-field" style="margin-top:14px;">
                    <label>Link Personalizado Gerado:</label>
                    <div class="share-input-group">
                        <input type="text" id="inputGeneratedShareUrl" readonly class="share-url-input" />
                        <button type="button" class="btn-locar" id="btnCopyGeneratedUrl">
                            <span>📋 Copiar</span>
                        </button>
                    </div>
                </div>
            </div>

            <div class="modal-footer">
                <button type="button" class="btn-outline" id="btnTestOpenShareUrl">↗ Abrir em Nova Aba</button>
                <button type="button" class="btn-outline" id="btnCancelShareModal">Fechar</button>
            </div>
        </div>
    </div>

    <!-- TOAST FLUTUANTE DE NOTIFICAÇÃO -->
    <div id="locarToast" class="locar-toast">✔ Link de visualização copiado para a área de transferência!</div>

    <!-- Leaflet JS -->
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        let map;
        let geojsonLayer = null;
        let sectorLabelsLayer = null;
        let priorityHaloLayer = null;
        
        let drawStartMarker = null;
        let drawEndMarker = null;
        let drawActivePolyline = null;
        let drawGuidePolyline = null;
        let drawingCoordinates = [];
        
        let currentAudit = null;
        let currentFeatures = [];
        let setoresList = [];
        let selectedSectors = new Set();
        let isReadOnlyMode = false;
        let showSectorLabels = true;
        let showPriorityHighlight = true;
        let isEditMode = false;
        let selectedFeature = null;
        let activeGisTool = 'select';

        function checkUrlParams() {
            const urlParams = new URLSearchParams(window.location.search);
            
            // 1. Modo Somente Leitura (Bloqueia totalmente o modo de edição)
            if (urlParams.get('readonly') === '1' || urlParams.get('modo') === 'view' || urlParams.get('view') === '1') {
                isReadOnlyMode = true;
                const btnEdit = document.getElementById('btnToggleEditMode');
                if (btnEdit) btnEdit.style.display = 'none';
                const gisToolbar = document.getElementById('gisToolbar');
                if (gisToolbar) gisToolbar.style.display = 'none';
                const panelEdicao = document.getElementById('panelEdicaoGIS');
                if (panelEdicao) panelEdicao.style.display = 'none';
                const badgeReadOnly = document.getElementById('badgeReadOnly');
                if (badgeReadOnly) badgeReadOnly.style.display = 'inline-flex';
                const textShare = document.getElementById('textShareLink');
                if (textShare) textShare.innerText = "Link Desta Visão";
            }

            // 2. Filtro de Cidades permitidas no link
            if (urlParams.has('cidades')) {
                const permitidas = urlParams.get('cidades').split(',').map(c => c.trim().toLowerCase()).filter(Boolean);
                if (permitidas.length > 0) {
                    const selectCidade = document.getElementById('selectCidade');
                    const opts = Array.from(selectCidade.options);
                    opts.forEach(opt => {
                        if (!permitidas.includes(opt.value.toLowerCase())) {
                            opt.remove();
                        }
                    });
                }
            }

            // 3. Cidade Selecionada
            if (urlParams.has('cidade')) {
                const c = urlParams.get('cidade').toLowerCase();
                const selectCidade = document.getElementById('selectCidade');
                if (selectCidade) selectCidade.value = c;
            }

            // 4. Data
            if (urlParams.has('data')) {
                const d = urlParams.get('data');
                const inputData = document.getElementById('inputData');
                if (inputData) inputData.value = d;
            }

            // 5. Filtro de Coleta
            if (urlParams.has('coleta')) {
                const col = urlParams.get('coleta');
                const filterColeta = document.getElementById('filterColeta');
                if (filterColeta) filterColeta.value = col;
            }

            // 6. Setores Iniciais
            if (urlParams.has('setores')) {
                const raw = urlParams.get('setores');
                const parts = raw.split(',').map(s => s.trim()).filter(Boolean);
                selectedSectors = new Set(parts);
            }
        }

        function initMap() {
            map = L.map('map', {
                preferCanvas: true,
                zoomControl: true
            }).setView([-8.1930, -34.9364], 12);

            // Pane de alta precisão para a borda externa azul das prioritárias
            // zIndex 390 garante que fica ESTRITAMENTE ABAIXO do traçado de status da coleta (zIndex 400)
            map.createPane('priorityHaloPane');
            map.getPane('priorityHaloPane').style.zIndex = 390;
            map.getPane('priorityHaloPane').style.pointerEvents = 'none';

            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap · Locar Saneamento',
                maxZoom: 19
            }).addTo(map);

            priorityHaloLayer = L.featureGroup().addTo(map);
            sectorLabelsLayer = L.layerGroup().addTo(map);

            map.on('click', onMapClickForDrawing);
            map.on('mousemove', onMapMouseMoveForDrawing);
        }

        async function carregarAuditoria(forcar = false) {
            const cidade = document.getElementById('selectCidade').value;
            const data = document.getElementById('inputData').value;
            
            document.getElementById('loadingOverlay').style.display = 'flex';
            document.getElementById('loadingText').innerText = `Carregando Projeto (${cidade.toUpperCase()})...`;

            try {
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
            const selectVeiculo = document.getElementById('filterVeiculo');
            const editSetor = document.getElementById('editSetor');
            const multiSetorList = document.getElementById('multiSetorList');

            // Setores no Editor GIS
            if (editSetor) {
                editSetor.innerHTML = '';
                setoresList.forEach(s => {
                    const opt = document.createElement('option');
                    opt.value = s.codigo;
                    opt.textContent = s.codigo;
                    editSetor.appendChild(opt);
                });
            }

            // Setores no Multi-Select Dropdown
            if (multiSetorList) {
                multiSetorList.innerHTML = '';
                setoresList.forEach(s => {
                    const isChecked = (selectedSectors.size === 0 || selectedSectors.has(s.codigo));
                    const item = document.createElement('label');
                    item.className = 'multi-select-item' + (isChecked && selectedSectors.size > 0 && selectedSectors.size < setoresList.length ? ' selected' : '');
                    item.dataset.code = s.codigo.toLowerCase();
                    item.innerHTML = `
                        <input type="checkbox" value="${s.codigo}" ${isChecked ? 'checked' : ''} />
                        <div class="sector-item-info">
                            <span class="sector-code">${s.codigo}</span>
                            <span class="sector-meta">${s.total_vias || 0} vias · ${s.extensao_km || 0} km</span>
                        </div>
                    `;
                    const chk = item.querySelector('input');
                    chk.addEventListener('change', () => onSectorCheckboxChange());
                    multiSetorList.appendChild(item);
                });
                atualizarTriggerSetores();
            }

            // Veículos
            selectVeiculo.innerHTML = '<option value="all">Todos os veículos</option>';
            const placas = dataRes.resumo.placas_ativas || [];
            placas.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p;
                opt.textContent = `Apenas ${p}`;
                selectVeiculo.appendChild(opt);
            });
        }

        // ====================================================================
        // FUNÇÕES DO COMPONENTE MULTI-SELECT DE SETORES
        // ====================================================================
        function toggleMultiSetorDropdown(forceClose = false) {
            const dd = document.getElementById('multiSetorDropdown');
            const btn = document.getElementById('btnMultiSetorTrigger');
            if (!dd) return;
            if (forceClose || dd.style.display === 'block') {
                dd.style.display = 'none';
                if (btn) btn.classList.remove('active');
            } else {
                dd.style.display = 'block';
                if (btn) btn.classList.add('active');
                const search = document.getElementById('inputSearchSector');
                if (search) {
                    search.value = '';
                    filtrarItensSetor('');
                    search.focus();
                }
            }
        }

        function filtrarItensSetor(termo) {
            const items = document.querySelectorAll('#multiSetorList .multi-select-item');
            const q = termo.toLowerCase().trim();
            items.forEach(it => {
                if (!q || (it.dataset.code && it.dataset.code.includes(q))) {
                    it.style.display = 'flex';
                } else {
                    it.style.display = 'none';
                }
            });
        }

        function onSectorCheckboxChange() {
            const checkboxes = document.querySelectorAll('#multiSetorList input[type="checkbox"]');
            const marcados = [];
            checkboxes.forEach(c => {
                if (c.checked) marcados.push(c.value);
                const parent = c.closest('.multi-select-item');
                if (parent) {
                    if (c.checked && marcados.length < checkboxes.length) {
                        parent.classList.add('selected');
                    } else {
                        parent.classList.remove('selected');
                    }
                }
            });

            if (marcados.length === 0 || marcados.length === checkboxes.length) {
                selectedSectors.clear();
            } else {
                selectedSectors = new Set(marcados);
            }

            atualizarTriggerSetores();
            aplicarFiltros();
        }

        function atualizarTriggerSetores() {
            const labelEl = document.getElementById('multiSetorLabel');
            const pillEl = document.getElementById('sectorSelectedCount');
            const total = setoresList.length;
            if (!labelEl || !pillEl) return;

            if (selectedSectors.size === 0 || selectedSectors.size === total) {
                labelEl.innerText = "Todos os setores";
                pillEl.innerText = `${total} setores`;
                pillEl.classList.remove('active');
            } else if (selectedSectors.size === 1) {
                const cod = Array.from(selectedSectors)[0];
                labelEl.innerText = `Apenas ${cod}`;
                pillEl.innerText = "1 setor";
                pillEl.classList.add('active');
            } else {
                labelEl.innerText = `${selectedSectors.size} setores selecionados`;
                pillEl.innerText = `${selectedSectors.size} de ${total}`;
                pillEl.classList.add('active');
            }
        }

        function selectAllSectors() {
            const checkboxes = document.querySelectorAll('#multiSetorList input[type="checkbox"]');
            checkboxes.forEach(c => {
                c.checked = true;
                const parent = c.closest('.multi-select-item');
                if (parent) parent.classList.remove('selected');
            });
            selectedSectors.clear();
            atualizarTriggerSetores();
            aplicarFiltros();
        }

        function clearSectors() {
            const checkboxes = document.querySelectorAll('#multiSetorList input[type="checkbox"]');
            checkboxes.forEach(c => {
                c.checked = false;
                const parent = c.closest('.multi-select-item');
                if (parent) parent.classList.remove('selected');
            });
            selectedSectors.clear();
            // Marca todos para evitar tela vazia e restaurar visão global
            checkboxes.forEach(c => { c.checked = true; });
            atualizarTriggerSetores();
            aplicarFiltros();
        }

        function sincronizarCheckboxesSetor() {
            const checkboxes = document.querySelectorAll('#multiSetorList input[type="checkbox"]');
            checkboxes.forEach(c => {
                const shouldCheck = (selectedSectors.size === 0 || selectedSectors.has(c.value));
                c.checked = shouldCheck;
                const parent = c.closest('.multi-select-item');
                if (parent) {
                    if (shouldCheck && selectedSectors.size > 0 && selectedSectors.size < setoresList.length) {
                        parent.classList.add('selected');
                    } else {
                        parent.classList.remove('selected');
                    }
                }
            });
            atualizarTriggerSetores();
        }

        function renderizarResultado() {
            const r = currentAudit.resumo;
            document.getElementById('inlogStatusText').innerText = r.status_inlog;

            renderizarRotulosSetores();
            aplicarFiltros();
        }

        // ====================================================================
        // RÓTULOS DOS SETORES: SUTIS (HOVER ONLY) & SELEÇÃO DE SETOR
        // ====================================================================
        function renderizarRotulosSetores() {
            sectorLabelsLayer.clearLayers();
            if (!showSectorLabels || !setoresList) return;

            setoresList.forEach(s => {
                if (s.centro && s.centro[0] !== 0) {
                    const isDestaque = (selectedSectors.size > 0 && selectedSectors.has(s.codigo));
                    const htmlContent = `
                        <div class="sector-badge-container ${isDestaque ? 'destaque' : ''}">
                            <div class="sector-dot-marker"></div>
                            <div class="sector-pill-label">${s.codigo}</div>
                        </div>
                    `;
                    const icon = L.divIcon({
                        className: 'sector-custom-marker',
                        html: htmlContent,
                        iconSize: [20, 20],
                        iconAnchor: [10, 10]
                    });
                    const marker = L.marker(s.centro, { icon: icon });
                    marker.on('click', () => {
                        if (selectedSectors.has(s.codigo) && selectedSectors.size === 1) {
                            selectedSectors.clear();
                        } else {
                            selectedSectors.clear();
                            selectedSectors.add(s.codigo);
                        }
                        sincronizarCheckboxesSetor();
                        aplicarFiltros();
                    });
                    sectorLabelsLayer.addLayer(marker);
                }
            });
        }

        function aplicarFiltros() {
            const filtroColeta = document.getElementById('filterColeta').value;
            const filtroVeiculo = document.getElementById('filterVeiculo').value;

            let featuresFiltradas = [];
            let totalM = 0, coletadoM = 0, omissaoM = 0;
            let cCol = 0, cOmi = 0, cMan = 0, cGps = 0, cPrio = 0;
            let veiculosNoFiltro = new Set();

            currentFeatures.forEach(f => {
                const p = f.properties;

                if (!isEditMode) {
                    if (filtroColeta !== 'all' && p.status !== filtroColeta) return;
                    if (selectedSectors.size > 0 && !selectedSectors.has(p.sector)) return;
                    if (filtroVeiculo !== 'all' && (!p.vehicles || !p.vehicles.includes(filtroVeiculo))) return;
                }

                featuresFiltradas.push(f);
                const len = p.len_m || 0;
                totalM += len;

                if (p.status === 'collected') { coletadoM += len; cCol++; }
                else if (p.status === 'not_collected') { omissaoM += len; cOmi++; }
                else if (p.status === 'possible_manual') { coletadoM += len; cMan++; }
                else if (p.status === 'possible_gps_gap') { cGps++; }

                if (p.is_priority) cPrio++;
                if (p.vehicles) p.vehicles.forEach(v => veiculosNoFiltro.add(v));
            });

            // Atualiza KPIs
            const pct = totalM > 0 ? (coletadoM / totalM * 100) : 0;
            document.getElementById('kpiCobertura').innerText = pct.toFixed(2) + ' %';
            document.getElementById('kpiExtensao').innerText = (coletadoM / 1000).toFixed(2) + ' km coletados';
            document.getElementById('kpiTotalKm').innerText = (totalM / 1000).toFixed(2) + ' km';
            document.getElementById('kpiTotalVias').innerText = featuresFiltradas.length + ' trechos no projeto';
            document.getElementById('kpiOmitidas').innerText = cOmi + ' vias';
            document.getElementById('kpiOmitidasKm').innerText = (omissaoM / 1000).toFixed(2) + ' km pendentes';
            document.getElementById('kpiCarros').innerText = veiculosNoFiltro.size;

            document.getElementById('legColetada').innerText = cCol;
            document.getElementById('legNaoColetada').innerText = cOmi;
            document.getElementById('legManual').innerText = cMan;
            document.getElementById('legGps').innerText = cGps;
            const legPrioEl = document.getElementById('legPrioritaria');
            if (legPrioEl) legPrioEl.innerText = cPrio;

            // ================================================================
            // CONTORNO AZUL SAFIRA NÍTIDO PARA RUAS PRIORITÁRIAS
            // Renderizado em priorityHaloPane (zIndex 390), estritamente por baixo
            // da linha de status viário (zIndex 400), eliminando borrões ou sobreposições
            // ================================================================
            priorityHaloLayer.clearLayers();
            if (showPriorityHighlight) {
                featuresFiltradas.forEach(f => {
                    if (f.properties.is_priority) {
                        const coords = f.geometry.coordinates.map(pt => [pt[1], pt[0]]);
                        L.polyline(coords, {
                            pane: 'priorityHaloPane',
                            color: '#1D4ED8', // Azul Cobalto Real / Safira Intenso
                            weight: 7.5,      // Borda nítida de ~1.75px em volta da linha interna de 4.0px
                            opacity: 1.0,     // Totalmente nítido, sem transparências, sem borrões
                            lineCap: 'round',
                            lineJoin: 'round'
                        }).addTo(priorityHaloLayer);
                    }
                });
            }

            // Renderiza as ruas no mapa
            if (geojsonLayer) map.removeLayer(geojsonLayer);

            geojsonLayer = L.geoJSON({ type: 'FeatureCollection', features: featuresFiltradas }, {
                style: function(feat) {
                    const p = feat.properties;
                    let cor = '#00A859';
                    if (p.status === 'not_collected') cor = '#E53935';
                    else if (p.status === 'possible_manual') cor = '#FB8C00';
                    else if (p.status === 'possible_gps_gap') cor = '#FBC02D';

                    // Ruas normais: 3.0px (limpa e fina)
                    // Ruas prioritárias: 4.0px (mais visível e forte no mapa)
                    let weight = (p.is_priority && showPriorityHighlight) ? 4.0 : 3.0;
                    if (selectedFeature && selectedFeature.properties.id === p.id) {
                        cor = '#7C3AED'; // Roxo vibrante para seleção em edição
                        weight = 6.0;
                    }

                    return {
                        color: cor,
                        weight: weight,
                        opacity: 1.0,
                        lineCap: 'round',
                        lineJoin: 'round'
                    };
                },
                onEachFeature: function(feat, layer) {
                    const p = feat.properties;
                    const prioridadeTxt = p.is_priority ? ' · 🌟 Prioritária' : '';
                    layer.bindTooltip(`<b>${p.street_name}</b> · ${p.sector}${prioridadeTxt}`, { sticky: true });

                    layer.on('click', function(e) {
                        if (isReadOnlyMode) return;
                        if (isEditMode && activeGisTool === 'draw') {
                            L.DomEvent.stopPropagation(e);
                            processarCliqueDesenho(e.latlng);
                            return;
                        }
                        selecionarSegmento(feat);
                    });
                }
            }).addTo(map);

            // Zoom automático nos setores selecionados
            if (selectedSectors.size === 1) {
                const sCod = Array.from(selectedSectors)[0];
                const sInfo = setoresList.find(s => s.codigo === sCod);
                if (sInfo && sInfo.centro && sInfo.centro[0] !== 0) {
                    map.setView(sInfo.centro, 14, { animate: true });
                }
            } else if (selectedSectors.size > 1) {
                const pts = [];
                setoresList.forEach(s => {
                    if (selectedSectors.has(s.codigo) && s.centro && s.centro[0] !== 0) {
                        pts.push(s.centro);
                    }
                });
                if (pts.length > 0) {
                    const bounds = L.latLngBounds(pts);
                    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
                }
            }
            renderizarRotulosSetores();
        }

        // ====================================================================
        // SISTEMA DE CRIAÇÃO E TRAÇADO DE RUAS NO PROJETO
        // ====================================================================
        function toggleEditMode() {
            isEditMode = !isEditMode;
            const btn = document.getElementById('btnToggleEditMode');
            const panelAuditoria = document.getElementById('panelAuditoria');
            const panelEdicao = document.getElementById('panelEdicaoGIS');
            const gisToolbar = document.getElementById('gisToolbar');

            if (isEditMode) {
                btn.classList.add('active');
                panelAuditoria.style.display = 'none';
                panelEdicao.style.display = 'block';
                gisToolbar.style.display = 'flex';
                setGisTool('select');
            } else {
                btn.classList.remove('active');
                panelAuditoria.style.display = 'block';
                panelEdicao.style.display = 'none';
                gisToolbar.style.display = 'none';
                cancelarDesenho();
                aplicarFiltros();
            }
        }

        function setGisTool(tool) {
            activeGisTool = tool;
            ['toolSelect', 'toolDrawLine', 'toolDelete'].forEach(id => {
                const b = document.getElementById(id);
                if (b) b.classList.remove('active');
            });

            const instrBox = document.getElementById('drawInstructionBox');

            if (tool === 'select') {
                document.getElementById('toolSelect').classList.add('active');
                document.getElementById('map').style.cursor = 'default';
                instrBox.style.display = 'none';
                cancelarDesenho();
            }
            else if (tool === 'draw') {
                document.getElementById('toolDrawLine').classList.add('active');
                document.getElementById('map').style.cursor = 'crosshair';
                iniciarModoDesenho();
            }
            else if (tool === 'delete') {
                document.getElementById('toolDelete').classList.add('active');
                excluirTrecho();
            }
        }

        function iniciarModoDesenho() {
            cancelarDesenho();
            const instrBox = document.getElementById('drawInstructionBox');
            const instrText = document.getElementById('drawInstructionText');
            instrBox.style.display = 'flex';
            instrText.innerHTML = '📍 <b>1º Clique:</b> Marque o INÍCIO da nova rua (pode clicar em cima de uma rua existente).';
            
            document.getElementById('editId').value = "NOVA RUA DO PROJETO";
            document.getElementById('editNomeRua').value = "";
            document.getElementById('editNomeRua').placeholder = "Digite o nome da nova rua aqui";
            document.getElementById('editExtensao').innerText = "Aguardando traçado...";
        }

        function onMapClickForDrawing(e) {
            if (!isEditMode || activeGisTool !== 'draw') return;
            processarCliqueDesenho(e.latlng);
        }

        function processarCliqueDesenho(latlng) {
            const instrText = document.getElementById('drawInstructionText');

            if (drawingCoordinates.length === 0) {
                drawingCoordinates.push([latlng.lat, latlng.lng]);

                drawStartMarker = L.circleMarker([latlng.lat, latlng.lng], {
                    radius: 7,
                    fillColor: '#00A859',
                    color: '#FFFFFF',
                    weight: 2,
                    fillOpacity: 1
                }).addTo(map).bindTooltip("Início da Nova Rua", { permanent: true, direction: 'top' });

                instrText.innerHTML = '📍 <b>2º Clique:</b> Clique no ponto de FIM da rua para traçar o trajeto.';
            }
            else {
                drawingCoordinates.push([latlng.lat, latlng.lng]);

                if (drawGuidePolyline) {
                    map.removeLayer(drawGuidePolyline);
                    drawGuidePolyline = null;
                }

                if (drawActivePolyline) map.removeLayer(drawActivePolyline);
                drawActivePolyline = L.polyline(drawingCoordinates, {
                    color: '#2563EB',
                    weight: 5,
                    opacity: 1.0,
                    dashArray: '6, 6'
                }).addTo(map);

                if (drawEndMarker) map.removeLayer(drawEndMarker);
                drawEndMarker = L.circleMarker([latlng.lat, latlng.lng], {
                    radius: 7,
                    fillColor: '#E53935',
                    color: '#FFFFFF',
                    weight: 2,
                    fillOpacity: 1
                }).addTo(map).bindTooltip("Fim da Rua", { permanent: true, direction: 'bottom' });

                const metros = calcularDistanciaMetros(drawingCoordinates);
                document.getElementById('editExtensao').innerText = metros.toFixed(1) + ' metros';

                instrText.innerHTML = '✔ <b>Traçado Concluído!</b> Preencha os atributos ao lado e clique em <b>"Salvar no Projeto da Cidade"</b>.';
                document.getElementById('editNomeRua').focus();
            }
        }

        function onMapMouseMoveForDrawing(e) {
            if (!isEditMode || activeGisTool !== 'draw' || drawingCoordinates.length === 0) return;

            const ultimoPonto = drawingCoordinates[drawingCoordinates.length - 1];
            const coordsGuia = [ultimoPonto, [e.latlng.lat, e.latlng.lng]];

            if (drawGuidePolyline) {
                drawGuidePolyline.setLatLngs(coordsGuia);
            } else {
                drawGuidePolyline = L.polyline(coordsGuia, {
                    color: '#2563EB',
                    weight: 2.5,
                    dashArray: '4, 6',
                    opacity: 0.8
                }).addTo(map);
            }
        }

        function cancelarDesenho() {
            if (drawStartMarker) map.removeLayer(drawStartMarker);
            if (drawEndMarker) map.removeLayer(drawEndMarker);
            if (drawActivePolyline) map.removeLayer(drawActivePolyline);
            if (drawGuidePolyline) map.removeLayer(drawGuidePolyline);

            drawStartMarker = null;
            drawEndMarker = null;
            drawActivePolyline = null;
            drawGuidePolyline = null;
            drawingCoordinates = [];

            document.getElementById('drawInstructionBox').style.display = 'none';
        }

        function calcularDistanciaMetros(coords) {
            if (coords.length < 2) return 0.0;
            let total = 0.0;
            for (let i = 0; i < coords.length - 1; i++) {
                const p1 = L.latLng(coords[i][0], coords[i][1]);
                const p2 = L.latLng(coords[i+1][0], coords[i+1][1]);
                total += p1.distanceTo(p2);
            }
            return total;
        }

        function selecionarSegmento(feat) {
            selectedFeature = feat;
            const p = feat.properties;

            document.getElementById('editId').value = p.id;
            document.getElementById('editNomeRua').value = p.street_name;
            document.getElementById('editSetor').value = p.sector;
            document.getElementById('editTipo').value = p.tipo || (p.is_manual ? 'MANUAL' : 'COLETA');
            document.getElementById('editIsPriority').checked = p.is_priority || false;
            document.getElementById('editExtensao').innerText = p.len_m + ' metros';

            cancelarDesenho();
            aplicarFiltros();
        }

        // ====================================================================
        // PERSISTÊNCIA NO PROJETO DA CIDADE & BANCO SQLITE
        // ====================================================================
        async function salvarAlteracoesNoProjeto() {
            const cidade = document.getElementById('selectCidade').value;
            const idVal = document.getElementById('editId').value;
            const nomeRua = document.getElementById('editNomeRua').value.trim();
            const setor = document.getElementById('editSetor').value;
            const tipo = document.getElementById('editTipo').value;
            const isPriority = document.getElementById('editIsPriority').checked;

            if (!nomeRua) {
                alert('Por favor, informe o nome do logradouro / rua.');
                document.getElementById('editNomeRua').focus();
                return;
            }

            let payload = {
                contrato_id: cidade,
                nome_rua: nomeRua,
                setor: setor,
                tipo: tipo,
                is_manual: (tipo === 'MANUAL'),
                is_priority: isPriority
            };

            const isNovaRua = (idVal === "NOVA RUA DO PROJETO" || idVal === "Nenhum selecionado" || !idVal);

            if (!isNovaRua) {
                payload.id = parseInt(idVal);
            } else {
                if (drawingCoordinates.length < 2) {
                    return alert('Por favor, clique em "Desenhar Rua" e marque o início e o fim da rua no mapa.');
                }
                payload.coordinates = drawingCoordinates.map(pt => [pt[1], pt[0]]);
            }

            document.getElementById('loadingOverlay').style.display = 'flex';
            document.getElementById('loadingText').innerText = 'Salvando no Projeto da Cidade e recalculando malha...';

            try {
                const resp = await fetch('/api/segmento/salvar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const res = await resp.json();

                if (res.sucesso) {
                    alert('✔ Sucesso! O Projeto da Cidade foi atualizado. A nova rua agora faz parte oficial da malha e é auditada no Mapa de Calor.');
                    cancelarDesenho();
                    setGisTool('select');
                    await carregarAuditoria(true);
                } else {
                    alert('Erro ao salvar no projeto: ' + (res.erro || 'Falha desconhecida'));
                }
            } catch (err) {
                console.error(err);
                alert('Erro de comunicação ao salvar.');
            } finally {
                document.getElementById('loadingOverlay').style.display = 'none';
            }
        }

        async function excluirTrecho() {
            const idVal = document.getElementById('editId').value;
            if (!idVal || idVal === "NOVA RUA DO PROJETO" || idVal === "Nenhum selecionado") {
                return alert('Clique em uma rua do mapa com a ferramenta "Selecionar" antes de apagar.');
            }

            if (!confirm(`Deseja realmente remover o trecho ID ${idVal} do Projeto da Cidade?`)) return;

            document.getElementById('loadingOverlay').style.display = 'flex';
            try {
                const resp = await fetch('/api/segmento/excluir', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: parseInt(idVal) })
                });
                const res = await resp.json();

                if (res.sucesso) {
                    alert('✔ Trecho removido com sucesso do Projeto da Cidade!');
                    selectedFeature = null;
                    document.getElementById('editId').value = "Nenhum selecionado";
                    await carregarAuditoria(true);
                }
            } catch (err) {
                console.error(err);
                alert('Erro ao excluir do projeto.');
            } finally {
                document.getElementById('loadingOverlay').style.display = 'none';
            }
        }

        // ====================================================================
        // EVENTOS PRINCIPAIS
        // ====================================================================
        document.getElementById('btnToggleEditMode').addEventListener('click', toggleEditMode);
        document.getElementById('toolSelect').addEventListener('click', () => setGisTool('select'));
        document.getElementById('toolDrawLine').addEventListener('click', () => setGisTool('draw'));
        document.getElementById('toolDelete').addEventListener('click', () => setGisTool('delete'));
        document.getElementById('btnCancelDrawing').addEventListener('click', () => setGisTool('select'));
        document.getElementById('toolSyncInlog').addEventListener('click', () => {
            alert('Projeto da Cidade sincronizado com o banco e preparado para exportação Inlog!');
        });
        document.getElementById('btnSalvarTrecho').addEventListener('click', salvarAlteracoesNoProjeto);
        document.getElementById('btnExcluirTrecho').addEventListener('click', excluirTrecho);

        document.getElementById('filterColeta').addEventListener('change', aplicarFiltros);
        document.getElementById('filterVeiculo').addEventListener('change', aplicarFiltros);

        // VISUALIZAÇÃO: CÓDIGOS DOS SETORES
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

        // VISUALIZAÇÃO: RUAS PRIORITÁRIAS (BORDA AZUL SAFIRA DE PRECISÃO)
        document.getElementById('btnShowPriority').addEventListener('click', function() {
            showPriorityHighlight = true;
            this.classList.add('active');
            document.getElementById('btnHidePriority').classList.remove('active');
            aplicarFiltros();
        });
        document.getElementById('btnHidePriority').addEventListener('click', function() {
            showPriorityHighlight = false;
            this.classList.add('active');
            document.getElementById('btnShowPriority').classList.remove('active');
            aplicarFiltros();
        });

        document.getElementById('btnConsultar').addEventListener('click', () => carregarAuditoria(true));
        document.getElementById('selectCidade').addEventListener('change', () => carregarAuditoria(false));
        document.getElementById('inputData').addEventListener('change', () => carregarAuditoria(false));
        document.getElementById('btnBaixarCsv').addEventListener('click', () => {
            if (!currentAudit || !currentAudit.resumo) return alert('Nenhuma auditoria processada para exportar.');
            window.location.href = `/api/exportar-csv?id=${currentAudit.resumo.id}`;
        });

        // ====================================================================
        // EVENTOS: MULTI-SELECT DE SETORES
        // ====================================================================
        document.getElementById('btnMultiSetorTrigger').addEventListener('click', (e) => {
            e.stopPropagation();
            toggleMultiSetorDropdown();
        });

        document.getElementById('btnSelectAllSectors').addEventListener('click', (e) => {
            e.stopPropagation();
            selectAllSectors();
        });

        document.getElementById('btnClearSectors').addEventListener('click', (e) => {
            e.stopPropagation();
            clearSectors();
        });

        document.getElementById('inputSearchSector').addEventListener('input', function(e) {
            e.stopPropagation();
            filtrarItensSetor(this.value);
        });

        document.getElementById('inputSearchSector').addEventListener('keydown', (e) => e.stopPropagation());

        // Fechar dropdown ao clicar fora
        document.addEventListener('click', (e) => {
            const container = document.getElementById('multiSetorContainer');
            if (container && !container.contains(e.target)) {
                toggleMultiSetorDropdown(true);
            }
        });

        // ====================================================================
        // EVENTOS: MODAL GERAR LINK PERSONALIZADO
        // ====================================================================
        const CIDADES_MAPA = {
            'jaboatao':      'Jaboatão dos Guararapes',
            'cabo':          'Cabo de Santo Agostinho',
            'olinda':        'Olinda',
            'paulista':      'Paulista',
            'caruaru':       'Caruaru',
            'cuiaba':        'Cuiabá',
            'garanhuns':     'Garanhuns',
            'pesqueira':     'Pesqueira',
            'vitoria':       'Vitória de Santo Antão',
            'canela':        'Canela',
            'jequie':        'Jequié',
            'novo hamburgo': 'Novo Hamburgo'
        };

        function abrirModalShare() {
            const modal = document.getElementById('modalShareLink');
            if (!modal) return;

            // Preencher select de cidade inicial com as cidades disponíveis no select principal
            const modalSelectCidade = document.getElementById('modalShareCidade');
            const mainSelectCidade = document.getElementById('selectCidade');
            modalSelectCidade.innerHTML = '';
            Array.from(mainSelectCidade.options).forEach(opt => {
                const o = document.createElement('option');
                o.value = opt.value;
                o.textContent = opt.textContent;
                if (opt.selected) o.selected = true;
                modalSelectCidade.appendChild(o);
            });

            // Preencher data
            document.getElementById('modalShareData').value =
                document.getElementById('inputData').value;

            // Preencher checkboxes de cidades permitidas (todos marcados por default)
            const cidadesGrid = document.getElementById('modalShareCidadesCheckboxList');
            cidadesGrid.innerHTML = '';
            Object.entries(CIDADES_MAPA).forEach(([id, nome]) => {
                const lbl = document.createElement('label');
                lbl.className = 'city-checkbox-item';
                lbl.innerHTML = `<input type="checkbox" value="${id}" checked /><span>${nome}</span>`;
                cidadesGrid.appendChild(lbl);
            });

            // Resumo dos setores selecionados atualmente
            const setoresResumoEl = document.getElementById('modalShareSetoresResumo');
            if (selectedSectors.size === 0 || selectedSectors.size === setoresList.length) {
                setoresResumoEl.innerText = 'Todos os setores da cidade';
            } else {
                setoresResumoEl.innerText = Array.from(selectedSectors).join(', ');
            }

            // Coleta inicial
            document.getElementById('modalShareColeta').value =
                document.getElementById('filterColeta').value;

            gerarUrlShare();
            modal.style.display = 'flex';
        }

        function gerarUrlShare() {
            const base = window.location.origin;
            const cidade = document.getElementById('modalShareCidade').value;
            const data = document.getElementById('modalShareData').value;
            const coleta = document.getElementById('modalShareColeta').value;

            // Cidades permitidas
            const cidadesChecked = Array.from(
                document.querySelectorAll('#modalShareCidadesCheckboxList input[type="checkbox"]:checked')
            ).map(c => c.value);

            const params = new URLSearchParams();
            params.set('readonly', '1');
            params.set('cidade', cidade);
            if (data) params.set('data', data);
            if (coleta && coleta !== 'all') params.set('coleta', coleta);
            if (selectedSectors.size > 0 && selectedSectors.size < setoresList.length) {
                params.set('setores', Array.from(selectedSectors).join(','));
            }
            if (cidadesChecked.length > 0 && cidadesChecked.length < Object.keys(CIDADES_MAPA).length) {
                params.set('cidades', cidadesChecked.join(','));
            }

            const url = `${base}/?${params.toString()}`;
            const inputUrl = document.getElementById('inputGeneratedShareUrl');
            if (inputUrl) inputUrl.value = url;
            return url;
        }

        function mostrarToast(msg) {
            const toast = document.getElementById('locarToast');
            if (!toast) return;
            toast.innerText = '✔ ' + msg;
            toast.style.display = 'flex';
            clearTimeout(toast._tmr);
            toast._tmr = setTimeout(() => { toast.style.display = 'none'; }, 3200);
        }

        document.getElementById('btnShareLink').addEventListener('click', abrirModalShare);
        document.getElementById('btnCloseModalShare').addEventListener('click', () => {
            document.getElementById('modalShareLink').style.display = 'none';
        });
        document.getElementById('btnCancelShareModal').addEventListener('click', () => {
            document.getElementById('modalShareLink').style.display = 'none';
        });
        document.getElementById('modalShareLink').addEventListener('click', (e) => {
            if (e.target === document.getElementById('modalShareLink')) {
                document.getElementById('modalShareLink').style.display = 'none';
            }
        });

        // Regenerar URL ao mudar qualquer campo do modal
        ['modalShareCidade','modalShareData','modalShareColeta'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', gerarUrlShare);
        });
        document.getElementById('modalShareCidadesCheckboxList').addEventListener('change', gerarUrlShare);

        document.getElementById('btnCopyGeneratedUrl').addEventListener('click', () => {
            const url = document.getElementById('inputGeneratedShareUrl').value;
            if (!url) return;
            navigator.clipboard.writeText(url).then(() => {
                mostrarToast('Link de visualização copiado para a área de transferência!');
            }).catch(() => {
                // fallback para browsers sem clipboard API
                const inp = document.getElementById('inputGeneratedShareUrl');
                inp.select();
                document.execCommand('copy');
                mostrarToast('Link copiado!');
            });
        });

        document.getElementById('btnTestOpenShareUrl').addEventListener('click', () => {
            const url = gerarUrlShare();
            if (url) window.open(url, '_blank');
        });

        // ====================================================================
        // INICIALIZAÇÃO
        // ====================================================================
        window.onload = () => {
            checkUrlParams();
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

        # 1. Interface Web Principal SPA
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

        # 3. Exportação de CSV de Vias Omitidas
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

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        dados = json.loads(body) if body else {}

        # 1. Salvar ou Criar Segmento no PROJETO DA CIDADE
        if path == "/api/segmento/salvar":
            try:
                res = db.salvar_ou_atualizar_segmento(dados)
                
                contrato_id = dados.get("contrato_id", "").lower().strip()
                if contrato_id:
                    conn = db.get_connection()
                    with conn:
                        conn.execute("DELETE FROM auditorias WHERE contrato_id = ?", (contrato_id,))
                    conn.close()

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(json.dumps({"sucesso": True, "segmento": res}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"sucesso": False, "erro": str(e)}).encode("utf-8"))
            return

        # 2. Excluir Segmento do PROJETO DA CIDADE
        if path == "/api/segmento/excluir":
            try:
                seg_id = dados.get("id")
                conn = db.get_connection()
                cur = conn.cursor()
                cur.execute("SELECT contrato_id FROM segmentos_viarios WHERE id = ?", (seg_id,))
                row = cur.fetchone()
                contrato_id = row["contrato_id"] if row else None
                conn.close()

                db.excluir_segmento(seg_id)

                if contrato_id:
                    conn = db.get_connection()
                    with conn:
                        conn.execute("DELETE FROM auditorias WHERE contrato_id = ?", (contrato_id,))
                    conn.close()

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(json.dumps({"sucesso": True}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"sucesso": False, "erro": str(e)}).encode("utf-8"))
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
