"""
Funções de processamento geoespacial.

Este módulo contém toda a lógica de manipulação de geometrias,
leitura de shapefiles, construção de segmentos e análise espacial.

VERSÃO CORRIGIDA:
- build_route_segments agora mantém segmentos separados por rota
- Novo parâmetro para controlar agrupamento
"""

import logging
import os
import re
import glob
import zipfile
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Set
from datetime import date, timedelta
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, GeometryCollection, Point
from shapely.validation import make_valid
from project_heatmaps.config import BASE_DIR, GEO, PATHS, GPS_DB_PATH
from project_heatmaps.parsers import parse_city_and_date_from_inner, parse_date_from_outer, extract_route_or_sector_code, split_code_parts, extract_plate_key, normalize_plate_key, format_plate
logger = logging.getLogger(__name__)

def read_zipped_shapefile(zip_path):
    """
    Lê o primeiro .shp válido dentro de um zip.
    
    Tenta:
    1. Leitura direta via /vsizip/
    2. Extração + leitura local
    3. Reparo com ogr2ogr (se disponível)
    
    Args:
        zip_path: Caminho do arquivo zip
        
    Returns:
        GeoDataFrame com geometrias válidas
        
    Raises:
        RuntimeError: Se não conseguir ler/reparar o shapefile
    """
    abs_zip = Path(zip_path).resolve()
    with zipfile.ZipFile(abs_zip, 'r') as z:
        members = z.namelist()
        shp_candidates = [m for m in members if m.lower().endswith('.shp')]
        if not shp_candidates:
            raise RuntimeError(f"No .shp inside {zip_path}")
        roots = [m for m in shp_candidates if '/' not in m and '\\' not in m]
        shp_inside = roots[0] if roots else shp_candidates[0]
    vfs_path = f"/vsizip/{abs_zip.as_posix()}/{shp_inside}"
    try:
        gdf = gpd.read_file(vfs_path, on_invalid='ignore')
        if gdf is not None and not gdf.empty:
            gdf = gdf[(gdf.geometry.notna() & (~gdf.geometry.is_empty))]
            return gdf
    except Exception:
        pass
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(abs_zip, 'r') as z:
            base = Path(shp_inside).stem
            relevant = [m for m in members if Path(m).stem == base]
            for m in set((relevant or members)):
                try:
                    data = z.read(m)
                    outpath = os.path.join(tmp, Path(m).name)
                    with open(outpath, 'wb') as fh:
                        fh.write(data)
                except Exception:
                    continue
        shp_local = os.path.join(tmp, f"{base}.shp")
        if not os.path.exists(shp_local):
            found = [f for f in os.listdir(tmp) if f.lower().endswith('.shp')]
            if found:
                shp_local = os.path.join(tmp, found[0])
            else:
                raise RuntimeError(f"No .shp found after extracting {zip_path}")
        try:
            gdf = gpd.read_file(shp_local, on_invalid='ignore')
            if gdf is not None and not gdf.empty:
                gdf = _clean_geometries(gdf)
                if not gdf.empty:
                    return gdf
        except Exception:
            pass
        ogr2ogr_path = shutil.which('ogr2ogr')
        if ogr2ogr_path:
            fixed_path = os.path.join(tmp, f"{base}_fixed.gpkg")
            try:
                cmd = [ogr2ogr_path, '-f', 'GPKG', fixed_path, shp_local, '-nlt', 'MULTIPOLYGON']
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                gdf_fixed = gpd.read_file(fixed_path)
                gdf_fixed = _clean_geometries(gdf_fixed)
                if not gdf_fixed.empty:
                    return gdf_fixed
            except Exception:
                pass
        raise RuntimeError(f"Could not read or repair shapefile inside {zip_path}")

def _clean_geometries(gdf):
    """
    Limpa geometrias inválidas de um GeoDataFrame.
    
    Args:
        gdf: GeoDataFrame original
        
    Returns:
        GeoDataFrame com apenas geometrias válidas
    """
    gdf = gdf[(gdf.geometry.notna() & (~gdf.geometry.is_empty))].copy()
    try:
        gdf['geometry'] = gdf.geometry.apply((lambda geom: geom.buffer(0) if not geom.is_valid else geom))
    except Exception:
        pass
    def is_valid_geom(geom):
        if geom is None or geom.is_empty:
            return False
        if geom.geom_type == 'Polygon':
            try:
                return len(list(geom.exterior.coords)) > 3
            except Exception:
                return False
        if geom.geom_type == 'MultiPolygon':
            try:
                parts = [p for p in geom.geoms if len(list(p.exterior.coords)) > 3]
                return len(parts) > 0
            except Exception:
                return False
        if geom.geom_type == 'LineString':
            return len(list(geom.coords)) > 1
        return True

    gdf = gdf[gdf.geometry.apply(is_valid_geom)].copy()
    return gdf

def read_all_shapes_from_zip(zip_path):
    """
    Lê todos os .shp dentro de um zip e concatena.
    
    Args:
        zip_path: Caminho do arquivo zip
        
    Returns:
        GeoDataFrame concatenado
        
    Raises:
        RuntimeError: Se todos os shapefiles falharem
    """
    abs_path = Path(zip_path).resolve()
    gdfs = []
    with zipfile.ZipFile(abs_path, 'r') as z:
        shp_files = [n for n in z.namelist() if n.lower().endswith('.shp')]
        if not shp_files:
            raise RuntimeError(f"No .shp in {zip_path}")
        for inner in shp_files:
            vfs = f"zip://{abs_path.as_posix()}!{inner}"
            try:
                gdf = gpd.read_file(vfs, on_invalid='ignore')
            except Exception as e:
                print(f"Warning: could not read {inner}: {e}")
                continue
            if 'geometry' in gdf.columns:
                gdf = gdf[(gdf.geometry.notna() & (~gdf.geometry.is_empty))]
            if not gdf.empty:
                shp_basename = os.path.splitext(os.path.basename(inner))[0]
                shp_basename = shp_basename.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')
                gdf['vehicle_id'] = shp_basename
                for col_orig in list(gdf.columns):
                    col_upper = col_orig.upper().strip()
                    if col_upper == 'DATAHORA' and col_orig != 'DATAHORA':
                        gdf = gdf.rename(columns={col_orig: 'DATAHORA'})
                    elif col_upper == 'VELOCIDADE' and col_orig != 'VELOCIDADE':
                        gdf = gdf.rename(columns={col_orig: 'VELOCIDADE'})
                    continue
                gdfs.append(gdf)
    if not gdfs:
        raise RuntimeError(f"All shapefiles in {zip_path} failed to load")
    df = pd.concat(gdfs, ignore_index=True)
    return gpd.GeoDataFrame(df, geometry='geometry')

TRACK_COLUMNS = ['geometry', 'city', 'day', 'vehicle_id', 'plate_key', 'DATAHORA', 'VELOCIDADE']

def _empty_tracks(target_crs=None):
    """GeoDataFrame de tracks vazio, com o schema canônico."""
    return gpd.GeoDataFrame({c: pd.Series(dtype='object') for c in TRACK_COLUMNS}, geometry='geometry', crs=target_crs)

def _city_key(city):
    """
    Chave de cidade para cruzar as duas fontes.

    O DuckDB grava a cidade em minúscula ("jaboatao") e o shapefile guarda o
    nome como veio do zip ("Jaboatao"); sem normalizar, nada casa.
    """
    return str(city).strip().upper()

def read_tracks_from_outer_zip(outer_zip_path, allowed_cities, visit_date):
    """
    Lê tracks de um outer zip para cidades específicas em uma data.
    
    Args:
        outer_zip_path: Caminho do arquivo 'Shapes - dd.mm.yyyy.zip'
        allowed_cities: Set de cidades permitidas
        visit_date: Data da visita
        
    Returns:
        GeoDataFrame com tracks filtrados
    """
    abs_path = Path(outer_zip_path).resolve()
    gdfs = []
    with zipfile.ZipFile(abs_path, 'r') as outer:
        inner_zips = [n for n in outer.namelist() if n.lower().endswith('.zip')]
        if not inner_zips:
            return gpd.GeoDataFrame(columns=['geometry', 'city', 'day'], geometry='geometry')
        with tempfile.TemporaryDirectory() as tmpdir:
            for inner_name in inner_zips:
                city, inner_date = parse_city_and_date_from_inner(inner_name)
                if city is None or inner_date != visit_date:
                    continue
                if city not in allowed_cities:
                    continue
                inner_bytes = outer.read(inner_name)
                inner_path = os.path.join(tmpdir, os.path.basename(inner_name))
                with open(inner_path, 'wb') as f:
                    f.write(inner_bytes)
                try:
                    gdf = read_all_shapes_from_zip(inner_path)
                except Exception as e:
                    print(f"Warning: could not read inner zip {inner_name}: {e}")
                    continue
                if not gdf.empty:
                    gdf['city'] = city
                    gdf['day'] = visit_date
                    gdfs.append(gdf)
    if not gdfs:
        return gpd.GeoDataFrame(columns=['geometry', 'city', 'day'], geometry='geometry')
    df = pd.concat(gdfs, ignore_index=True)
    return gpd.GeoDataFrame(df, geometry='geometry')

