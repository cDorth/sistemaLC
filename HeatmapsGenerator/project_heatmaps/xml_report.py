"""
Relatório XML dia-a-dia do modo histórico.

O modo histórico (`historic_only`) reprocessa o período **um dia por vez** e não
gera mapa nenhum. Este módulo aproveita exatamente esses resultados diários para
escrever um XML com a decomposição que o PDF/TXT não dá: para cada dia, cada
setor, o percentual de conclusão, o veículo principal (o que mais coletou dentro
daquele setor naquele dia), o percentual dele e o somatório dos veículos de apoio.

Um arquivo POR CONTRATO selecionado, escrito assim que aquele contrato termina —
não um consolidado no fim da execução. Uma seleção grande interrompida no meio
deixa em disco os contratos já concluídos, e a retomada é reprocessar só o resto.

Diferença de escopo em relação ao `report_generator`:
  - `report_generator` (modo normal) agrega o PERÍODO INTEIRO por rua;
  - aqui a granularidade é (dia × setor × veículo), sem consolidar os dias.

Regra de crédito por veículo
----------------------------
Cada segmento coletado é creditado a UM único veículo: o que registrou mais
passagens sobre ele (`passage_details`). Isso torna a conta fechada — a soma dos
percentuais dos veículos é o percentual do setor —, ao contrário de contar o
segmento para todo veículo que passou, que somaria acima de 100%.

Segmentos coletados sem passagem atribuível (preenchimento de falha de GPS,
propagação de coleta manual) não são inventados para ninguém: vão para o nó
`<sem_veiculo_identificado>`.

Segmentos de classificação "manual" ficam FORA de todas as contas, igual ao
`compute_overall_collection_ratio` que alimenta o cabeçalho dos mapas.

Nomes dos atributos
-------------------
Cada atributo tem nome ÚNICO no arquivo inteiro e diz a que nível pertence
(`pct_coleta_setor`, `pct_coleta_principal`...). Motivo: o Excel abre o XML
achatado numa tabela só, e nome repetido em níveis diferentes virava
`percentual_conclusao`, `percentual_conclusao2`, `percentual_conclusao3` — sem
dizer qual era qual. O significado de cada um está em `LEGENDA`, que também é
gravada como comentário no topo do arquivo. Até 2026-10-01 os nomes eram
genéricos e o arquivo trazia contagem de SEGMENTOS (`segmentos_previstos`,
`segmentos_coletados`); agora traz METROS, a mesma régua do percentual — então
`pct_coleta_setor` = `metros_coletados_setor` / `metros_previstos_setor`.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd
from project_heatmaps.config import PATHS
from project_heatmaps.parsers import sector_code_sort_key
from project_heatmaps.stats_module import segment_length_weights
logger = logging.getLogger(__name__)
UNIDENTIFIED = '__SEM_VEICULO__'
LEGENDA = {'data_inicial': 'primeiro dia do período selecionado (AAAA-MM-DD)', 'data_final': 'último dia do período selecionado (AAAA-MM-DD)', 'qtd_dias': 'quantidade de dias no período', 'gerado_em': 'quando o arquivo foi gerado', 'data': 'dia a que as linhas se referem (AAAA-MM-DD)', 'sem_setores_agendados': 'true quando nenhum setor tinha coleta prevista no dia', 'contrato': 'contrato, com turno e tipo de coleta', 'pct_coleta_contrato': '% dos metros previstos no dia que tiveram coleta comprovada por GPS, no contrato inteiro', 'pct_coleta_contrato_com_falha_gps': 'igual a pct_coleta_contrato, mas contando como coletado o trecho com possível falha de GPS ou possível coleta manual', 'qtd_trechos_falha_gps_contrato': 'trechos de rua (x dias) com possível falha de GPS no contrato: o carro passou ao lado, mas o GPS não comprova', 'metros_falha_gps_contrato': 'metros (x dias) desses trechos com possível falha de GPS no contrato', 'pct_falha_gps_contrato': '% dos metros previstos do contrato com possível falha de GPS', 'qtd_trechos_possivel_manual_contrato': 'trechos de rua (x dias) com possível coleta manual no contrato: veículo parado na boca da rua, sem passar por ela', 'metros_possivel_manual_contrato': 'metros (x dias) desses trechos com possível coleta manual no contrato', 'pct_possivel_manual_contrato': '% dos metros previstos do contrato com possível coleta manual', 'qtd_setores': 'setores com coleta prevista no contrato naquele dia', 'setor': 'código do setor (ex.: SCD 05)', 'pct_coleta_setor': '% dos metros previstos do setor que tiveram coleta = metros_coletados_setor / metros_previstos_setor', 'metros_previstos_setor': 'metros de rua do setor com coleta prevista no dia', 'metros_coletados_setor': 'metros de rua do setor com coleta comprovada', 'qtd_trechos_falha_gps_setor': 'trechos de rua (x dias) do setor com possível falha de GPS', 'metros_falha_gps_setor': 'metros (x dias) desses trechos com possível falha de GPS no setor', 'pct_falha_gps_setor': '% dos metros previstos do setor com possível falha de GPS', 'qtd_trechos_possivel_manual_setor': 'trechos de rua (x dias) do setor com possível coleta manual', 'metros_possivel_manual_setor': 'metros (x dias) desses trechos com possível coleta manual no setor', 'pct_possivel_manual_setor': '% dos metros previstos do setor com possível coleta manual', 'placa_principal': 'veículo que mais coletou no setor (vazio se nenhum)', 'pct_coleta_principal': 'parte do pct_coleta_setor feita pelo veículo principal', 'metros_coletados_principal': 'metros coletados pelo veículo principal', 'qtd_veiculos_apoio': 'quantos outros veículos coletaram no setor', 'pct_coleta_apoio_soma': 'soma da parte feita pelos veículos de apoio', 'placa_apoio': 'veículo de apoio', 'pct_coleta_apoio': 'parte do pct_coleta_setor feita por este veículo de apoio', 'metros_coletados_apoio': 'metros coletados por este veículo de apoio', 'pct_coleta_sem_veiculo': 'parte do pct_coleta_setor sem veículo identificado (falha de GPS preenchida ou coleta manual propagada)', 'metros_coletados_sem_veiculo': 'metros coletados sem veículo identificado'}

def _credited_vehicle(passage_details):
    """
    Veículo creditado por um segmento: o de maior número de passagens.

    Empate resolvido pela placa (ordem alfabética) para que a mesma entrada
    produza sempre o mesmo XML.
    """
    if (not isinstance(passage_details, (list, tuple)) or not passage_details):
        return None
    best = None
    for entry in passage_details:
        if not isinstance(entry, dict):
            continue
        plate = str((entry.get('v', '') or '')).strip()
        if not plate:
            continue
        try:
            n = int((entry.get('n', 0) or 0))
        except (TypeError, ValueError):
            n = 0
        key = ((-n), plate)
        if best is None or key < best[0]:
            best = (key, plate)
        continue
    return best[1] if best else None

def _sector_rows(segments_map):
    """
    Agrega o `segments_map` de UM dia em uma linha por setor.

    Retorna lista de dicts ordenada por código de setor (número, depois
    prefixo — mesma ordem dos filtros do mapa), cada um com:
      sector, pct, m_expected, m_collected, vehicles (placa -> pct, m_collected)

    Metros só saem quando o motor gravou `segment_length_m`; sem ela o peso
    cai para 1 por segmento e "metros" seria mentira — ficam None.
    """
    if (segments_map is None or len(segments_map) == 0):
        return []
    df = pd.DataFrame(segments_map.drop(columns='geometry', errors='ignore'))
    if df.empty or 'sector_code' not in df.columns:
        return []
    if 'classification' in df.columns:
        cls = df['classification'].fillna('').astype(str).str.lower().str.strip()
        df = df[cls != 'manual'].copy()
    for col in ('total_days', 'missed_days'):
        if col not in df.columns:
            return []
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    df = df[df['total_days'] > 0].copy()
    if df.empty:
        return []
    df['_collected'] = (1.0 - (df['missed_days'] / df['total_days']))
    df['_collected'] = df['_collected'].clip(lower=0.0, upper=1.0)
    if 'passage_details' in df.columns:
        df['_vehicle'] = df['passage_details'].apply(_credited_vehicle)
    else:
        df['_vehicle'] = None
    df['_vehicle'] = df['_vehicle'].fillna(UNIDENTIFIED)
    df['_w'] = segment_length_weights(df)
    for col in ('n_gps_gap_days', 'n_days_possible_manual'):
        if col in df.columns:
            pass
        else:
            pass
        df[col] = 0.0
    has_meters = ('segment_length_m' in df.columns and float(pd.to_numeric(df['segment_length_m'], errors='coerce').fillna(0).sum()) > 0)
    rows = []
    for sector, grp in df.groupby(df['sector_code'].fillna('').astype(str)):
        n_seg = len(grp)
        if n_seg == 0:
            continue
        w_total = float(grp['_w'].sum())
        if w_total <= 0:
            continue
        pct = ((float((grp['_w'] * grp['_collected']).sum()) / w_total) * 100.0)
        vehicles = {}
        for plate, vgrp in grp.groupby('_vehicle'):
            share = ((float((vgrp['_w'] * vgrp['_collected']).sum()) / w_total) * 100.0)
            if share <= 0.0:
                continue
            vehicles[str(plate)] = {
                'pct': share,
                'm_collected': float((vgrp['_w'] * vgrp['_collected']).sum()) if has_meters else None
            }
        perdidos = grp['missed_days'].clip(lower=0)
        man_days = grp['n_days_possible_manual'].clip(lower=0)
        man_days = man_days.where(man_days <= perdidos, perdidos)
        gap_days = grp['n_gps_gap_days'].clip(lower=0)
        gap_days = gap_days.where(gap_days <= (perdidos - man_days), (perdidos - man_days))
        rows.append({
            'n_gps_gap': int(gap_days.sum()),
            'm_gps_gap': float((grp['_w'] * gap_days).sum()) if has_meters else None,
            'pct_gps_gap': float(((grp['_w'] * gap_days / grp['total_days']).sum() / w_total) * 100.0),
            'n_possible_manual': int(man_days.sum()),
            'm_possible_manual': float((grp['_w'] * man_days).sum()) if has_meters else None,
            'pct_possible_manual': float(((grp['_w'] * man_days / grp['total_days']).sum() / w_total) * 100.0),
            'w_total': w_total,
            'sector': sector or '(sem código)',
            'pct': pct,
            'm_expected': w_total if has_meters else None,
            'm_collected': float((grp['_w'] * grp['_collected']).sum()) if has_meters else None,
            'vehicles': vehicles,
        })
    rows.sort(key=(lambda r: sector_code_sort_key(r['sector'])))
    return rows

def _fmt(value):
    """Percentual com 1 casa, ponto decimal (XML não é planilha pt-BR)."""
    return '' if value is None else f"{value:.1f}"

def _fmt_m(value):
    """Metros com 1 casa; vazio quando o motor não mediu comprimento.

    Uma casa, não inteiro: em setor pequeno (1,4 m previstos, 0,6 m coletados)
    o inteiro dava 1/1 = 100 % ao lado de pct_coleta_setor = 42,9 % — achado do
    auditor-claims, 2026-10-01."""
    return '' if value is None else f"{value:.1f}"

def _as_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None

def _ratio_pct(value):
    """`overall_ratio` chega como (strict, com_falha_gps) ou escalar."""
    if value is None:
        return (None, None)
    if isinstance(value, (tuple, list)):
        strict = (float(value[0]) * 100.0)
        gps = (float(value[1]) * 100.0) if len(value) > 1 else strict
        return (strict, gps)
    v = (float(value) * 100.0)
    return (v, v)

def _append_sector(parent, row):
    sector_el = ET.SubElement(parent, 'setor', {'setor': row['sector'], 'pct_coleta_setor': _fmt(row['pct']), 'metros_previstos_setor': _fmt_m(row['m_expected']), 'metros_coletados_setor': _fmt_m(row['m_collected']), 'qtd_trechos_falha_gps_setor': str(row['n_gps_gap']), 'metros_falha_gps_setor': _fmt_m(row['m_gps_gap']), 'pct_falha_gps_setor': _fmt(row['pct_gps_gap']), 'qtd_trechos_possivel_manual_setor': str(row['n_possible_manual']), 'metros_possivel_manual_setor': _fmt_m(row['m_possible_manual']), 'pct_possivel_manual_setor': _fmt(row['pct_possible_manual'])})
    vehicles = dict(row['vehicles'])
    unidentified = vehicles.pop(UNIDENTIFIED, None)
    ranked = sorted(vehicles.items(), key=(lambda kv: ((-kv[1]['pct']), kv[0])))
    if ranked:
        main_plate, main_data = ranked[0]
        ET.SubElement(sector_el, 'veiculo_principal', {'placa_principal': main_plate, 'pct_coleta_principal': _fmt(main_data['pct']), 'metros_coletados_principal': _fmt_m(main_data['m_collected'])})
    else:
        ET.SubElement(sector_el, 'veiculo_principal', {'placa_principal': '', 'pct_coleta_principal': '', 'metros_coletados_principal': ''})
    support = ranked[1:]
    support_el = ET.SubElement(sector_el, 'veiculos_apoio', {'qtd_veiculos_apoio': str(len(support)), 'pct_coleta_apoio_soma': _fmt(sum((d['pct'] for _, d in support)))})
    for plate, data in support:
        ET.SubElement(support_el, 'veiculo_apoio', {'placa_apoio': plate, 'pct_coleta_apoio': _fmt(data['pct']), 'metros_coletados_apoio': _fmt_m(data['m_collected'])})
    if unidentified:
        ET.SubElement(sector_el, 'sem_veiculo_identificado', {'pct_coleta_sem_veiculo': _fmt(unidentified['pct']), 'metros_coletados_sem_veiculo': _fmt_m(unidentified['m_collected'])})
        return None

def _indent(elem, level=0):
    """Indentação in-place (ET.indent só existe no Python 3.9+; isto é neutro)."""
    pad = ("""
