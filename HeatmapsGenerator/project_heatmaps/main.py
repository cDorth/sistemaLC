"""
Módulo principal de orquestração do gerador de heatmaps.

Este módulo coordena o processamento de contratos, aplicação
de estatísticas e geração de visualizações.
"""

import argparse
import logging
import os
from datetime import date
from typing import List, Optional, Union
import pandas as pd
import geopandas as gpd
from project_heatmaps.config import PATHS, GEO, TURN, USER_CONFIG, get_contract_city_mapping
from project_heatmaps.janela import minutos_da_janela, recortar_dia_operacional
from project_heatmaps.parsers import parse_date_arg, parse_schedule_from_code, parse_full_schedule_from_code, make_schedule_label, extract_plate_key, normalize_plate_key
from project_heatmaps.geo_processing import load_tracks_for_range, load_geometries_for_contract, build_route_segments, filter_segments_inside_sectors, extract_collection_types, _find_macro_column
from project_heatmaps.stats_module import aggregate_segment_days, compute_segment_missed_days, compute_overall_collection_ratio, compute_overall_with_manual_ratio, build_segments_for_map
from project_heatmaps.stops_stats import compute_stop_scores, compute_overall_stop_ratio, build_stops_for_map, save_daily_stop_stat
from project_heatmaps.stops_plotting import export_stops_map_html, export_stops_mobile_map_html
from project_heatmaps.history import save_daily_collection_stat, load_collection_history
from project_heatmaps.xml_report import generate_historic_xml_report
from project_heatmaps.plotting_mobile_enhanced import export_dual_maps
from project_heatmaps.report_generator import generate_reports
from project_heatmaps.pontos_viciados import load_pontos_viciados, compute_pontos_stats
from project_heatmaps.estacionarios import get_estacionario_types_for_contract, get_required_vehicle_types, load_estacionario_points
from project_heatmaps.estacionarios_plotting import export_estacionarios_map_html
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _operational_window_minutes(contract):
    """Retorna (ini_min, fin_min) para o contrato — sempre com defaults aplicados."""
    return minutos_da_janela(USER_CONFIG.get_time_config(contract))

def _filter_tracks_by_contract_time(tracks, contract, date_from, date_to):
    """
    Restringe os GPS tracks à janela operacional do contrato e reatribui a
    coluna 'day' para o "dia operacional" (não o dia calendário).

    Janela = [date_from + INICIAL, (date_to + 1) + FINAL).

    Defaults: INICIAL=05:00, FINAL=05:00 (janela canônica de 24h por dia).
    Quando FINAL é configurado (ex: 06:30), a janela do último dia se estende
    para a manhã do dia seguinte.

    O 'day' de cada track passa a ser (DATAHORA - INICIAL).date(), de modo
    que pontos da madrugada do dia D+1 são atribuídos ao dia operacional D.
    """
    if 'DATAHORA' not in tracks.columns or tracks.empty:
        return tracks
    ini_min, fin_min = _operational_window_minutes(contract)
    out, n_before = recortar_dia_operacional(tracks, ini_min, fin_min, date_from, date_to)
    n_after = len(out)
    ini_lbl = f"{(ini_min // 60):02d}:{(ini_min % 60):02d}"
    fin_lbl = f"{(fin_min // 60):02d}:{(fin_min % 60):02d}"
    logger.info('[%s] Janela operacional %s (D) → %s (D+1): %d → %d pontos GPS', contract, ini_lbl, fin_lbl, n_before, n_after)
    return out

_FONTE_MAPA = {}

def _fonte_mapa():
    """
    O leitor do Job 4 (`mapa_mysql`), uma conexão por execução. Só com
    GPS_BACKEND=mysql; MAPA_FONTE=arquivos no ambiente volta ao caminho de
    sempre. Sem conexão = arquivos, com o motivo no log (o mapa não deixa de sair).
    """
    if 'f' not in _FONTE_MAPA:
        _FONTE_MAPA['f'] = None
        if os.environ.get('MAPA_FONTE', 'mysql').strip().lower() != 'arquivos':
            try:
                from project_heatmaps.geo_processing import _gps_backend
                if _gps_backend() == 'mysql':
                    from project_heatmaps.mapa_mysql import FonteMapaMySQL
                    _FONTE_MAPA['f'] = FonteMapaMySQL.abrir()
            except Exception as exc:
                logger.warning(f"Mapa do banco indisponível, usando os arquivos: {exc}")
    return _FONTE_MAPA['f']