def _backend_pelo_env():
    """
    GPS_BACKEND lido aqui, com as MESMAS regras do `_env_file_values`/`gps_backend`
    do InlogAPI: env > dependencias/.env.local > dependencias/.env; dentro do arquivo
    a última linha vence; `export ` e aspas saem; chave vazia não esconde o arquivo
    seguinte. A única diferença: texto que não é UTF-8 cai para latin-1, como o
    user_config já faz com este mesmo .env (Bloco de Notas grava cp1252).
    """
    v = os.environ.get('GPS_BACKEND')
    if not v and BASE_DIR is not None:
        vals = {}
        for nome in ('.env', '.env.local'):
            f = ((BASE_DIR / 'dependencias') / nome)
            if not f.is_file():
                continue
            raw = f.read_bytes()
            try:
                texto = raw.decode('utf-8')
            except UnicodeDecodeError:
                texto = raw.decode('latin-1')
            for line in texto.splitlines():
                line = line.strip()
                if line.startswith('export '):
                    line = line[len('export '):].strip()
                if line and not line.startswith('#') and '=' in line:
                    k, val = line.split('=', 1)
                    val = val.strip().strip('"').strip("'")
                    if val:
                        vals[k.strip()] = val
                continue
        v = vals.get('GPS_BACKEND')
    v = (v or 'duckdb').strip().lower()
    if v not in ('duckdb', 'mysql'):
        raise ValueError(f"GPS_BACKEND={v!r} — use 'duckdb' ou 'mysql'")
    return v

def _gps_backend():
    """
    'duckdb' ou 'mysql' — a MESMA chave GPS_BACKEND do InlogAPI
    (`inlog_api_project.core.gps_store_mysql.gps_backend`).

    Sem o inlog_api_project importável (o .exe de hoje é compilado só com este
    repo) só existe o DuckDB. Mas a máquina que já pede mysql não pode cair calada
    num gps.duckdb que parou de ser atualizado: aí é erro, com o motivo. Um .env
    fora de UTF-8 derrubava o parser do InlogAPI; aqui ele é lido como latin-1.
    """
    try:
        from inlog_api_project.core.gps_store_mysql import gps_backend
    except ImportError:
        if _backend_pelo_env() == 'mysql':
            raise RuntimeError('GPS_BACKEND=mysql, mas este executável não tem o leitor MySQL (compilado sem o inlog_api_project). Atualize o HeatmapsGenerator.exe.')
        return 'duckdb'
    try:
        return gps_backend()
    except UnicodeDecodeError:
        return _backend_pelo_env()

class _FonteDuckDB:
    """As consultas de sempre sobre o gps.duckdb (conexão read_only)."""
    nome = 'DuckDB'
    def __init__(self, conn):
        self.conn = conn

    def close(self):
        self.conn.close()

    def tabelas_faltando(self):
        """Aviso da primeira tabela ausente, ou None."""
        try:
            self.conn.execute('SELECT 1 FROM vehicle_contract_timeline LIMIT 1')
        except Exception:
            return 'Tabela vehicle_contract_timeline não encontrada no banco DuckDB. Execute o processor VehicleContractTimeline antes de gerar os mapas de execução.'
        try:
            self.conn.execute('SELECT 1 FROM gps_history LIMIT 1')
        except Exception:
            return 'Tabela gps_history não encontrada no banco DuckDB. Execute o fetcher history.py antes de gerar os mapas de execução.'

    def placas_do_contrato(self, city_upper, start_ts, end_ts):
        rows = self.conn.execute("""
            SELECT DISTINCT placa
            FROM vehicle_contract_timeline
            WHERE contrato = ?
              AND dt_inicio <= ? AND dt_fim >= ?
        """, (city_upper, end_ts, start_ts)).fetchall()
        return [r[0] for r in rows]

    def segmentos_vct(self, plates, start_ts, end_ts):
        """Todos os segmentos da timeline das placas (qualquer contrato/tipo) — insumo de `_dias_de_outro_contrato`."""
        ph = ','.join(('?' for _ in plates))
        return self.conn.execute(f"\n            SELECT placa, contrato, tipo, dt_inicio, dt_fim, n_pontos_coleta\n            FROM vehicle_contract_timeline\n            WHERE placa IN ({ph})\n              AND dt_inicio <= ? AND dt_fim >= ?\n        ", (plates + [end_ts, start_ts])).df()

    def dias_com_ponto(self, plates, start_ts, end_ts):
        placeholders = ','.join(['?' for _ in plates])
        rows = self.conn.execute(f"\n            SELECT DISTINCT CAST(dataHora AS DATE) AS dt\n            FROM gps_history\n            WHERE placa_normalizada IN ({placeholders})\n              AND dataHora BETWEEN ? AND ?\n        ", (plates + [start_ts, end_ts])).fetchall()
        return {r[0] for r in rows if r[0] is not None}

    def n_pontos(self, plates, start_ts, end_ts):
        ph = ','.join(('?' for _ in plates))
        return self.conn.execute(f"\n            SELECT count(*) FROM gps_history\n            WHERE placa_normalizada IN ({ph})\n              AND dataHora BETWEEN ? AND ?\n              AND latitude IS NOT NULL AND longitude IS NOT NULL\n        ", (plates + [start_ts, end_ts])).fetchone()[0]

    def pontos(self, plates, start_ts, end_ts):
        placeholders = ','.join(['?' for _ in plates])
        return self.conn.execute(f"\n            SELECT\n                placa_normalizada,\n                dataHora,\n                latitude,\n                longitude,\n                velocidade_kmh,\n                CAST(dataHora AS DATE) AS gps_date\n            FROM gps_history\n            WHERE placa_normalizada IN ({placeholders})\n              AND dataHora BETWEEN ? AND ?\n              AND latitude  IS NOT NULL\n              AND longitude IS NOT NULL\n            ORDER BY placa_normalizada, dataHora\n        ", (plates + [start_ts, end_ts])).df()