""" + ('  ' * level))
    if len(elem):
        if not (elem.text or '').strip():
            elem.text = pad + '  '
        for child in elem:
            _indent(child, (level + 1))
            if not (child.tail or '').strip():
                child.tail = pad + '  '
            continue
        if not (elem[-1].tail or '').strip():
            elem[-1].tail = pad
    if level:
        if not (elem.tail or '').strip():
            elem.tail = pad
            return None
            return None

def _legenda_comment():
    """LEGENDA como comentário XML: quem abre o arquivo num editor ou navegador
    lê o significado de cada coluna sem procurar documentação."""
    largura = max((len(k) for k in LEGENDA))
    linhas = ['', '  LEGENDA DAS COLUNAS (percentuais por metro de rua, 0 a 100; metros com 1 casa decimal)', '']
    linhas += [f"  {k.ljust(largura)}  {v}" for k, v in LEGENDA.items()]
    linhas += ['', '  pct_coleta_principal + pct_coleta_apoio_soma + pct_coleta_sem_veiculo = pct_coleta_setor', '']
    texto = """
""".join(linhas).replace('--', '-')
    return f"<!--{texto}-->\n"

def build_xml_filename(date_from, date_to, contracts, label=None):
    """
    Nome baseado nas DATAS SELECIONADAS (não no instante da geração), para que
    reprocessar o mesmo período sobrescreva o relatório em vez de acumular
    cópias com carimbo de hora.
    """
    dsuf = f"{date_from:%d.%m.%Y}" if date_from == date_to else f"{date_from:%d.%m.%Y}_a_{date_to:%d.%m.%Y}"
    if label:
        prefix = str(label).strip().replace(' ', '_').replace('/', '-')
    else:
        uniq = sorted({str(c).split(' - ')[0].split(' [')[0].strip() for c in contracts if c})
        if len(uniq) == 1:
            prefix = uniq[0].replace(' ', '_').replace('/', '-')
        else:
            prefix = 'Consolidado' if uniq else 'Relatorio'
    return f"Relatorio_Historico_{prefix}_{dsuf}.xml"

def generate_historic_xml_report(results, date_from, date_to, output_dir=None, label=None):
    """
    Escreve o XML dia-a-dia do modo histórico.

    Args:
        results: resultados do modo histórico (um por contrato POR DIA)
        date_from, date_to: período SELECIONADO pelo usuário (nomeia o arquivo)
        output_dir: default `RELATORIOS_HEATMAPS_XML/` (vizinha de `HEATMAPS/`)
        label: contrato selecionado que nomeia o arquivo (ex.: "CABO"). O
               chamador passa UM contrato por vez — ver `_flush_profile_xml`
               em `main.py`.

    Returns:
        Path do XML, ou None se não houver nada a relatar.
    """
    if not results:
        logger.warning('Modo histórico sem resultados — XML não gerado.')
        return None
    usable = [r for r in results if r and not r.get('stops_mode') and not r.get('estacionarios_mode') if r.get('segments_map') is not None]
    if not usable:
        logger.warning('Modo histórico: nenhum resultado de coleta domiciliar — XML não gerado.')
        return None
    by_day = {}
    for r in usable:
        day = _as_date(r.get('date_from'))
        if day is None:
            continue
        by_day.setdefault(day, []).append(r)
    if not by_day:
        logger.warning('Modo histórico: resultados sem data — XML não gerado.')
        return None
    all_days = sorted((set((d.date() for d in pd.date_range(date_from, date_to, freq='D'))) | set(by_day)))
    root = ET.Element('relatorio_historico', {'data_inicial': f"{date_from:%Y-%m-%d}", 'data_final': f"{date_to:%Y-%m-%d}", 'qtd_dias': str(len(all_days)), 'gerado_em': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    total_sectors = 0
    for day in all_days:
        day_el = ET.SubElement(root, 'dia', {'data': f"{day:%Y-%m-%d}"})
        for r in sorted(by_day.get(day, []), key=(lambda x: str(x.get('contract', '')))):
            rows = _sector_rows(r.get('segments_map'))
            if not rows:
                continue
            strict, with_gap = _ratio_pct(r.get('overall_ratio'))
            w_all = sum((x['w_total'] for x in rows))
            def _soma_m(chave):
                vals = [x[chave] for x in rows]
                return None if any((v is None for v in vals)) else sum(vals)

            def _pct_contrato(chave):
                if w_all > 0:
                    pass
                else:
                    pass

            contract_el = ET.SubElement(day_el, 'contrato', {'contrato': str(r.get('contract', '')), 'pct_coleta_contrato': _fmt(strict), 'pct_coleta_contrato_com_falha_gps': _fmt(with_gap), 'qtd_trechos_falha_gps_contrato': str(sum((x['n_gps_gap'] for x in rows))), 'metros_falha_gps_contrato': _fmt_m(_soma_m('m_gps_gap')), 'pct_falha_gps_contrato': _fmt(_pct_contrato('pct_gps_gap')), 'qtd_trechos_possivel_manual_contrato': str(sum((x['n_possible_manual'] for x in rows))), 'metros_possivel_manual_contrato': _fmt_m(_soma_m('m_possible_manual')), 'pct_possivel_manual_contrato': _fmt(_pct_contrato('pct_possible_manual')), 'qtd_setores': str(len(rows))})
            for row in rows:
                _append_sector(contract_el, row)
                total_sectors += 1
        if len(day_el) == 0:
            day_el.set('sem_setores_agendados', 'true')
    if total_sectors == 0:
        logger.warning('Modo histórico: nenhum setor no período — XML não gerado.')
        return None
    output_dir = PATHS.xml_reports_dir if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    contracts = [r.get('contract', '') for r in usable]
    out_path = (output_dir / build_xml_filename(date_from, date_to, contracts, label=label))
    _indent(root)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write("""<?xml version="1.0" encoding="utf-8"?>
""")
        fh.write(_legenda_comment())
        fh.write(ET.tostring(root, encoding='unicode'))
        fh.write("""
""")
    logger.info('🧾 Relatório XML (histórico): %s — %d dia(s), %d registro(s) de setor', out_path, len(all_days), total_sectors)
    return out_path