def _mapa_do_banco(contract, date_from, date_to, turnos):
    """A fonte preparada para (contrato, período), ou None = caminho dos arquivos."""
    fonte = _fonte_mapa()
    if fonte is None:
        return None
    from project_heatmaps.mapa_mysql import MapaInconsistente
    if turnos:
        pastas = [(contract, t) for t in turnos]
    else:
        base, _, turno = contract.rpartition(' - ')
        pastas = [(base, turno)]
    try:
        ok = fonte.preparar(pastas, date_from, date_to, _operational_window_minutes(contract))
    except MapaInconsistente as exc:
        logger.warning(f"[{contract}] mapa pelos arquivos: {exc}")
        return None
    except Exception as exc:
        logger.warning(f"[{contract}] mapa pelos arquivos (banco falhou): {exc}")
        _FONTE_MAPA.pop('f', None)
        return None
    if not ok:
        logger.info(f"[{contract}] mapa pelos arquivos: {fonte.motivo_arquivos}")
        return None
    logger.info(f"[{contract}] mapa do banco: {len(fonte.segmentos):,} trechos do registro de rotas")
    return fonte

def _process_contract_core(contract, sectors, routes, tracks_city, date_from, date_to, generate_map, single_day_mode, day_and_night_mode, generate_mobile=True, stops_mode=False, secure_mode=True, mapa=None):
    """
    Lógica central de processamento de um contrato (ou fatia de tipo de coleta).
    
    Recebe setores e rotas já carregados (possivelmente filtrados por tipo
    de coleta) e executa todo o pipeline: agenda, segmentos, estatísticas,
    histórico e geração de mapas.
    
    Args:
        contract: Nome do contrato (pode incluir sufixo de tipo, ex:
                  "CANELA - DIURNO [SELETIVA]")
        sectors: GeoDataFrame de setores (já carregado, com colunas originais)
        routes: GeoDataFrame de rotas (já carregado, com colunas originais)
        tracks_city: GeoDataFrame com tracks da cidade
        date_from: Data inicial
        date_to: Data final
        generate_map: Se deve gerar visualização
        single_day_mode: Modo dia único
        day_and_night_mode: Modo agregado dia+noite
        generate_mobile: Gerar versão mobile
        stops_mode: Modo de pontos críticos de parada
        secure_mode: Se True, aplica medidas de segurança
        mapa: `mapa_mysql.FonteMapaMySQL` já preparada para o período — segmentos
              do registro de rotas e linhas gravadas pelo Job 4, no lugar de
              `routes` e de `tracks_city` (None = caminho dos arquivos)
        
    Returns:
        Dicionário com resultados ou None se falhar
    """
    if 'sector_name' not in sectors.columns:
        raise RuntimeError("Coluna 'sector_name' não encontrada")
    sectors = sectors.copy()
    sectors['code_full'] = sectors['sector_name']
    sectors['_schedule'] = sectors['code_full'].apply(parse_full_schedule_from_code)
    sectors['weekday_set'] = sectors['_schedule'].apply((lambda s: s.weekday_set))
    sectors['schedule_label'] = sectors['_schedule'].apply(make_schedule_label)
    if 'route_name' not in routes.columns:
        raise RuntimeError("Coluna 'route_name' não encontrada")
    routes = routes.copy()
    routes['code_full'] = routes['route_name']
    routes['_schedule'] = routes['code_full'].apply(parse_full_schedule_from_code)
    routes['weekday_set'] = routes['_schedule'].apply((lambda s: s.weekday_set))
    routes['schedule_label'] = routes['_schedule'].apply(make_schedule_label)
    segments_ideal = mapa.segmentos.copy() if mapa is not None else build_route_segments(routes, date_from, date_to)
    dates_in_range = [d.date() for d in pd.date_range(date_from, date_to, freq='D')]
    def _sector_has_scheduled_day(schedule):
        """Verifica se o Schedule tem pelo menos 1 dia no período."""
        if not hasattr(schedule, 'matches_date'):
            return False
        return any((schedule.matches_date(d) for d in dates_in_range))

    sectors_scheduled = sectors[sectors['_schedule'].apply(_sector_has_scheduled_day)].copy()
    if sectors_scheduled.empty:
        logger.warning(f"[{contract}] Nenhum setor agendado para o período {date_from} → {date_to}")
        return None
    n_filtered = (len(sectors) - len(sectors_scheduled))
    if n_filtered > 0:
        logger.info(f"[{contract}] {n_filtered} setor(es) fora da agenda removido(s) ({len(sectors_scheduled)} restantes)")
    segments_ideal = filter_segments_inside_sectors(segments_ideal, sectors_scheduled)
    if segments_ideal.empty:
        logger.warning(f"No route segments inside sectors for {contract}")
        return None
    if stops_mode:
        segments_stats = compute_stop_scores(tracks_city, segments_ideal, date_from=date_from, date_to=date_to)
        if (segments_stats is None or segments_stats.empty or 'geometry' not in segments_stats.columns or segments_stats.geometry.isna().all()):
            logger.warning(f"[{contract}] Dia {date_from:%d/%m/%Y} sem segmentos válidos para stops_mode")
            return None
        segments_map = build_stops_for_map(segments_ideal, segments_stats, sectors_scheduled)
        overall_stop_ratio = compute_overall_stop_ratio(segments_stats)
        total_stops = int(segments_stats['total_stops'].sum())
        total_stop_time = int(segments_stats['total_stop_time_sec'].sum())
        save_daily_stop_stat(contract, overall_stop_ratio, total_stops, total_stop_time, date_from, day_and_night_mode)
        stops_map_path = None
        stops_mobile_path = None
        if generate_map:
            stops_map_path = export_stops_map_html(contract_name=contract, segments_map=segments_map, sectors=sectors_scheduled, date_from=date_from, date_to=date_to, overall_stop_ratio=overall_stop_ratio, day_and_night_mode=day_and_night_mode, secure_mode=secure_mode)
            if stops_map_path:
                logger.info(f"[{contract}] 🛑 Mapa de paradas: {stops_map_path}")
            if generate_mobile:
                stops_mobile_path = export_stops_mobile_map_html(contract_name=contract, segments_map=segments_map, sectors=sectors_scheduled, date_from=date_from, date_to=date_to, overall_stop_ratio=overall_stop_ratio, day_and_night_mode=day_and_night_mode, secure_mode=secure_mode)
                if stops_mobile_path:
                    logger.info(f"[{contract}] 📱🛑 Mobile paradas: {stops_mobile_path}")
            logger.info(f"[{contract}] 🛑 stops_mode: ratio={overall_stop_ratio:.2%}, sessões={total_stops}, tempo_total={total_stop_time}s")
        result = {'contract': contract, 'date_from': date_from, 'date_to': date_to, 'segments_map': segments_map, 'sectors': sectors_scheduled, 'overall_stop_ratio': overall_stop_ratio, 'total_stops': total_stops, 'total_stop_time_sec': total_stop_time, 'generate_map': generate_map, 'stops_mode': True, 'stops_map_path': stops_map_path, 'stops_mobile_path': stops_mobile_path}
        return result
    if mapa is not None:
        segments_stats, _ = aggregate_segment_days(segments_ideal, mapa.linhas, date_from, date_to)
    else:
        segments_stats, _ = compute_segment_missed_days(tracks_city, segments_ideal, date_from=date_from, date_to=date_to)
    if (segments_stats is None or segments_stats.empty or 'geometry' not in segments_stats.columns or segments_stats.geometry.isna().all()):
        logger.warning(f"[{contract}] Dia {date_from:%d/%m/%Y} sem segmentos válidos")
        return None
    segments_map = build_segments_for_map(segments_ideal, segments_stats, sectors_scheduled)
    overall_ratio = compute_overall_collection_ratio(segments_stats)
    overall_with_manual = compute_overall_with_manual_ratio(segments_stats)
    stats_parquet = save_daily_collection_stat(contract, overall_ratio, date_from, day_and_night_mode, single_day_mode)
    if stats_parquet:
        logger.info(f"[{contract}] Histórico salvo: {stats_parquet}")
    pontos_gdf = load_pontos_viciados(contract, date_from=date_from, date_to=date_to, day_and_night_mode=day_and_night_mode)
    if pontos_gdf is not None:
        pontos_gdf = compute_pontos_stats(tracks_city, pontos_gdf)
        logger.info(f"[{contract}] 📍 Pontos viciados: {len(pontos_gdf)} ponto(s) com paradas registradas: {int(pontos_gdf['total_stops'].sum())}")
    if generate_map:
        map_paths = export_dual_maps(contract_name=contract, segments_map=segments_map, sectors=sectors_scheduled, date_from=date_from, date_to=date_to, single_day_mode=single_day_mode, overall_ratio=overall_ratio, overall_with_manual=overall_with_manual, generate_mobile=generate_mobile, secure_mode=secure_mode, pontos_viciados_gdf=pontos_gdf)
        if map_paths.get('desktop'):
            logger.info(f"[{contract}] 🖥️  Mapa desktop: {map_paths['desktop']}")
        if map_paths.get('mobile'):
            logger.info(f"[{contract}] 📱 Mapa mobile: {map_paths['mobile']}")
    result = {'contract': contract, 'date_from': date_from, 'date_to': date_to, 'segments_map': segments_map, 'sectors': sectors_scheduled, 'overall_ratio': overall_ratio, 'overall_with_manual': overall_with_manual, 'generate_map': generate_map}
    return result

