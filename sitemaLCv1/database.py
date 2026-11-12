"""
Módulo de Banco de Dados SQLite Otimizado (database.py)
Desenvolvido para sitemaLCv1 - Locar Saneamento Ambiental.

Gerencia o banco local locar.db com modo WAL para alta performance,
eliminando a necessidade de gerar arquivos HTML ou acumular dados temporários em disco.
"""

import os
import sqlite3
import json
import re
from typing import Dict, List, Optional, Any, Tuple

DB_PATH = os.path.join(os.path.dirname(__file__), "locar.db")

def get_connection() -> sqlite3.Connection:
    """Cria conexão com o SQLite configurada para alta velocidade."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -64000;")  # 64MB cache em RAM
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    """Cria as tabelas e índices se não existirem."""
    conn = get_connection()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS contratos (
                id TEXT PRIMARY KEY,
                nome TEXT NOT NULL,
                centro_lat REAL,
                centro_lon REAL,
                zoom_padrao REAL DEFAULT 12
            );

            CREATE TABLE IF NOT EXISTS segmentos_viarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contrato_id TEXT NOT NULL,
                fid INTEGER,
                nome_rua TEXT,
                setor TEXT,
                extensao_m REAL,
                geom_json TEXT NOT NULL,
                FOREIGN KEY (contrato_id) REFERENCES contratos(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_seg_contrato ON segmentos_viarios(contrato_id);
            CREATE INDEX IF NOT EXISTS idx_seg_setor ON segmentos_viarios(contrato_id, setor);

            CREATE TABLE IF NOT EXISTS auditorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contrato_id TEXT NOT NULL,
                data_iso TEXT NOT NULL,
                turno TEXT DEFAULT 'DIURNO',
                pct_cobertura REAL,
                km_total REAL,
                km_coletado REAL,
                km_omissao REAL,
                total_vias INTEGER,
                vias_omissao INTEGER,
                placas_ativas_json TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(contrato_id, data_iso, turno)
            );
            CREATE INDEX IF NOT EXISTS idx_audit_contrato_data ON auditorias(contrato_id, data_iso);

            CREATE TABLE IF NOT EXISTS auditoria_detalhes (
                auditoria_id INTEGER NOT NULL,
                segmento_id INTEGER NOT NULL,
                status TEXT NOT NULL, -- 'collected', 'not_collected', 'possible_gps_gap', 'possible_manual'
                placas_responsaveis TEXT,
                PRIMARY KEY (auditoria_id, segmento_id),
                FOREIGN KEY (auditoria_id) REFERENCES auditorias(id) ON DELETE CASCADE,
                FOREIGN KEY (segmento_id) REFERENCES segmentos_viarios(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_det_status ON auditoria_detalhes(auditoria_id, status);
        """)
        
        # Popula contratos padrão se tabela estiver vazia
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM contratos")
        if cur.fetchone()[0] == 0:
            contratos_iniciais = [
                ("jaboatao", "Jaboatão dos Guararapes", -8.1930, -34.9364, 12),
                ("cabo", "Cabo de Santo Agostinho", -8.2833, -35.0333, 12),
                ("olinda", "Olinda", -7.9986, -34.8459, 13),
                ("paulista", "Paulista", -7.9408, -34.8728, 12.5),
                ("caruaru", "Caruaru", -8.2838, -35.9761, 12),
                ("cuiaba", "Cuiabá", -15.6014, -56.0979, 12),
                ("garanhuns", "Garanhuns", -8.8907, -36.4966, 13),
                ("pesqueira", "Pesqueira", -8.3586, -36.6961, 13),
                ("vitoria", "Vitória de Santo Antão", -8.1189, -35.2928, 13),
                ("canela", "Canela", -29.3644, -50.8119, 13),
                ("jequie", "Jequié", -13.8575, -40.0839, 13),
                ("novo hamburgo", "Novo Hamburgo", -29.6789, -51.1306, 12.5)
            ]
            cur.executemany("INSERT INTO contratos (id, nome, centro_lat, centro_lon, zoom_padrao) VALUES (?, ?, ?, ?, ?)", contratos_iniciais)
    conn.close()

def importar_malha_cidade_se_necessario(contrato_id: str):
    """
    Importa as vias da cidade para a tabela segmentos_viarios apenas na primeira vez,
    extraindo da pasta '5' ou de projetos locais. Rápido e executado uma única vez.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM segmentos_viarios WHERE contrato_id = ?", (contrato_id,))
    total = cur.fetchone()[0]
    if total > 0:
        conn.close()
        return total

    # Busca o arquivo de referência da cidade na pasta 5
    pasta_5 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "5"))
    mapa_arquivos = {
        "jaboatao": "Jaboatao_dia-05.11.2026.html",
        "cabo": "Cabo_dia-05.11.2026.html",
        "olinda": "Olinda_dia-05.11.2026.html",
        "paulista": "Paulista_dia-05.11.2026.html",
        "caruaru": "Caruaru_dia-05.11.2026.html",
        "cuiaba": "Cuiaba_dia-05.11.2026.html",
        "garanhuns": "Garanhuns_dia-05.11.2026.html",
        "pesqueira": "Pesqueira_dia-05.11.2026.html",
        "vitoria": "Vitoria_dia-05.11.2026.html",
        "canela": "Canela_dia-05.11.2026.html",
        "jequie": "Jequie_dia-05.11.2026.html",
        "novo hamburgo": "Novo hamburgo_dia-05.11.2026.html"
    }

    arquivo_nome = mapa_arquivos.get(contrato_id.lower())
    if not arquivo_nome:
        conn.close()
        return 0

    caminho = os.path.join(pasta_5, arquivo_nome)
    if not os.path.exists(caminho):
        conn.close()
        return 0

    with open(caminho, "r", encoding="utf-8", errors="ignore") as f:
        texto = f.read()

    m = re.search(r'window\.SEGMENTS\s*=\s*(\{.*?\});', texto)
    if not m:
        conn.close()
        return 0

    data = json.loads(m.group(1))
    features = data.get("features", [])
    
    registros = []
    for f in features:
        props = f.get("properties", {})
        geom = f.get("geometry", {})
        fid = props.get("fid", 0)
        rua = props.get("street_name") or "Via Sem Nome"
        setor = props.get("sector") or props.get("sector_key") or "S/S"
        extensao = float(props.get("len_m", 50.0))
        geom_str = json.dumps(geom.get("coordinates", []))
        
        registros.append((contrato_id, fid, rua, setor, extensao, geom_str))

    with conn:
        conn.executemany("""
            INSERT INTO segmentos_viarios (contrato_id, fid, nome_rua, setor, extensao_m, geom_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, registros)

    conn.close()
    return len(registros)

# Inicializa banco na importação do módulo
init_db()
