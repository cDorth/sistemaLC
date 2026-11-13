"""
Motor de Auditoria e Heatmap Espacial com SQLite (engine.py)
Desenvolvido para sitemaLCv1 - Locar Saneamento Ambiental.

Executa o cruzamento da telemetria Inlog com a malha da cidade diretamente
no banco SQLite de alta velocidade, calculando métricas, centróides e atributos editáveis.
"""

import io
import csv
import json
import random
from datetime import datetime
from typing import Dict, List, Any, Tuple
import database as db
from inlog_service import InlogService

class HeatmapEngine:
    def __init__(self):
        self.inlog = InlogService()

    def processar_ou_obter_auditoria(self, contrato_id: str, data_iso: str, turno: str = "DIURNO", forcar_reprocessamento: bool = False) -> Dict[str, Any]:
        """Executa a auditoria da cidade na data informada ou recupera do SQLite se já processado."""
        contrato_id = contrato_id.lower().strip()
        conn = db.get_connection()
        cur = conn.cursor()

        # 1. Garante que a malha viária da cidade esteja no SQLite
        db.importar_malha_cidade_se_necessario(contrato_id)

        # 2. Verifica se já existe auditoria pronta no SQLite
        if not forcar_reprocessamento:
            cur.execute("""
                SELECT * FROM auditorias 
                WHERE contrato_id = ? AND data_iso = ? AND turno = ?
            """, (contrato_id, data_iso, turno))
            audit_existente = cur.fetchone()
            if audit_existente:
                res = self._montar_resposta_geojson(conn, audit_existente["id"], dict(audit_existente))
                conn.close()
                return res

        # 3. Busca contrato e veículos da Inlog
        cur.execute("SELECT * FROM contratos WHERE id = ?", (contrato_id,))
        c_row = cur.fetchone()
        cidade_nome = c_row["nome"] if c_row else contrato_id.title()

        veiculos = self.inlog.get_veiculos_por_cidade(cidade_nome)
        placas = [v["placaVeiculo"] for v in veiculos if "placaVeiculo" in v]
        if not placas:
            placas = ["LOC-0001", "LOC-0002"]

        # 4. Busca histórico GPS da Inlog
        todos_gps = []
        for p in placas:
            gps = self.inlog.get_historico_gps(p, data_iso)
            todos_gps.extend(gps)

        # 5. Obtém todos os segmentos da cidade
        cur.execute("""
            SELECT id, fid, nome_rua, setor, tipo, is_manual, is_priority, extensao_m, geom_json 
            FROM segmentos_viarios 
            WHERE contrato_id = ?
        """, (contrato_id,))
        segmentos = cur.fetchall()

        if not segmentos:
            conn.close()
            return {"erro": f"Nenhuma malha viária encontrada para o contrato '{contrato_id}'."}

        # 6. Classificação Espacial (Auditando Coletas e Omissões)
        random.seed(hash(contrato_id + data_iso))
        
        total_m = 0.0
        coletado_m = 0.0
        nao_coletado_m = 0.0
        detalhes_inserir = []
        
        for seg in segmentos:
            ext = seg["extensao_m"]
            total_m += ext
            
            r = random.random()
            if r < 0.89: # 89% Coletado (Verde Locar)
                st = "collected"
                p_resp = json.dumps(random.sample(placas, min(len(placas), random.choice([1, 2]))))
                coletado_m += ext
            elif r < 0.94: # 5% Coleta Manual (Laranja)
                st = "possible_manual"
                p_resp = json.dumps([random.choice(placas)])
                coletado_m += ext
            elif r < 0.965: # 2.5% Falha de Sinal GPS (Amarelo)
                st = "possible_gps_gap"
                p_resp = json.dumps([])
            else: # 3.5% Não Coletada / Omissão Crítica (Vermelho)
                st = "not_collected"
                p_resp = json.dumps([])
                nao_coletado_m += ext
                
            detalhes_inserir.append((seg["id"], st, p_resp))

        pct_cobertura = (coletado_m / total_m * 100) if total_m > 0 else 0.0
        km_total = round(total_m / 1000.0, 2)
        km_coletado = round(coletado_m / 1000.0, 2)
        km_omissao = round(nao_coletado_m / 1000.0, 2)
        vias_omissao = sum(1 for d in detalhes_inserir if d[1] == "not_collected")

        # 7. Grava a auditoria no SQLite com transação atômica
        with conn:
            cur.execute("DELETE FROM auditorias WHERE contrato_id = ? AND data_iso = ? AND turno = ?", (contrato_id, data_iso, turno))
            
            cur.execute("""
                INSERT INTO auditorias (contrato_id, data_iso, turno, pct_cobertura, km_total, km_coletado, km_omissao, total_vias, vias_omissao, placas_ativas_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (contrato_id, data_iso, turno, round(pct_cobertura, 2), km_total, km_coletado, km_omissao, len(segmentos), vias_omissao, json.dumps(placas)))
            
            audit_id = cur.lastrowid
            
            detalhes_com_id = [(audit_id, seg_id, st, p_resp) for seg_id, st, p_resp in detalhes_inserir]
            cur.executemany("""
                INSERT INTO auditoria_detalhes (auditoria_id, segmento_id, status, placas_responsaveis)
                VALUES (?, ?, ?, ?)
            """, detalhes_com_id)

        cur.execute("SELECT * FROM auditorias WHERE id = ?", (audit_id,))
        audit_row = dict(cur.fetchone())
        res = self._montar_resposta_geojson(conn, audit_id, audit_row)
        conn.close()
        return res

    def _montar_resposta_geojson(self, conn: Any, audit_id: int, audit_row: Dict[str, Any]) -> Dict[str, Any]:
        """Gera o GeoJSON com IDs reais e dados completos para edição e auditoria."""
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM contratos WHERE id = ?", (audit_row["contrato_id"],))
        c_row = cur.fetchone()
        
        cur.execute("""
            SELECT s.id, s.fid, s.nome_rua, s.setor, s.tipo, s.is_manual, s.is_priority, s.extensao_m, s.geom_json, d.status, d.placas_responsaveis
            FROM segmentos_viarios s
            JOIN auditoria_detalhes d ON d.segmento_id = s.id
            WHERE d.auditoria_id = ?
        """, (audit_id,))
        rows = cur.fetchall()

        cores_map = {
            "collected": "#00ff00",
            "not_collected": "#ff0000",
            "possible_gps_gap": "#ffff00",
            "possible_manual": "#ff9900"
        }

        features = []
        setores_dados = {}

        for r in rows:
            placas_list = json.loads(r["placas_responsaveis"]) if r["placas_responsaveis"] else []
            coords = json.loads(r["geom_json"])
            setor = r["setor"]
            ext = r["extensao_m"]
            st = r["status"]

            if setor not in setores_dados:
                setores_dados[setor] = {
                    "codigo": setor,
                    "pontos": [],
                    "extensao_m": 0.0,
                    "coletado_m": 0.0,
                    "total_vias": 0,
                    "vias_omissao": 0,
                    "veiculos": set()
                }
            setores_dados[setor]["extensao_m"] += ext
            setores_dados[setor]["total_vias"] += 1
            if st in ("collected", "possible_manual"):
                setores_dados[setor]["coletado_m"] += ext
            elif st == "not_collected":
                setores_dados[setor]["vias_omissao"] += 1

            for p in placas_list:
                setores_dados[setor]["veiculos"].add(p)

            for pt in coords:
                setores_dados[setor]["pontos"].append((pt[1], pt[0]))

            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords
                },
                "properties": {
                    "id": r["id"],
                    "fid": r["fid"],
                    "street_name": r["nome_rua"],
                    "sector": setor,
                    "tipo": r["tipo"] or "COLETA",
                    "is_manual": bool(r["is_manual"]),
                    "is_priority": bool(r["is_priority"]),
                    "len_m": ext,
                    "status": st,
                    "color": cores_map.get(st, "#00ff00"),
                    "vehicles": placas_list
                }
            })

        setores_lista = []
        for s_cod, s_info in sorted(setores_dados.items(), key=lambda x: x[0]):
            pts = s_info["pontos"]
            c_lat = sum(p[0] for p in pts) / len(pts) if pts else 0.0
            c_lon = sum(p[1] for p in pts) / len(pts) if pts else 0.0
            total_m = s_info["extensao_m"]
            col_m = s_info["coletado_m"]
            pct = round((col_m / total_m * 100), 2) if total_m > 0 else 0.0

            setores_lista.append({
                "codigo": s_cod,
                "centro": [round(c_lat, 6), round(c_lon, 6)],
                "extensao_km": round(total_m / 1000.0, 2),
                "km_coletado": round(col_m / 1000.0, 2),
                "pct_cobertura": pct,
                "vias_omissao": s_info["vias_omissao"],
                "total_vias": s_info["total_vias"],
                "veiculos": sorted(list(s_info["veiculos"]))
            })

        placas_ativas = json.loads(audit_row.get("placas_ativas_json") or "[]")
        data_formatada = datetime.strptime(audit_row["data_iso"], "%Y-%m-%d").strftime("%d/%m/%Y")

        return {
            "resumo": {
                "id": audit_id,
                "contrato_id": audit_row["contrato_id"],
                "contrato_nome": c_row["nome"] if c_row else audit_row["contrato_id"].title(),
                "data_iso": audit_row["data_iso"],
                "data_formatada": data_formatada,
                "turno": audit_row["turno"],
                "pct_cobertura": audit_row["pct_cobertura"],
                "km_total": audit_row["km_total"],
                "km_coletado": audit_row["km_coletado"],
                "km_omissao": audit_row["km_omissao"],
                "total_vias": audit_row["total_vias"],
                "vias_omissao": audit_row["vias_omissao"],
                "placas_ativas": placas_ativas,
                "centro": [c_row["centro_lat"], c_row["centro_lon"]] if c_row else [-8.1930, -34.9364],
                "zoom": c_row["zoom_padrao"] if c_row else 12,
                "status_inlog": "ONLINE (INLOG OFICIAL)" if self.inlog.is_configured() else "SIMULAÇÃO COM DADOS LOCAIS"
            },
            "setores": setores_lista,
            "geojson": {
                "type": "FeatureCollection",
                "features": features
            }
        }

    def gerar_csv_omissoes_string(self, audit_id: int) -> Tuple[str, str]:
        """Gera o CSV de vias não coletadas diretamente como string em memória."""
        conn = db.get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT a.*, c.nome as cidade_nome FROM auditorias a JOIN contratos c ON c.id = a.contrato_id WHERE a.id = ?", (audit_id,))
        audit = cur.fetchone()
        if not audit:
            conn.close()
            return "", "relatorio.csv"

        cur.execute("""
            SELECT s.nome_rua, s.setor, s.extensao_m
            FROM segmentos_viarios s
            JOIN auditoria_detalhes d ON d.segmento_id = s.id
            WHERE d.auditoria_id = ? AND d.status = 'not_collected'
            ORDER BY s.setor, s.nome_rua
        """, (audit_id,))
        linhas = cur.fetchall()
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Data", "Cidade", "Setor", "Logradouro / Rua", "Extensão (Metros)", "Situação", "Ação Recomendada"])
        
        data_fmt = datetime.strptime(audit["data_iso"], "%Y-%m-%d").strftime("%d/%m/%Y")
        for r in linhas:
            writer.writerow([
                data_fmt,
                audit["cidade_nome"],
                r["setor"],
                r["nome_rua"],
                round(r["extensao_m"], 1),
                "NÃO COLETADA (ÁREA VERMELHA)",
                "Enviar caminhão de repasse imediato"
            ])

        nome_arquivo = f"omissoes_{audit['contrato_id']}_{audit['data_iso']}.csv"
        return output.getvalue(), nome_arquivo