def process_contract(contract, tracks_city, date_from, date_to, generate_map, day_and_night_mode, single_day_mode, generate_mobile=True, stops_mode=False, secure_mode=True, split_by_collection_type=False):
    """
    Processa um contrato completo. Carrega setores e rotas, e opcionalmente
    separa por tipo de coleta (DOMICILIAR, SELETIVA, etc.) se
    split_by_collection_type=True e houver mais de um tipo.
    
    Args:
        contract: Nome do contrato
        tracks_city: GeoDataFrame com tracks da cidade
        date_from: Data inicial
        date_to: Data final
        generate_map: Se deve gerar visualização
        day_and_night_mode: Modo agregado dia+noite
        single_day_mode: Modo dia único
        stops_mode: Modo de pontos críticos de parada
        secure_mode: Se True, aplica medidas de segurança
        split_by_collection_type: Se True, gera mapas separados para cada
                                  tipo de coleta (DOMICILIAR, SELETIVA, etc.)
        
    Returns:
        dict, list[dict] ou None.
        Retorna lista quando split_by_collection_type gera múltiplos mapas.
    """
    label = f"{date_from:%d/%m/%Y}" if date_from == date_to else f"{date_from:%d/%m/%Y} → {date_to:%d/%m/%Y}"
    logger.info(f"Processing contract: {contract} -> {label}")
    tracks_city = _filter_tracks_by_contract_time(tracks_city, contract, date_from, date_to)
    try:
        if day_and_night_mode:
            available_turns = [t for t in TURN if os.path.isdir(os.path.join(PATHS.contracts_root, f"{contract} - {t}", 'setores'))]
            if not available_turns:
                logger.warning(f"[{contract}] Nenhum turno encontrado (verificadas pastas DIURNO/NOTURNO){PATHS.contracts_root}/{contract} - [DIURNO|NOTURNO]/setores")
                return None
            if len(available_turns) < len(TURN):
                logger.info(f"[{contract}] Apenas turno(s) disponível(is): {', '.join(available_turns)}")
            sectors_parts = []
            routes_parts = []
            for t in available_turns:
                sectors_parts.append(load_geometries_for_contract(f"{contract} - {t}", 'setores', target_crs=GEO.projected_crs))
                routes_parts.append(load_geometries_for_contract(f"{contract} - {t}", 'rotas', target_crs=GEO.projected_crs))
                continue
            sectors = pd.concat(sectors_parts, ignore_index=True)
            routes = pd.concat(routes_parts, ignore_index=True)
        else:
            sectors = load_geometries_for_contract(contract, 'setores', target_crs=GEO.projected_crs)
            routes = load_geometries_for_contract(contract, 'rotas', target_crs=GEO.projected_crs)
        if split_by_collection_type:
            collection_types = extract_collection_types(sectors)
            if len(collection_types) > 1:
                logger.info(f"[{contract}] 🗂️  Separando por tipo de coleta: {', '.join(sorted(collection_types.keys()))}")
                split_results = []
                for ctype in sorted(collection_types.keys()):
                    idx_list = collection_types[ctype]
                    sectors_of_type = sectors.loc[idx_list].copy()
                    macro_col_routes = _find_macro_column(routes)
                    if macro_col_routes:
                        routes_mask = routes[macro_col_routes].str.upper().str.contains(ctype.upper(), na=False)
                        routes_of_type = routes[routes_mask].copy()
                        if routes_of_type.empty:
                            routes_of_type = routes.copy()
                    else:
                        routes_of_type = routes.copy()
                    if sectors_of_type.empty:
                        logger.info(f"[{contract}] Tipo '{ctype}': sem setores, pulando.")
                    else:
                        contract_typed = f"{contract} [{ctype}]"
                        logger.info(f"[{contract}] 🗂️  Processando tipo '{ctype}' ({len(sectors_of_type)} setores)")
                        sub_result = _process_contract_core(contract=contract_typed, sectors=sectors_of_type, routes=routes_of_type, tracks_city=tracks_city, date_from=date_from, date_to=date_to, generate_map=generate_map, single_day_mode=single_day_mode, day_and_night_mode=day_and_night_mode, generate_mobile=generate_mobile, stops_mode=stops_mode, secure_mode=secure_mode)
                        if sub_result:
                            split_results.append(sub_result)
                return split_results if split_results else None
            if collection_types:
                ctype = list(collection_types.keys())[0]
                logger.info(f"[{contract}] Tipo de coleta único: '{ctype}' (split não necessário)")
        if stops_mode:
            pass
        else:
            pass
        mapa = _mapa_do_banco(contract, date_from, date_to, available_turns if day_and_night_mode else None)
        return _process_contract_core(contract=contract, sectors=sectors, routes=routes, tracks_city=tracks_city, date_from=date_from, date_to=date_to, generate_map=generate_map, single_day_mode=single_day_mode, day_and_night_mode=day_and_night_mode, generate_mobile=generate_mobile, stops_mode=stops_mode, secure_mode=secure_mode, mapa=mapa)
    except Exception as e:
        logger.error(f"Error processing {contract}: {e}", exc_info=True)
        return None