class _FonteMySQL:
    """
    As mesmas perguntas pelos stores MySQL do InlogAPI (GPS_BACKEND=mysql), sem
    SQL novo: placas pelo VehicleContractTimelineStore, pontos e dias pelo
    GPSStore (view gps_history + gps_cobertura_dia). Duas conexões por uso.

    Os limites de período são sempre texto gerado de `date` (`_duckdb_window`),
    nunca digitado: o texto de data inválida que o MySQL compara calado
    ('2026-09-31') não chega aqui. O store da timeline ainda o passa pelo DuckDB.
    No MySQL latitude/longitude são NOT NULL, então "dia com ponto" pela
    cobertura diária é o mesmo que no DuckDB.
    """
    nome = 'MySQL'
    _COLS = ['placa_normalizada', 'dataHora', 'latitude', 'longitude', 'velocidade_kmh']
    def __init__(self):
        try:
            from inlog_api_project.core.gps_store_mysql import MySQLGPSStore
            from inlog_api_project.processors.vehicle_contract_timeline_mysql import MySQLVehicleContractTimelineStore
        except ImportError as exc:
            raise RuntimeError(f"GPS_BACKEND=mysql, mas o leitor MySQL não carregou: {exc}") from exc
            self.vct = MySQLVehicleContractTimelineStore()
            try:
                self.pts = MySQLGPSStore()
            except Exception:
                self.vct.close()
                raise

    def close(self):
        self.vct.close()
        self.pts.close()

    def tabelas_faltando(self):
        pass

    def placas_do_contrato(self, city_upper, start_ts, end_ts):
        return self.vct.get_contract_vehicles(city_upper, start_ts, end_ts)

    def segmentos_vct(self, plates, start_ts, end_ts):
        """Todos os segmentos da timeline das placas (qualquer contrato/tipo) — insumo de `_dias_de_outro_contrato`."""
        df = self.vct.query(start_ts, end_ts)
        df = df[df['placa'].isin(set(plates))]
        return df[['placa', 'contrato', 'tipo', 'dt_inicio', 'dt_fim', 'n_pontos_coleta']].reset_index(drop=True)

    def _por_dia(self, plates, start_ts, end_ts):
        """
        Pontos por (placa, dia) no período EXATO do BETWEEN do DuckDB.

        Período de dias inteiros, o store responde pela gps_cobertura_dia, que conta
        o dia todo — inclusive 23:59:59.500. Desde 2026-10-28 o próprio store
        reconta o último dia (MySQLGPSStore._sem_sobra_do_ultimo_dia); antes, era
        corrigido aqui (achado do adversário, 2026-10-27).
        """
        return self.pts.point_stats_by_placa_day(start_ts, end_ts, plates)

    def dias_com_ponto(self, plates, start_ts, end_ts):
        return set(self._por_dia(plates, start_ts, end_ts)['dia'].dt.date)

    def n_pontos(self, plates, start_ts, end_ts):
        return int(self._por_dia(plates, start_ts, end_ts)['n_pontos'].sum())

    def pontos(self, plates, start_ts, end_ts):
        partes = [df for df in (self.pts.query_columns(start_ts, end_ts, [p], self._COLS) for p in sorted(plates)) if not df.empty]
        if not partes:
            return pd.DataFrame(columns=(self._COLS + ['gps_date']))
        df = pd.concat(partes, ignore_index=True)
        df = df[(df['latitude'].notna() & df['longitude'].notna())].reset_index(drop=True)
        df['gps_date'] = df['dataHora'].dt.date
        return df

def _duckdb_window(date_from, date_to):
    """
    Janela de consulta do banco: [D 00:00:00, (D_fim + 1) 23:59:59].

    O fim é estendido em +1 dia para capturar a madrugada do dia seguinte que
    pertence ao "dia operacional" do último dia (turno noturno). O recorte fino
    é feito depois em main.py via _filter_tracks_by_contract_time.
    """
    return (f"{date_from.isoformat()} 00:00:00", f"{(date_to + timedelta(days=1)).isoformat()} 23:59:59")

def _allowed_plates_for_types(vehicle_types):
    """
    Placas do cache de veículos para os tipos pedidos (None → COMPACTADOR).

    Returns:
        (allowed_plates, filter_types). `allowed_plates` vazio significa
        "cache ausente" — o chamador então NÃO filtra por tipo.
    """
    from project_heatmaps.user_config import get_plates_by_vehicle_type
    if not vehicle_types:
        vehicle_types = ['COMPACTADOR']
    filter_types = {v.upper() for v in vehicle_types}
    allowed_plates = set()
    for vt in filter_types:
        p = get_plates_by_vehicle_type(vt)
        if p:
            allowed_plates |= p
    return (allowed_plates, filter_types)

def _dias_de_outro_contrato(segs, contrato, ini_min):
    """(placa, dia operacional) que a timeline diz não serem de `contrato`."""
    if segs is None or segs.empty:
        return set()
    ini = (pd.to_datetime(segs['dt_inicio']) - pd.Timedelta(minutes=int(ini_min)))
    fim = (pd.to_datetime(segs['dt_fim']) - pd.Timedelta(minutes=int(ini_min)))
    s = segs.assign(dia=ini.dt.date, n=pd.to_numeric(segs['n_pontos_coleta'], errors='coerce').fillna(0), meu=segs['contrato'] == contrato)
    toca = s.assign(dia=[list(pd.date_range(a.normalize(), b.normalize(), freq='D').date) if b >= a else a.date() for a, b in zip(ini, fim)]).explode('dia')
    tem_meu = toca.groupby(['placa', 'dia'])['meu'].any()
    col = s[(s['tipo'] == 'COLETA') & s['contrato'].notna()]
    tot = col.pivot_table(index=['placa', 'dia'], columns='contrato', values='n', aggfunc='sum', fill_value=0)
    tot = tot.reindex(tem_meu.index, fill_value=0)
    meu = tot[contrato] if contrato in tot.columns else 0
    outros = tot.drop(columns=[contrato], errors='ignore')
    perde = outros.max(axis=1) > meu if not outros.empty else False
    return set(tem_meu.index[((~tem_meu) | perde)])

def _sem_dias_de_outro_contrato(df, segs, city_upper, ini_min=None):
    """Tira de `df` (pontos do banco) as placa-dias que pertencem a outro contrato."""
    if ini_min is None:
        from project_heatmaps.config import USER_CONFIG
        from project_heatmaps.janela import minutos_da_janela
        ini_min = minutos_da_janela(USER_CONFIG.get_time_config(city_upper))[0]
    return _tirar_placa_dias(df, _dias_de_outro_contrato(segs, city_upper, ini_min), ini_min, city_upper)

def _tirar_placa_dias(df, fora, ini_min, city_upper):
    """Tira de `df` os pontos cujo (placa, dia operacional) está em `fora`. O Job 4 chama direto."""
    if not fora or df.empty:
        return df
    dia = (pd.to_datetime(df['dataHora']) - pd.Timedelta(minutes=int(ini_min))).dt.date
    chave = pd.Series(list(zip(df['placa_normalizada'], dia)), index=df.index)
    sai = chave.isin(fora)
    if not sai.any():
        return df
    por_placa = pd.Series([p for p, _ in fora]).value_counts().sort_index()
    resumo = ', '.join((f"{p} ({n} dia(s))" for p, n in por_placa.items()))
    logger.info("[DuckDB] '%s': %d ponto(s) de placa-dia de outro contrato descartado(s): %s", city_upper, int(sai.sum()), resumo)
    print(f"  [{city_upper}] placa-dia de outro contrato fora do mapa: {resumo}")
    return df[(~sai)].reset_index(drop=True)

def _sem_placas_ignoradas(plates, ignoradas, city_upper):
    """Tira as placas de [PLACAS_IGNORADAS] do contrato (decisão da operação, ver user_config)."""
    if not ignoradas:
        return plates
    fica = [p for p in plates if normalize_plate_key(p) not in ignoradas]
    if len(fica) != len(plates):
        logger.info("[DuckDB] '%s': [PLACAS_IGNORADAS] tirou %s", city_upper, sorted((set(plates) - set(fica))))
    return fica

def _plates_for_contract(fonte, city_upper, start_ts, end_ts, allowed_plates, filter_types):
    """
    Placas que o carregador vai efetivamente consultar para um contrato.

    Returns:
        (placas, motivo_do_vazio). `motivo_do_vazio` é "" quando há placas; do
        contrário é a frase que explica POR QUE não há — é ela que o pré-voo
        mostra ao usuário em vez de deixar o loader dar `continue` calado.
    """
    from project_heatmaps.config import USER_CONFIG
    plates = [p for p in fonte.placas_do_contrato(city_upper, start_ts, end_ts) if p]
    plates = _sem_placas_ignoradas(plates, USER_CONFIG.get_ignored_plates(city_upper), city_upper)
    if not plates:
        return ([], f"'{city_upper}': nenhum veículo na vehicle_contract_timeline para o período. Rode a timeline de garagens no InlogAPI para este intervalo.")
    if allowed_plates:
        antes = len(plates)
        plates = [p for p in plates if p in allowed_plates]
        if not plates:
            return ([], f"'{city_upper}': as {antes} placa(s) da timeline não estão no cache de veículos {sorted(filter_types)}. Rode 'Buscar Veículos' no InlogAPI — sem isso o banco é descartado inteiro para este contrato e só os shapefiles respondem.")
    return (plates, '')