def process_estacionarios_for_contract(contract_base, tracks_city, date_from, date_to):
    """
    Gera um mapa de Estacionários por tipo presente para o contrato.

    Reusa o cálculo de paradas de pontos viciados (compute_pontos_stats):
    quais veículos pararam em quais pontos, por quanto tempo, em que dia.

    Args:
        contract_base: Ex: "CABO"
        tracks_city: GPS tracks já filtrados para essa cidade (e tipo de veículo)
        date_from, date_to: Período

    Returns:
        Lista de dicts (um por tipo gerado).
    """
    available = get_estacionario_types_for_contract(contract_base)
    if not available:
        logger.info('[%s] Sem tipos de estacionário configurados/encontrados.', contract_base)
        return []
    results = []
    for tipo, vehicle_type in available.items():
        pontos = load_estacionario_points(contract_base, tipo)
        if pontos is None or pontos.empty:
            logger.info("[%s] Tipo '%s': pasta vazia ou shapefile inválido.", contract_base, tipo)
        else:
            from project_heatmaps.user_config import get_plates_by_vehicle_type
            plates = get_plates_by_vehicle_type(vehicle_type)
            tracks_for_type = tracks_city
            if plates and 'vehicle_id' in tracks_city.columns:
                plate_keys = {normalize_plate_key(p) for p in plates}
                mask = tracks_city['vehicle_id'].astype(str).apply(extract_plate_key).isin(plate_keys)
                tracks_for_type = tracks_city[mask].copy()
                logger.info("[%s] Tipo '%s' (%s): %d/%d pontos GPS após filtro de placas.", contract_base, tipo, vehicle_type, len(tracks_for_type), len(tracks_city))
            pontos = compute_pontos_stats(tracks_for_type, pontos)
            n_with_stops = int((pontos['total_stops'] > 0).sum()) if 'total_stops' in pontos.columns else 0
            logger.info('[%s] 📦 %s: %d ponto(s), %d com paradas registradas.', contract_base, tipo, len(pontos), n_with_stops)
            path = export_estacionarios_map_html(contract_base=contract_base, tipo=tipo, pontos=pontos, date_from=date_from, date_to=date_to)
            if path is None:
                continue
            results.append({'contract': contract_base, 'tipo_estacionario': tipo, 'vehicle_type': vehicle_type, 'date_from': date_from, 'date_to': date_to, 'n_pontos': len(pontos), 'n_with_stops': n_with_stops, 'map_path': path, 'estacionarios_mode': True})
    return results

def process_all_contracts(profiles, date_from, date_to, day_and_night_mode, historic_only_mode, single_day_mode, generate_mobile=True, stops_mode=False, generate_report=False, secure_mode=True, split_by_collection_type=False, estacionarios_mode=False):
    """
    Processa todos os contratos selecionados.

    Args:
        profiles: Lista de perfis/contratos base
        date_from: Data inicial
        date_to: Data final
        day_and_night_mode: Modo agregado
        historic_only_mode: Apenas gerar histórico
        single_day_mode: Modo dia único
        stops_mode: Modo de pontos críticos de parada
        generate_report: Gerar relatório (PDF+TXT no modo normal;
                         XML dia-a-dia quando historic_only_mode=True)
        secure_mode: Se True, aplica medidas de segurança
        split_by_collection_type: Se True, gera mapas separados por tipo
                                  de coleta (DOMICILIAR, SELETIVA, etc.)
        estacionarios_mode: Se True, gera o mapa de Estacionários
                            (ex: Caixas Brooks → POLIGUINDASTE) em vez do
                            heatmap padrão de domiciliar/compactador.

    Returns:
        Lista de resultados
    """
    logger.info('Loading tracks for date range...')
    contract_city_map = get_contract_city_mapping(profiles)
    allowed_cities = set(contract_city_map.values())
    if estacionarios_mode:
        vehicle_types = get_required_vehicle_types(profiles)
        if not vehicle_types:
            logger.warning('Modo Estacionários ativo, mas nenhum contrato selecionado tem pasta em dependencias/estacionarios/. Nada a gerar.')
            return []
        logger.info('📦 Modo Estacionários — tipos de veículo necessários: %s', sorted(vehicle_types))
        tracks_all = load_tracks_for_range(date_from, date_to, allowed_cities, GEO.projected_crs, vehicle_types=vehicle_types)
        results = []
        for contract_base in profiles:
            city = contract_base.lower()
            tracks_city = tracks_all[tracks_all['city'].str.lower() == city].copy()
            if tracks_city.empty:
                logger.warning("No tracks for '%s'", city)
            else:
                tracks_city = _filter_tracks_by_contract_time(tracks_city, contract_base, date_from, date_to)
                sub = process_estacionarios_for_contract(contract_base=contract_base, tracks_city=tracks_city, date_from=date_from, date_to=date_to)
                results.extend(sub)
            continue
        return results
    tracks_all = load_tracks_for_range(date_from, date_to, allowed_cities, GEO.projected_crs)
    results = []
    def _collect_result(result):
        """Adiciona resultado(s) à lista, tratando retorno simples ou lista."""
        if result is None:
            return None
        if isinstance(result, list):
            results.extend((r for r in result if r is not None))
            return None
        results.append(result)

    def _flush_profile_xml(contract_base, start_index):
        """
        Escreve o XML do contrato que acabou de ser processado.

        No modo histórico o XML sai POR CONTRATO, assim que o contrato termina —
        não num consolidado no fim da execução. Uma seleção de 8 contratos × 31
        dias que morre no 6º deixa 5 relatórios prontos em disco; consolidado no
        fim deixaria zero.
        """
        if not generate_report or not historic_only_mode:
            return None
        profile_results = results[start_index:]
        if not profile_results:
            return None
        try:
            xml_path = generate_historic_xml_report(profile_results, date_from=date_from, date_to=date_to, label=contract_base)
            if xml_path:
                logger.info(f"🧾 Relatório XML [{contract_base}]: {xml_path}")
                return None
        except Exception as e:
            logger.error(f"Erro ao gerar relatório XML de '{contract_base}': {e}", exc_info=True)
        else:
            return None
        return None

    for contract_base in profiles:
        _profile_start = len(results)
        if day_and_night_mode:
            contract = contract_base
            city = contract_base.lower()
            tracks_city = tracks_all[tracks_all['city'].str.lower() == city].copy()
            if tracks_city.empty:
                logger.warning(f"No tracks for '{city}'")
            elif historic_only_mode:
                for day in pd.date_range(date_from, date_to, freq='D').date:
                    result = process_contract(contract=contract, tracks_city=tracks_city, date_from=day, date_to=day, generate_map=False, day_and_night_mode=day_and_night_mode, single_day_mode=True, generate_mobile=generate_mobile, stops_mode=stops_mode, secure_mode=secure_mode, split_by_collection_type=split_by_collection_type)
                    _collect_result(result)
                    continue
            else:
                result = process_contract(contract=contract, tracks_city=tracks_city, date_from=date_from, date_to=date_to, generate_map=True, day_and_night_mode=day_and_night_mode, single_day_mode=single_day_mode, generate_mobile=generate_mobile, stops_mode=stops_mode, secure_mode=secure_mode, split_by_collection_type=split_by_collection_type)
                _collect_result(result)
        else:
            for turn in TURN:
                contract = f"{contract_base} - {turn}"
                city = contract_base.lower()
                turn_folder = os.path.join(PATHS.contracts_root, contract, 'setores')
                if not os.path.isdir(turn_folder):
                    logger.info(f"Turno '{turn}' não existe para '{contract_base}', pulando.")
                else:
                    tracks_city = tracks_all[tracks_all['city'].str.lower() == city].copy()
                    if tracks_city.empty:
                        logger.warning(f"No tracks for '{city}'")
                    elif historic_only_mode:
                        for day in pd.date_range(date_from, date_to, freq='D').date:
                            result = process_contract(contract=contract, tracks_city=tracks_city, date_from=day, date_to=day, generate_map=False, day_and_night_mode=day_and_night_mode, single_day_mode=True, generate_mobile=generate_mobile, stops_mode=stops_mode, secure_mode=secure_mode, split_by_collection_type=split_by_collection_type)
                            _collect_result(result)
                            continue
                    else:
                        result = process_contract(contract=contract, tracks_city=tracks_city, date_from=date_from, date_to=date_to, generate_map=True, day_and_night_mode=day_and_night_mode, single_day_mode=single_day_mode, generate_mobile=generate_mobile, stops_mode=stops_mode, secure_mode=secure_mode, split_by_collection_type=split_by_collection_type)
                        _collect_result(result)
        _flush_profile_xml(contract_base, _profile_start)
    if generate_report and results and not historic_only_mode:
        try:
            report = generate_reports(results, secure_mode=secure_mode)
            if report.get('txt'):
                logger.info(f"📝 Relatório TXT: {report['txt']}")
            if report.get('pdf'):
                logger.info(f"🔒 Relatório PDF: {report['pdf']}")
        except Exception as e:
            logger.error(f"Erro ao gerar relatórios: {e}", exc_info=True)
    return results