def diagnose_gps_db(date_from=None, date_to=None, contracts=None, vehicle_types=None):
    """
    Diz, em uma frase acionável, se o gps.duckdb vai poder ser usado.

    Existe porque a degradação era INVISÍVEL: o .exe é compilado com
    `console=False`, então todo `print`/`logger.warning` do caminho "DuckDB
    indisponível" cai no vazio e o usuário só vê um heatmap desenhado apenas
    com os shapefiles — sem nenhum sinal de que o banco inteiro ficou de fora.
    A GUI chama isto ANTES de processar e mostra o motivo numa janela.

    Cobre os modos de falha reais, na ordem em que aparecem:
      1. módulo `duckdb` ausente do executável (build sem a dependência);
      2. arquivo gps.duckdb não encontrado (caminho/config);
      3. banco não abre (lock de outro processo, versão de arquivo mais nova);
      4. banco abre mas está sem as tabelas que o heatmap lê;
      5. **com a seleção do usuário em mãos** (datas + contratos): o banco está
         de pé mas NÃO tem ponto nenhum para o que foi pedido — timeline sem o
         contrato no período, placas fora do cache de veículos, ou período fora
         do alcance do gps_history. Sem este passo o pré-voo dizia "ok" e o
         loader devolvia zero calado (derrubado pelo adversário em 04/09/2026).

    O passo 5 usa as MESMAS funções que o carregador (`_duckdb_window`,
    `_allowed_plates_for_types`, `_plates_for_contract`), não uma segunda
    consulta parecida: se divergirem, o diagnóstico volta a mentir.

    Args:
        date_from/date_to/contracts: a seleção do usuário. Se qualquer um for
            None, o passo 5 é pulado (checagem só estrutural).
        vehicle_types: tipos a considerar no filtro (None → COMPACTADOR),
            igual ao carregador.

    Com GPS_BACKEND=mysql os passos 1–4 viram "os stores MySQL conectam e o
    esquema tem o ALTER" (a mensagem do store já diz rota × senha × ALTER), e o
    passo 5 é o mesmo, pela mesma fonte que o carregador usa.

    Returns:
        (ok: bool, motivo: str) — `motivo` é "" quando ok=True.
    """
    try:
        backend = _gps_backend()
    except Exception as exc:
        return (False, str(exc))
    if backend == 'mysql':
        try:
            fonte = _FonteMySQL()
        except Exception as exc:
            return (False, f"GPS_BACKEND=mysql, mas o banco MySQL não abriu:\n{exc}")
        try:
            return _conferir_selecao(fonte, date_from, date_to, contracts, vehicle_types)
        finally:
            fonte.close()
    try:
        import duckdb
    except ImportError:
        return (False, "O módulo 'duckdb' não está neste executável — ele foi compilado sem a dependência. Atualize o HeatmapsGenerator.exe para a versão nova; nesta, o gps.duckdb é ignorado e só os shapefiles são lidos.")
    if GPS_DB_PATH is None:
        from project_heatmaps.config import GPS_DB_SEARCHED
        onde = ("""
""".join((f"  - {p}" for p in GPS_DB_SEARCHED)) or '  (nenhum caminho testado)')
        return (False, f"Arquivo gps.duckdb não encontrado. Procurei em:\n{onde}\nAponte o caminho no .env/config.txt em [GPS_DATABASE] caminho = ...")
    if not GPS_DB_PATH.is_file():
        return (False, f"gps.duckdb não existe mais em: {GPS_DB_PATH}")
    try:
        conn = duckdb.connect(str(GPS_DB_PATH), read_only=True)
    except Exception as exc:
        return (False, f"Não consegui abrir {GPS_DB_PATH}:\n{exc}\nSe o InlogAPI estiver aberto/coletando, feche-o e tente de novo — o DuckDB não deixa ler enquanto outro processo tem o banco travado.")
    try:
        faltando = [t for t in ('gps_history', 'vehicle_contract_timeline') if not conn.execute('SELECT count(*) FROM duckdb_tables() WHERE table_name = ?', (t,)).fetchone()[0]]
    except Exception as exc:
        conn.close()
        return (False, f"Banco abriu mas não respondeu à consulta de tabelas: {exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if faltando:
        return (False, f"O banco {GPS_DB_PATH} não tem a(s) tabela(s): {', '.join(faltando)}. Rode no InlogAPI a busca de histórico e a timeline de garagens antes de gerar o mapa de execução.")
    if date_from is None or date_to is None or not contracts:
        return (True, '')
    try:
        conn = duckdb.connect(str(GPS_DB_PATH), read_only=True)
    except Exception as exc:
        return (False, f"Não consegui reabrir {GPS_DB_PATH} para conferir a seleção:\n{exc}")
    fonte = _FonteDuckDB(conn)
    try:
        return _conferir_selecao(fonte, date_from, date_to, contracts, vehicle_types)
    finally:
        try:
            fonte.close()
        except Exception:
            pass

def _conferir_selecao(fonte, date_from, date_to, contracts, vehicle_types):
    """Passo 5 do diagnose_gps_db, para qualquer fonte. Devolve (ok, motivo)."""
    if date_from is None or date_to is None or not contracts:
        return (True, '')
    start_ts, end_ts = _duckdb_window(date_from, date_to)
    allowed_plates, filter_types = _allowed_plates_for_types(vehicle_types)
    problemas = []
    com_dados = 0
    try:
        for contrato in contracts:
            city_upper = str(contrato).upper()
            plates, motivo = _plates_for_contract(fonte, city_upper, start_ts, end_ts, allowed_plates, filter_types)
            if not plates:
                problemas.append(motivo)
            else:
                n = fonte.n_pontos(plates, start_ts, end_ts)
                if n == 0:
                    problemas.append(f"'{city_upper}': {len(plates)} placa(s) na timeline, mas ZERO ponto GPS no período. Rode a busca de histórico no InlogAPI para estas datas.")
                else:
                    com_dados += 1
    except Exception as exc:
        return (False, f"O banco abriu, mas a conferência da seleção falhou: {exc}")
    if problemas:
        cabecalho = 'O banco está de pé, mas não responde a parte do que foi pedido:' if com_dados else 'O banco está de pé, mas NÃO tem dado nenhum para o que foi pedido:'
        return (False, ((cabecalho + """
""") + """
""".join((f"  • {m}" for m in problemas))))
    return (True, '')

def _validate_duckdb_coverage(fonte, date_from, date_to, allowed_cities):
    """
    Verifica se o banco (DuckDB ou MySQL, ver `_FonteDuckDB`/`_FonteMySQL`) tem
    todos os dados necessários para o período.

    Checks:
      1. Tabela vehicle_contract_timeline existe e tem registros para cada cidade
      2. Tabela gps_history tem pontos para as placas dessas cidades em cada
         dia do período (pelo menos uma placa com dado por dia por cidade)

    Returns:
        Tupla (is_complete: bool, warnings: list[str])
    """
    warnings = []
    start_ts = f"{date_from.isoformat()} 00:00:00"
    end_ts = f"{date_to.isoformat()} 23:59:59"
    all_dates = set()
    d = date_from
    while d <= date_to:
        all_dates.add(d)
        d += timedelta(days=1)
    falta = fonte.tabelas_faltando()
    if falta:
        warnings.append(falta)
        return (False, warnings)
    for city in sorted(allowed_cities):
        city_upper = city.upper()
        rows = fonte.placas_do_contrato(city_upper, start_ts, end_ts)
        if not rows:
            warnings.append(f"Nenhum veículo encontrado para o contrato '{city_upper}' no período {date_from:%d/%m/%Y} → {date_to:%d/%m/%Y} (vehicle_contract_timeline sem registros para essa cidade/período).")
            return (False, warnings)
        plates = [p for p in rows if p]
        if plates:
            covered_dates = fonte.dias_com_ponto(plates, start_ts, end_ts)
            missing_dates = sorted((all_dates - covered_dates))
            if missing_dates:
                sample = [str(d) for d in missing_dates[:5]]
                suffix = '...' if len(missing_dates) > 5 else ''
                warnings.append(f"GPS incompleto para '{city_upper}': {len(missing_dates)} dia(s) sem dados no {fonte.nome}: {', '.join(sample)}{suffix}. Execute o fetcher history.py para buscar os dados faltantes.")
                return (False, warnings)
    return (True, warnings)

def pontos_para_tracks(df, city_lower, target_crs):
    """
    Pontos do banco (`_FonteMySQL.pontos` / `_FonteDuckDB.pontos`) → tracks no schema
    de `TRACK_COLUMNS`. Também usada pelo Job 4 (`locar_mysql/heatmap_dia.py`), para o
    dia gravado no banco ler os pontos exatamente como o mapa os lê.
    """
    df = df.copy()
    df['geometry'] = [Point(lon, lat) for lon, lat in zip(df['longitude'], df['latitude'])]
    df['city'] = city_lower
    df['day'] = pd.to_datetime(df['gps_date']).dt.date
    df['vehicle_id'] = df['placa_normalizada']
    df['DATAHORA'] = pd.to_datetime(df['dataHora'])
    df['VELOCIDADE'] = df['velocidade_kmh']
    df['plate_key'] = df['placa_normalizada'].map(normalize_plate_key)
    gdf = gpd.GeoDataFrame(df[['geometry', 'city', 'day', 'vehicle_id', 'plate_key', 'DATAHORA', 'VELOCIDADE']], geometry='geometry', crs='EPSG:4326')
    if target_crs and gdf.crs.to_string() != target_crs:
        gdf = gdf.to_crs(target_crs)
    return gdf

def load_tracks_from_duckdb(date_from, date_to, allowed_cities, target_crs, vehicle_types=None, strict=True):
    """
    Carrega tracks GPS do banco DuckDB (gps.duckdb do inlog_api_project) — ou,
    com GPS_BACKEND=mysql, do MySQL pelos stores do InlogAPI (`_FonteMySQL`).
    O nome ficou por compatibilidade com quem chama.

    Usa vehicle_contract_timeline para descobrir quais placas estavam em
    cada cidade no período, depois busca o histórico GPS dessas placas
    em gps_history.

    Args:
        date_from: Data inicial
        date_to: Data final
        allowed_cities: Cidades a carregar (ex: {"olinda", "cabo"})
        target_crs: CRS alvo para reprojeção dos pontos
        vehicle_types: Tipos de veículo a manter (None → COMPACTADOR)
        strict: Se True (padrão), lacuna de cobertura vira RuntimeError — o
            banco é tudo-ou-nada. Se False, a lacuna vira AVISO e a função
            devolve o que existir (possivelmente vazio): é o modo usado pela
            união DuckDB+shapefiles, em que o que faltar aqui é preenchido
            pelos shapefiles em vez de invalidar o banco inteiro.

    Returns:
        GeoDataFrame com colunas: geometry, city, day, vehicle_id, plate_key,
        DATAHORA, VELOCIDADE — mesmo formato dos shapefiles.

    Raises:
        RuntimeError: Se o banco não existir; e, com strict=True, se os dados
            estiverem incompletos.
    """
    if _gps_backend() == 'mysql':
        abrir = _FonteMySQL
    else:
        try:
            import duckdb
        except ImportError:
            raise RuntimeError("módulo 'duckdb' ausente deste executável/ambiente — o gps.duckdb não pode ser lido. Em dev: pip install duckdb. No .exe: atualize para a versão nova do HeatmapsGenerator.")
        if GPS_DB_PATH is None:
            raise RuntimeError('Banco DuckDB não encontrado. Configure o caminho em config.txt ([GPS_DATABASE] caminho = ...) ou certifique-se de que dependencias/outputs/gps.duckdb existe.')
        if not GPS_DB_PATH.is_file():
            raise RuntimeError(f"Arquivo DuckDB não encontrado: {GPS_DB_PATH}")
        def abrir():
            return _FonteDuckDB(duckdb.connect(str(GPS_DB_PATH), read_only=True))

    allowed_plates, filter_types = _allowed_plates_for_types(vehicle_types)
    if allowed_plates:
        logger.info('[DuckDB] Filtro de veículos %s: %d placas no cache.', sorted(filter_types), len(allowed_plates))
    else:
        logger.warning("[DuckDB] Cache de veículos não encontrado para %s — usando todas as placas. Execute 'Buscar Veículos' no InlogAPI.exe para ativar o filtro.", sorted(filter_types))
    fonte = abrir()
    try:
        is_complete, warnings_list = _validate_duckdb_coverage(fonte, date_from, date_to, allowed_cities)
        if not is_complete:
            msg_lines = [f"Dados incompletos no {fonte.nome}:"]
            for w in warnings_list:
                msg_lines.append(f"  • {w}")
                continue
            msg = """
""".join(msg_lines)
            if strict:
                raise RuntimeError(msg)
            logger.warning("""%s
[DuckDB] Lacuna será coberta pelos shapefiles.""", msg)
            print(f"\n⚠️  {msg}\n⚠️  Os shapefiles vão cobrir o que faltar.\n")
        start_ts, end_ts = _duckdb_window(date_from, date_to)
        all_gdfs = []
        for city in sorted(allowed_cities):
            city_upper = city.upper()
            city_lower = city.lower()
            plates, motivo = _plates_for_contract(fonte, city_upper, start_ts, end_ts, allowed_plates, filter_types)
            if not plates:
                logger.warning('[DuckDB] %s', motivo)
            else:
                df = fonte.pontos(plates, start_ts, end_ts)
                if not df.empty:
                    df = _sem_dias_de_outro_contrato(df, fonte.segmentos_vct(plates, start_ts, end_ts), city_upper)
                if df.empty:
                    logger.warning("[DuckDB] Nenhum ponto GPS retornado para '%s' (%s → %s)", city_upper, date_from, date_to)
                else:
                    gdf = pontos_para_tracks(df, city_lower, target_crs)
                    logger.info("[DuckDB] '%s': %d pontos GPS, %d placas", city_upper, len(gdf), len(plates))
                    all_gdfs.append(gdf)
    finally:
        fonte.close()
    if not all_gdfs:
        if strict:
            raise RuntimeError('Nenhum ponto GPS encontrado no banco GPS para as cidades selecionadas.')
        return _empty_tracks(target_crs)
    result = pd.concat(all_gdfs, ignore_index=True)
    return gpd.GeoDataFrame(result, geometry='geometry', crs=target_crs)

def _load_tracks_from_shapefiles(date_from, date_to, allowed_cities, target_crs, vehicle_types=None, apply_vehicle_type_filter=True, strict=True):
    """
    Carrega tracks a partir dos shapefiles (inlog_automation / RasCol).

    Args:
        vehicle_types: Tipos a manter quando o filtro está ligado
            (None → COMPACTADOR).
        apply_vehicle_type_filter: Se True, cruza a placa com
            veiculos_contratos.xlsx e descarta quem não é do tipo pedido.
            **A união passa False**: o shapefile estar na pasta do contrato +
            data JÁ É a decisão do usuário de incluir aquele veículo, e o
            cadastro do Inlog não conhece metade da frota de Jaboatão. Ver
            `load_tracks_for_range`.
        strict: Se True, ausência de shapefiles/tracks vira RuntimeError. Se
            False, devolve vazio — a união trata o vazio como "nada a
            complementar".

    Returns:
        GeoDataFrame com o schema de `TRACK_COLUMNS`.
    """
    outer_files = glob.glob(os.path.join(PATHS.shapes_root, 'Shapes - *.zip'))
    outer_files += glob.glob(os.path.join(PATHS.shapes_root, '*', 'Shapes - *.zip'))
    if not outer_files:
        if strict:
            raise RuntimeError(f"No 'Shapes - dd.mm.yyyy.zip' under {PATHS.shapes_root}")
        logger.info("[shapefiles] Nenhum 'Shapes - dd.mm.yyyy.zip' em %s.", PATHS.shapes_root)
        return _empty_tracks(target_crs)
    extended_end = (date_to + timedelta(days=1))
    gdfs = []
    for outer_path in sorted(outer_files):
        try:
            visit_date = parse_date_from_outer(outer_path)
        except ValueError:
            continue
        if not (date_from <= visit_date <= extended_end):
            continue
        print(f"Reading tracks for {visit_date} from {outer_path}...")
        gdf_day = read_tracks_from_outer_zip(outer_path, allowed_cities, visit_date)
        if gdf_day.empty:
            print(f"  No tracks for selected cities on {visit_date}")
        else:
            if gdf_day.crs and gdf_day.crs != target_crs:
                gdf_day = gdf_day.to_crs(target_crs)
            gdfs.append(gdf_day)
    if not gdfs:
        if strict:
            raise RuntimeError('No tracks loaded for the selected date range and cities.')
        return _empty_tracks(target_crs)
    tracks = pd.concat(gdfs, ignore_index=True)
    has_vehicle = 'vehicle_id' in tracks.columns
    has_datahora = 'DATAHORA' in tracks.columns
    if has_vehicle and has_datahora:
        tracks['_dedup_dt'] = pd.to_datetime(tracks['DATAHORA'], errors='coerce', dayfirst=True)
        tracks['_dedup_day'] = pd.to_datetime(tracks['day']).dt.normalize()
        tracks['_dedup_real_date'] = tracks['_dedup_dt'].dt.normalize()
        n_before = len(tracks)
        dedup_key = ['vehicle_id', '_dedup_dt']
        tracks['_is_dup'] = tracks.duplicated(subset=dedup_key, keep=False)
        tracks['_dedup_priority'] = 0
        dup_mask = tracks['_is_dup']
        if dup_mask.any():
            tracks.loc[(dup_mask, '_dedup_priority')] = (tracks.loc[(dup_mask, '_dedup_day')] != tracks.loc[(dup_mask, '_dedup_real_date')]).astype(int)
        tracks = tracks.sort_values('_dedup_priority', kind='mergesort')
        tracks = tracks.drop_duplicates(subset=dedup_key, keep='first')
        n_after = len(tracks)
        n_removed = n_before - n_after
        if n_removed > 0:
            print(f"  [dedup] {n_removed} ponto(s) GPS duplicado(s) removido(s) (sobreposição entre shapefiles de datas diferentes)")
        tracks.drop(columns=['_dedup_dt', '_dedup_day', '_dedup_real_date', '_dedup_priority', '_is_dup'], inplace=True)
    elif has_vehicle:
        n_before = len(tracks)
        tracks['_dedup_wkb'] = tracks.geometry.apply((lambda g: g.wkb if not g.is_empty else None))
        tracks = tracks.drop_duplicates(subset=['vehicle_id', '_dedup_wkb'], keep='first')
        tracks.drop(columns=['_dedup_wkb'], inplace=True)
        n_after = len(tracks)
        n_removed = n_before - n_after
        if n_removed > 0:
            print(f"  [dedup] {n_removed} ponto(s) GPS duplicado(s) removido(s)")
    tracks = tracks.reset_index(drop=True)
    if 'vehicle_id' in tracks.columns:
        tracks['plate_key'] = tracks['vehicle_id'].apply(extract_plate_key)
    else:
        tracks['plate_key'] = ''
    if apply_vehicle_type_filter and 'vehicle_id' in tracks.columns:
        if not vehicle_types:
            vehicle_types = ['COMPACTADOR']
        filter_types = {v.upper() for v in vehicle_types}
        allowed_plates = set()
        try:
            from project_heatmaps.user_config import get_plates_by_vehicle_type
            for vt in filter_types:
                p = get_plates_by_vehicle_type(vt)
                if p:
                    allowed_plates |= p
                continue
        except Exception:
            {'COMPACTADOR'}
            allowed_plates = set()
        if allowed_plates:
            allowed_keys = {normalize_plate_key(p) for p in allowed_plates}
            n_before_filter = len(tracks)
            tracks = tracks[tracks['plate_key'].isin(allowed_keys)]
            n_filtered = (n_before_filter - len(tracks))
            if n_filtered > 0:
                print(f"  [vehicle filter] {n_filtered} ponto(s) de fora de {sorted(filter_types)} removido(s) (via veiculos_contratos.xlsx)")
            tracks = tracks.reset_index(drop=True)
        else:
            print(f"  [vehicle filter] Cache não encontrado para {sorted(filter_types)} — usando todas as placas dos shapefiles.")
    return gpd.GeoDataFrame(tracks, geometry='geometry', crs=target_crs)

def _real_date_series(datahora):
    """
    Data-calendário real de cada ponto, a partir do DATAHORA.

    Usada como parte da chave de cobertura. NÃO dá para usar a coluna `day`:
    ela significa coisas diferentes nas duas fontes — no shapefile é a data do
    zip (um ponto das 02:00 do dia D+1 vive no zip do dia D, por causa do turno
    noturno), enquanto no DuckDB é a data do próprio ponto. Cruzar por `day`
    marcaria como "não coberto" o que já está no banco, duplicando a carreta.

    `dayfirst=True` porque o shapefile grava "16/03/2026 06:39:19"; em coluna
    já datetime (DuckDB) o parâmetro é inócuo.
    """
    return pd.to_datetime(datahora, errors='coerce', dayfirst=True).dt.date

def load_tracks_for_range(date_from, date_to, allowed_cities, target_crs, vehicle_types=None):
    """
    Carrega todos os tracks para um intervalo de datas e cidades — **união**
    de DuckDB + shapefiles.

    O DuckDB (`gps.duckdb`) segue como fonte primária e continua filtrado por
    tipo de veículo, porque ele contém a frota inteira e é esse filtro que
    separa quem faz coleta. Os shapefiles entram como COMPLEMENTO: todo
    veículo que estiver na pasta do contrato + data e que o DuckDB **não**
    cobriu naquele dia é incluído, **sem** filtro de tipo.

    O motivo é operacional: o cadastro do Inlog não é a régua do que existe.
    Jaboatão não tem nenhum veículo na API (12 das 15 placas da pasta são
    desconhecidas do cadastro), a API às vezes perde um carro, um veículo pode
    já ter saído da frota quando se refaz um heatmap antigo, e há contrato que
    usa caçamba para coletar. Nesses casos o usuário põe o shapefile na pasta
    certa — e essa é a decisão. Presença na pasta do contrato + data vale mais
    que o cadastro.

    A cobertura é decidida por **(cidade, placa canônica, data real do ponto)**,
    com a placa normalizada dos dois lados: o DuckDB traz "DGG-8F39" e o
    shapefile do RasCol traz "DGG8F39" — sem normalizar, a mesma carreta
    entraria pelas duas fontes e a cobertura seria contada em dobro.

    Args:
        date_from: Data inicial
        date_to: Data final
        allowed_cities: Set de cidades permitidas
        target_crs: CRS alvo para reprojeção
        vehicle_types: Tipos a manter no lado DuckDB (None → COMPACTADOR)

    Returns:
        GeoDataFrame com todos os tracks (schema de `TRACK_COLUMNS`)

    Raises:
        RuntimeError: Se NENHUMA das duas fontes devolver dados
    """
    try:
        duck = load_tracks_from_duckdb(date_from, date_to, allowed_cities, target_crs, vehicle_types=vehicle_types, strict=False)
    except Exception as exc:
        logger.warning('[DuckDB] Indisponível (%s) — os shapefiles vão responder sozinhos.', exc)
        print(f"\n⚠️  AVISO: DuckDB indisponível: {exc}\n")
        duck = _empty_tracks(target_crs)
    if not duck.empty:
        try:
            origem = 'MySQL' if _gps_backend() == 'mysql' else 'gps.duckdb'
        except Exception:
            origem = 'banco GPS'
        logger.info('[DuckDB] %d pontos GPS carregados do banco (%s).', len(duck), origem)
        print(f"[DuckDB] {len(duck):,} pontos GPS carregados ({origem}).")
    try:
        shp = _load_tracks_from_shapefiles(date_from, date_to, allowed_cities, target_crs, vehicle_types=vehicle_types, apply_vehicle_type_filter=False, strict=False)
    except Exception as exc:
        logger.warning('[shapefiles] Falha ao ler os shapefiles: %s', exc)
        print(f"\n⚠️  AVISO: falha ao ler os shapefiles: {exc}\n")
        shp = _empty_tracks(target_crs)
    if not shp.empty and not duck.empty and 'DATAHORA' not in shp.columns:
        logger.warning('[união] Shapefiles sem coluna DATAHORA — impossível cruzar com o DuckDB sem risco de duplicar. Ignorando o complemento.')
        print('  ⚠️  [união] Shapefiles sem DATAHORA — complemento ignorado (risco de duplicar o que já está no DuckDB).')
        shp = _empty_tracks(target_crs)
    if not shp.empty and not duck.empty:
        covered = set(zip(duck['city'].map(_city_key), duck['plate_key'], _real_date_series(duck['DATAHORA'])))
        shp_key = pd.Series(list(zip(shp['city'].map(_city_key), shp['plate_key'], _real_date_series(shp['DATAHORA']))), index=shp.index)
        n_before = len(shp)
        shp = shp[(~shp_key.isin(covered))]
        n_dropped = (n_before - len(shp))
        if n_dropped:
            logger.info('[união] %d ponto(s) de shapefile ignorado(s) — veículo/dia já coberto pelo DuckDB.', n_dropped)
            print(f"  [união] {n_dropped:,} ponto(s) de shapefile ignorado(s) (veículo/dia já coberto pelo DuckDB).")
    if not shp.empty:
        _log_shapefile_additions(shp, vehicle_types)
    parts = [g for g in (duck, shp) if not g.empty]
    if not parts:
        raise RuntimeError('Nenhum ponto GPS encontrado — nem no DuckDB, nem nos shapefiles, para as cidades e o período selecionados.')
    tracks = pd.concat(parts, ignore_index=True)
    return gpd.GeoDataFrame(tracks, geometry='geometry', crs=target_crs)

def _log_shapefile_additions(shp, vehicle_types):
    """
    Lista os veículos que entraram pelos shapefiles, marcando o que o cadastro
    do Inlog diz sobre cada um.

    Puramente informativo — nada é descartado aqui. Serve para o usuário
    enxergar quando um veículo entrou apesar de o cadastro classificá-lo como
    outro tipo, em vez de isso acontecer em silêncio.
    """
    if not vehicle_types:
        vehicle_types = ['COMPACTADOR']
    filter_types = {v.upper() for v in vehicle_types}
    allowed_keys = set()
    try:
        from project_heatmaps.user_config import get_plates_by_vehicle_type
        for vt in filter_types:
            p = get_plates_by_vehicle_type(vt)
            if p:
                allowed_keys |= {normalize_plate_key(x) for x in p}
    except Exception:
        allowed_keys = set()
    pairs = sorted(set(zip(shp['city'].map(_city_key), shp['plate_key'])))
    if not pairs:
        return None
    off_type = [f"{city}/{format_plate(k)}" for city, k in pairs if allowed_keys if k not in allowed_keys]
    print(f"  [shapefiles] {len(pairs)} veículo(s) incluído(s) por estarem na pasta do contrato + data.")
    if off_type:
        msg = (f"  [shapefiles] {len(off_type)} deles não constam como {sorted(filter_types)} no cadastro do Inlog e entraram assim mesmo (a pasta manda): {', '.join(off_type[:10])}" + ('...' if len(off_type) > 10 else ''))
        print(msg)
        logger.info(msg.strip())
        return None

_PREFIXOS_ROTA_V1 = ('Rota_Final_', 'Rota_', 'Ruas_Coleta_')
_ROTA_V1_PARA_V2 = {'street_name': 'name', 'length_m': 'length'}

def _sem_rota_v1_duplicada(files):
    """Tira `Rota_Final_X.gpkg` (ou `Rota_X`/`Ruas_Coleta_X`) quando `X.gpkg`
    está na mesma lista.

    É o arquivo v1 que sobrou ao lado do v2 que o substituiu; lidos os dois,
    cada segmento da rota entraria DUAS vezes no heatmap.
    """
    nomes = {os.path.basename(f) for f in files}
    out = []
    for f in files:
        base = os.path.basename(f)
        pfx = next((p for p in _PREFIXOS_ROTA_V1 if base.startswith(p)), None)
        if pfx and base.lower().endswith('.gpkg') and base[len(pfx):] in nomes:
            continue
        out.append(f)
    return out

def _rota_colunas_v2(gdf):
    """Colunas v1 → v2 (`street_name` → `name`, `length_m` → `length`).

    Com as duas presentes, o valor v2 vence e o v1 só preenche o nulo. Sem
    isso o `concat` de rotas v1 e v2 deixaria metade dos nomes em cada coluna.
    """
    for velho, novo in _ROTA_V1_PARA_V2.items():
        if velho not in gdf.columns:
            continue
        if novo in gdf.columns:
            gdf = gdf.copy()
            gdf[novo] = gdf[novo].where(gdf[novo].notna(), gdf[velho])
            gdf = gdf.drop(columns=[velho])
        else:
            gdf = gdf.rename(columns={velho: novo})
    return gdf

def load_geometries_for_contract(contract_name, kind, target_crs=None, allowed_files=None):
    """
    Carrega shapefiles de um contrato (setores ou rotas).
    
    Args:
        contract_name: Nome completo do contrato (ex: 'VARZEA - DIURNO')
        kind: Tipo ('setores' ou 'rotas')
        target_crs: CRS alvo (opcional)
        allowed_files: Lista de arquivos permitidos (opcional)
        
    Returns:
        GeoDataFrame concatenado
        
    Raises:
        RuntimeError: Se nenhum arquivo for encontrado ou todos falharem
    """
    folder = os.path.join(PATHS.contracts_root, contract_name, kind)
    if not os.path.isdir(folder):
        raise RuntimeError(f"Pasta não encontrada: {folder}")
    if allowed_files is None:
        files = glob.glob(os.path.join(folder, '*.zip'))
        files.extend(glob.glob(os.path.join(folder, '*.gpkg')))
    else:
        allowed_abs = {os.path.abspath(p) for p in allowed_files}
        files = [f for f in glob.glob(os.path.join(folder, '*.zip')) if os.path.abspath(f) in allowed_abs]
    files = _sem_rota_v1_duplicada(files)
    if not files:
        raise RuntimeError(f"Nenhum arquivo encontrado para contrato={contract_name}, kind={kind}")
    def post_sector(gdf, fpath):
        gdf['sector_name'] = extract_route_or_sector_code(fpath)
        prefix, _, _ = split_code_parts(gdf['sector_name'].iloc[0])
        gdf['sector_prefix'] = prefix
        return gdf

    def post_route(gdf, fpath):
        gdf['route_name'] = extract_route_or_sector_code(fpath)
        prefix, _, _ = split_code_parts(gdf['route_name'].iloc[0])
        gdf['route_prefix'] = prefix
        return gdf

    post_processors = {'setores': post_sector, 'rotas': post_route}
    post = post_processors.get(kind.lower(), (lambda g, f: g))
    gdfs = []
    skipped = []
    for f in files:
        try:
            gdf = _rota_colunas_v2(gpd.read_file(f)) if f.lower().endswith('.gpkg') else read_zipped_shapefile(f)
        except Exception as e:
            print(f"[{contract_name}] WARNING: falha ao ler {f}: {e}")
            skipped.append(f)
            continue
        if gdf.empty:
            print(f"[{contract_name}] WARNING: shapefile vazio → {f}")
            skipped.append(f)
        else:
            gdf = post(gdf, f)
            gdfs.append(gdf)
    if not gdfs:
        raise RuntimeError((f"Todos os arquivos ignorados para contrato={contract_name}, kind={kind}. Arquivos:\n" + """
""".join(skipped)))
    result = pd.concat(gdfs, ignore_index=True)
    result = gpd.GeoDataFrame(result, geometry='geometry')
    if target_crs and result.crs != target_crs:
        result = result.to_crs(target_crs)
    return result

def build_route_segments(routes, date_from, date_to, merge_same_geometry=False):
    """
    Constrói segmentos ideais a partir das rotas.
    
    VERSÃO CORRIGIDA: Por padrão, NÃO agrupa rotas diferentes que passam
    pelo mesmo segmento geométrico. Cada rota mantém seus próprios segmentos.
    
    Args:
        routes: GeoDataFrame de rotas
        date_from: Data inicial do intervalo
        date_to: Data final do intervalo
        merge_same_geometry: Se True, mescla rotas com mesma geometria
                            (comportamento antigo). Default: False
        
    Returns:
        GeoDataFrame de segmentos
    """
    routes = routes.copy()
    dates_in_range = [d.date() for d in pd.date_range(date_from, date_to, freq='D')]
    if '_schedule' in routes.columns:
        def _route_has_scheduled_day(schedule):
            if not hasattr(schedule, 'matches_date'):
                return False
            return any((schedule.matches_date(d) for d in dates_in_range))

        routes = routes[routes['_schedule'].apply(_route_has_scheduled_day)]
    else:
        days_in_range = set(pd.date_range(date_from, date_to, freq='D').weekday)
        routes = routes[routes['weekday_set'].apply((lambda s: (isinstance(s, set) and len((s & days_in_range)) > 0)))]
    if routes.empty:
        return gpd.GeoDataFrame(columns=[], geometry='geometry', crs=routes.crs)
    if 'classification' in routes.columns:
        n_before = len(routes)
        filtered_parts = []
        for code, group in routes.groupby('code_full', dropna=False):
            cls_vals = group['classification'].dropna().str.lower().str.strip()
            has_collection = (cls_vals == 'collection').any()
            if has_collection:
                mask = group['classification'].fillna('').str.lower().str.strip().isin({'collection', 'manual'})
                filtered_parts.append(group[mask])
            else:
                filtered_parts.append(group)
            continue
        if filtered_parts:
            routes = pd.concat(filtered_parts, ignore_index=True)
            routes = gpd.GeoDataFrame(routes, geometry='geometry', crs=routes.crs)
        n_travel = (n_before - len(routes))
        if n_travel > 0:
            print(f"  [classification] {n_travel} segmento(s) de deslocamento removido(s) ({len(routes)} restantes)")
    if routes.empty:
        return gpd.GeoDataFrame(columns=[], geometry='geometry', crs=routes.crs)
    if {'u', 'v'}.issubset(routes.columns):
        routes['_edge_key_base'] = routes.apply((lambda r: tuple(sorted((r['u'], r['v'])))), axis=1)
    else:
        routes['_edge_key_base'] = routes.geometry.apply((lambda g: g.wkb_hex if g is not None else None))
    if merge_same_geometry:
        routes['_edge_key'] = routes['_edge_key_base']
    else:
        routes['_edge_key'] = routes.apply((lambda r: (r['_edge_key_base'], str(r.get('code_full', '')))), axis=1)
    grouped = []
    for edge_key, g in routes.groupby('_edge_key'):
        if g.empty:
            continue
        base = g.iloc[0].copy()
        weekday_sets = [s for s in g['weekday_set'] if isinstance(s, set)]
        if weekday_sets:
            pass
        else:
            pass
        base['weekday_set'] = set()
        route_codes = sorted(set(g['code_full'].dropna().astype(str)))
        base['route_codes_all'] = '; '.join(route_codes)
        if route_codes:
            pass
        else:
            pass
        base['route_code_individual'] = ''
        grouped.append(base)
    seg = gpd.GeoDataFrame(grouped, geometry='geometry', crs=routes.crs)
    seg['segment_id'] = range(len(seg))
    seg.drop(columns=['_edge_key', '_edge_key_base'], inplace=True, errors='ignore')
    return seg

def filter_segments_inside_sectors(segments, sectors, containment_threshold=0.99):
    """
    Filtra segmentos mantendo apenas os que estão COMPLETAMENTE contidos
    dentro de pelo menos um setor. A geometria dos segmentos é mantida intacta.
    
    Um segmento é considerado "completamente contido" quando a razão entre
    o comprimento da interseção com o setor e o comprimento total do segmento
    é >= containment_threshold (padrão 0.99, tolerância para float).
    
    IMPORTANTE: Esta função espera receber setores JÁ FILTRADOS pela agenda
    do período selecionado (feito em main.py). Ela NÃO faz filtragem de agenda.
    
    Args:
        segments: GeoDataFrame de segmentos de rotas
        sectors: GeoDataFrame de setores (já filtrados por agenda)
        containment_threshold: Razão mínima de contenção (0.0–1.0).
            1.0 = totalmente dentro; 0.99 = tolerância p/ imprecisão float.
        
    Returns:
        GeoDataFrame de segmentos filtrados (geometria original intacta),
        com coluna sector_names_all indicando setores associados.
    """
    _empty_result = (lambda crs: gpd.GeoDataFrame(columns=(list(segments.columns) + ['sector_names_all']), geometry='geometry', crs=crs))
    if segments.empty or sectors.empty:
        if not segments.empty:
            pass
        else:
            pass
        return _empty_result(segments.crs if not segments.empty else sectors.crs)
    if segments.crs != sectors.crs:
        segments = segments.to_crs(sectors.crs)
    sectors_clean = sectors.copy()
    sectors_clean['geometry'] = sectors_clean.geometry.apply((lambda g: make_valid(g) if not g.is_valid else g))
    sectors_clean = sectors_clean[(sectors_clean.geometry.notna() & (~sectors_clean.geometry.is_empty))]
    if sectors_clean.empty:
        return _empty_result(segments.crs)
    sectors_clean['_sector_geometry'] = sectors_clean.geometry
    sjoin = gpd.sjoin(segments[['segment_id', 'geometry']], sectors_clean[['geometry', 'sector_name', 'sector_prefix', '_sector_geometry']], how='inner', predicate='intersects')
    if sjoin.empty:
        return _empty_result(segments.crs)
    def _containment_ratio(seg_geom, sector_geom):
        """Razão do comprimento do segmento que está dentro do setor."""
        try:
            seg_len = seg_geom.length
            if seg_len == 0:
                return 0.0
            if not seg_geom.is_valid:
                seg_geom = make_valid(seg_geom)
            if not sector_geom.is_valid:
                sector_geom = make_valid(sector_geom)
            inter = seg_geom.intersection(sector_geom)
            if inter is None or inter.is_empty:
                return 0.0
            return inter.length / seg_len
        except Exception:
            return 0.0

    sjoin['_containment'] = sjoin.apply((lambda row: _containment_ratio(row.geometry, row._sector_geometry)), axis=1)
    sjoin_contained = sjoin[sjoin['_containment'] >= containment_threshold].copy()
    if sjoin_contained.empty:
        n_partial = sjoin['segment_id'].nunique()
        print(f"  [filter_segments] ATENÇÃO: {n_partial} segmento(s) intersectam setores mas nenhum está 100% contido (threshold={containment_threshold})")
        return _empty_result(segments.crs)
    sectors_agg = sjoin_contained[['segment_id', 'sector_name']].drop_duplicates().groupby('segment_id')['sector_name'].apply((lambda x: '; '.join(sorted(set((v for v in x if isinstance(v, str))))))).reset_index().rename(columns={'sector_name': 'sector_names_all'})
    kept_ids = set(sectors_agg['segment_id'])
    seg_filtered = segments[segments['segment_id'].isin(kept_ids)].copy()
    seg_result = seg_filtered.merge(sectors_agg, on='segment_id', how='inner')
    n_total = len(segments)
    n_kept = len(seg_result)
    n_removed = n_total - n_kept
    n_partial = (sjoin['segment_id'].nunique() - sjoin_contained['segment_id'].nunique())
    if n_removed > 0:
        print(f"  [filter_segments] {n_removed} segmento(s) removido(s) — {(n_removed - n_partial)} fora dos setores, {n_partial} parcialmente fora ({n_kept} restantes)")
    return seg_result

def _find_macro_column(gdf):
    """
    Detecta a coluna de macrosetor no GeoDataFrame.
    
    Os shapefiles de setores podem ter uma coluna chamada
    "macro" ou "MACROSETOR" (case-insensitive).
    
    Returns:
        Nome da coluna encontrada ou None
    """
    for col in gdf.columns:
        if col.upper() in ('MACRO', 'MACROSETOR'):
            return col

def extract_collection_types(sectors):
    """
    Extrai tipos de coleta únicos da coluna macro/MACROSETOR dos setores.
    
    A coluna contém valores como "CANELA - SELETIVA", "OLINDA - DOMICILIAR".
    A parte após o " - " é o tipo de coleta.
    
    Args:
        sectors: GeoDataFrame de setores (já carregado, com colunas originais)
        
    Returns:
        Dict {tipo_coleta: lista_de_indices} com os tipos encontrados.
        Retorna dict vazio se a coluna não existir ou todos os valores
        forem inválidos.
    """
    macro_col = _find_macro_column(sectors)
    if macro_col is None:
        return {}
    def _extract_type(val):
        if not isinstance(val, str):
            return None
        parts = val.split(' - ', 1)
        return parts[1].strip().upper() if len(parts) > 1 else None

    ctype_series = sectors[macro_col].apply(_extract_type)
    types = {}
    for ctype in ctype_series.dropna().unique():
        mask = ctype_series == ctype
        types[ctype] = sectors.index[mask].tolist()
    return types

def explode_to_lines(geom):
    """
    Converte geometria em lista de LineStrings.
    
    Args:
        geom: Geometria shapely
        
    Returns:
        Lista de LineStrings
    """
    if geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    if isinstance(geom, GeometryCollection):
        out = []
        for g in geom.geoms:
            out.extend(explode_to_lines(g))
            continue
        return out
    return []

def thin_coords(xs, ys, max_points=60):
    """
    Reduz quantidade de pontos preservando início e fim.
    
    Args:
        xs: Lista de longitudes
        ys: Lista de latitudes
        max_points: Máximo de pontos desejado
        
    Returns:
        Tupla (xs_thin, ys_thin)
    """
    n = len(xs)
    if n <= max_points:
        return (xs, ys)
    step = max(1, (n // max_points))
    xs_thin = xs[::step]
    ys_thin = ys[::step]
    if xs_thin[-1] != xs[-1] or ys_thin[-1] != ys[-1]:
        xs_thin.append(xs[-1])
        ys_thin.append(ys[-1])
    return (xs_thin, ys_thin)