def main():
    """Entry point para execução via linha de comando."""
    parser = argparse.ArgumentParser(description='Gera mapas de execução da coleta a partir de SHAPES/CONTRATOS')
    parser.add_argument('--date-from', required=True, help='Data inicial (DD.MM.YYYY)')
    parser.add_argument('--date-to', required=False, help='Data final (DD.MM.YYYY, opcional)')
    parser.add_argument('--profiles', nargs='+', required=True, help='Lista de contratos a processar')
    parser.add_argument('--single-day', action='store_true', help='Modo dia único')
    parser.add_argument('--historic-only', action='store_true', help='Apenas histórico (sem mapas)')
    parser.add_argument('--day-and-night', action='store_true', help='Modo agregado DIURNO+NOTURNO')
    parser.add_argument('--stops', action='store_true', help='Modo de pontos críticos de parada')
    parser.add_argument('--report', action='store_true', help='Gerar relatório de análise (PDF + TXT; com --historic-only gera XML dia a dia)')
    parser.add_argument('--no-secure', action='store_true', help='Desabilitar medidas de segurança (criptografia, watermark, etc.)')
    parser.add_argument('--split-type', action='store_true', help='Separar mapas por tipo de coleta (DOMICILIAR, SELETIVA, etc.)')
    parser.add_argument('--estacionarios', action='store_true', help='Gerar mapa de Estacionários (ex: Caixas Brooks → POLIGUINDASTE)')
    args = parser.parse_args()
    date_from = parse_date_arg(args.date_from)
    date_to = parse_date_arg(args.date_to) if args.date_to else date_from
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    single_day_mode = args.single_day or date_from == date_to or args.historic_only
    results = process_all_contracts(profiles=args.profiles, date_from=date_from, date_to=date_to, day_and_night_mode=args.day_and_night, historic_only_mode=args.historic_only, single_day_mode=single_day_mode, stops_mode=args.stops, generate_report=args.report, secure_mode=(not args.no_secure), split_by_collection_type=args.split_type, estacionarios_mode=args.estacionarios)
    logger.info(f"Processamento concluído. {len(results)} contratos processados.")

if __name__ == "__main__":
    main()

