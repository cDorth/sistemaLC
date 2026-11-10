"""
Módulo de geração de relatórios de análise de coleta.

Gera dois formatos de saída:
  1. PDF criptografado — relatório visual, não-editável, seguro para compartilhamento
  2. TXT — resumo compacto para copiar/colar em grupos operacionais (WhatsApp)

Os dados são agregados por RUA (coluna 'name' + 'osmid' dos shapefiles).
Apenas as datas selecionadas no pipeline são consideradas — sem histórico externo.

Dependências:
    pip install reportlab pypdf
"""

import io
import logging
import os
from datetime import date
from typing import Dict, List, Optional, Tuple
import geopandas as gpd
import pandas as pd
from project_heatmaps.config import PATHS
from project_heatmaps.stats_module import length_weighted_ratio
from project_heatmaps.watermark import draw_pdf_watermark, generate_self_signed_certificate, sign_pdf
logger = logging.getLogger(__name__)
CRITICAL_MISS_THRESHOLD = 0.5
MAX_STREETS_PER_SECTOR_PDF = 15
MAX_STREETS_PER_SECTOR_WA = 5
MAX_WORST_SECTORS_WA = 5
MAX_MANUAL_STREETS_WA = 3
DEFAULT_PDF_PASSWORD = 'coleta2025'
_STREET_NAME_COLS = ['name', 'NOME', 'nome', 'Nome', 'NM_LOG', 'nm_log', 'NM_TIP_LOG', 'nm_tip_log', 'nome_logra', 'NOME_LOGRA', 'street_name', 'STREET', 'street', 'logradouro', 'LOGRADOURO', 'descricao', 'DESCRICAO', 'label', 'LABEL']
_OSMID_COLS = ['osmid', 'osm_id', 'OSMID', 'OSM_ID']

def _find_column(gdf, candidates):
    """Retorna a primeira coluna candidata que exista e tenha dados."""
    for c in candidates:
        if c in gdf.columns:
            valid = gdf[c].dropna().astype(str).str.strip()
            if (valid != '').sum() > (len(gdf) * 0.05):
                return c

def _aggregate_by_street(df, street_col, osmid_col, has_geometry=False):
    """
    Agrega segmentos por rua.

    Para cada rua (identificada por nome + osmid quando disponível):
    - soma total_days e missed_days de todos os segmentos
    - calcula % de não-coleta da rua inteira
    - calcula metragem total e metragem não-coletada (ponderada)
    - ordena por metros não-coletados (critério operacional)

    Metragem não-coletada = soma de (comprimento_segmento × missed_days / total_days)
    por segmento. Isso dá o impacto real em metros de rua prejudicada.

    Retorna DataFrame com uma linha por rua, ordenado por missed_meters desc.
    """
    df = df.copy()
    df['_street'] = df[street_col].fillna('').astype(str).str.strip()
    df = df[df['_street'] != ''].copy()
    if df.empty:
        return pd.DataFrame()
    group_cols = ['_street']
    if osmid_col and osmid_col in df.columns:
        df['_osmid'] = df[osmid_col].fillna('').astype(str).str.strip()
        group_cols.append('_osmid')
    for c in ('total_days', 'missed_days', 'missed_days_with_gps_gap', 'n_days_possible_manual', 'n_days_manual_collected'):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    if 'segment_length_m' in df.columns:
        df['_len_m'] = pd.to_numeric(df['segment_length_m'], errors='coerce').fillna(0)
    else:
        if has_geometry and 'geometry' in df.columns:
            try:
                crs = df.crs if hasattr(df, 'crs') else None
                if crs and not crs.is_projected:
                    df_proj = df.to_crs('EPSG:31985')
                    df['_len_m'] = df_proj.geometry.length
                else:
                    df['_len_m'] = df.geometry.length
            except Exception:
                df['_len_m'] = 0.0
        df['_len_m'] = 0.0
    df['_miss_ratio'] = 0.0
    mask = df['total_days'] > 0
    df.loc[(mask, '_miss_ratio')] = (df.loc[(mask, 'missed_days')] / df.loc[(mask, 'total_days')])
    df['_missed_m'] = (df['_len_m'] * df['_miss_ratio'])
    df['_total_ld'] = (df['_len_m'] * df['total_days'])
    df['_missed_ld'] = (df['_len_m'] * df['missed_days'])
    agg_dict = {'segment_id': 'count', 'total_days': 'sum', 'missed_days': 'sum', '_len_m': 'sum', '_missed_m': 'sum', '_total_ld': 'sum', '_missed_ld': 'sum'}
    if 'missed_days_with_gps_gap' in df.columns:
        agg_dict['missed_days_with_gps_gap'] = 'sum'
    if 'n_days_possible_manual' in df.columns:
        agg_dict['n_days_possible_manual'] = 'sum'
    if 'n_days_manual_collected' in df.columns:
        agg_dict['n_days_manual_collected'] = 'sum'
    if 'sector_code' in df.columns:
        agg_dict['sector_code'] = 'first'
    result = df.groupby(group_cols, dropna=False).agg(agg_dict).reset_index()
    result.rename(columns={'segment_id': 'n_segments', '_street': 'street_name', '_len_m': 'total_meters', '_missed_m': 'missed_meters'}, inplace=True)
    if '_osmid' in result.columns:
        result.rename(columns={'_osmid': 'osmid'}, inplace=True)
    result['total_meters'] = result['total_meters'].round(0).astype(int)
    result['missed_meters'] = result['missed_meters'].round(0).astype(int)
    result['miss_pct'] = 0.0
    by_len = result['_total_ld'] > 0
    result.loc[(by_len, 'miss_pct')] = ((result.loc[(by_len, '_missed_ld')] / result.loc[(by_len, '_total_ld')]) * 100).round(1)
    by_days = ((~by_len) & result['total_days'] > 0)
    result.loc[(by_days, 'miss_pct')] = ((result.loc[(by_days, 'missed_days')] / result.loc[(by_days, 'total_days')]) * 100).round(1)
    result.drop(columns=['_total_ld', '_missed_ld'], inplace=True)
    result['collection_pct'] = (100.0 - result['miss_pct']).round(1)
    result = result.sort_values(['missed_meters', 'miss_pct'], ascending=[False, False]).reset_index(drop=True)
    return result

def _streets_per_sector_day(df, street_col, sector_col):
    """
    Para cada (setor, dia), conta ruas previstas, atendidas e não atendidas.

    Usa as colunas 'expected_dates' e 'visited_dates' (listas de datas) de cada
    segmento. Uma rua é considerada atendida no dia se ao menos um dos seus
    segmentos foi visitado naquele dia.
    """
    if df.empty or sector_col is None:
        return pd.DataFrame()
    rows = []
    for _, r in df.iterrows():
        street = str(r.get(street_col, '')).strip()
        if not street:
            continue
        sector = str((r.get(sector_col, '') or 'Sem setor')).strip()
        exp = (r.get('expected_dates') or [])
        visited = set((r.get('visited_dates') or []))
        for d in exp:
            rows.append({'sector': sector, 'date': pd.to_datetime(d).normalize(), 'street': street, 'visited': d in visited})
    if not rows:
        return pd.DataFrame()
    expanded = pd.DataFrame(rows)
    street_lvl = expanded.groupby(['sector', 'date', 'street'])['visited'].any().reset_index()
    out = street_lvl.groupby(['sector', 'date']).agg(n_streets=('street', 'nunique'), n_visited=('visited', 'sum')).reset_index()
    out['n_missed'] = (out['n_streets'] - out['n_visited'])
    out = out.sort_values(['sector', 'date']).reset_index(drop=True)
    return out

def analyze_contract(result):
    """
    Analisa resultados de um contrato agregando dados por rua.
    
    Usa APENAS os dados do período selecionado (date_from → date_to),
    sem consultar históricos ou dados de outros períodos.
    """
    if result is None:
        return None
    segments_map = result.get('segments_map')
    if segments_map is None or segments_map.empty:
        return None
    if result.get('stops_mode'):
        return _analyze_stops(result)
    contract = result.get('contract', 'Desconhecido')
    date_from = result.get('date_from')
    date_to = result.get('date_to')
    df = segments_map.copy()
    street_col = _find_column(df, _STREET_NAME_COLS)
    osmid_col = _find_column(df, _OSMID_COLS)
    if not street_col:
        logger.warning(f"[{contract}] Nenhuma coluna de nome de rua encontrada. Colunas disponíveis: {list(df.columns)}")
    has_cls = 'classification' in df.columns
    if has_cls:
        cls = df['classification'].fillna('').str.lower().str.strip()
        df_collection = df[cls != 'manual'].copy()
        df_manual = df[cls == 'manual'].copy()
    else:
        df_collection = df.copy()
        df_manual = df.iloc[0:0].copy()
    turn = None
    if ' - DIURNO' in contract.upper():
        turn = 'DIURNO'
    elif ' - NOTURNO' in contract.upper():
        turn = 'NOTURNO'
    overall = result.get('overall_ratio')
    overall_manual = result.get('overall_with_manual')
    def _unpack(val):
        if isinstance(val, (tuple, list)):
            return (round((val[0] * 100), 1), round((val[-1] * 100), 1))
        if val is not None:
            v = round((val * 100), 1)
            return (v, v)
        return (0.0, 0.0)

    pct_strict, pct_gps = _unpack(overall)
    pct_ms, pct_mg = _unpack(overall_manual)
    if overall_manual is None:
        pct_ms, pct_mg = pct_strict, pct_gps
    sector_col = 'sector_code' if 'sector_code' in df.columns else None
    sectors = {}
    if sector_col:
        pass
    streets_day_df = _streets_per_sector_day(df_collection, street_col, sector_col) if street_col else pd.DataFrame()
    if sector_col and street_col:
        for sname, grp in df_collection.groupby(sector_col, dropna=False):
            sname = str(sname).strip() if pd.notna(sname) else 'Sem setor'
            streets = _aggregate_by_street(grp, street_col, osmid_col, has_geometry=True)
            sector_ratio = length_weighted_ratio(grp) if {'total_days', 'missed_days'} <= set(grp.columns) else None
            sector_pct = round((sector_ratio * 100), 1) if sector_ratio is not None else 0.0
            manual_streets = pd.DataFrame()
            if sector_col in df.columns:
                manual_grp = df[(df[sector_col] == sname & df.get('n_days_possible_manual', pd.Series(0)) > 0)]
                if not manual_grp.empty and street_col:
                    manual_streets = _aggregate_by_street(manual_grp, street_col, osmid_col, has_geometry=True)
            manual_cls_streets = pd.DataFrame()
            if has_cls and street_col:
                mcls_grp = df_manual[df_manual.get(sector_col, pd.Series()) == sname] if sector_col in df_manual.columns else pd.DataFrame()
                if not mcls_grp.empty:
                    manual_cls_streets = _aggregate_by_street(mcls_grp, street_col, osmid_col, has_geometry=True)
            critical_streets = streets[streets['miss_pct'] >= 50.0]
            never_streets = streets[streets['miss_pct'] >= 99.9]
            sector_streets_day = streets_day_df[streets_day_df['sector'] == sname].reset_index(drop=True) if not streets_day_df.empty else pd.DataFrame()
            if not streets.empty:
                pass
            else:
                pass
            sectors[sname] = {'collection_pct': round((100 - sector_pct), 1), 'miss_pct': streets, 'all_streets': critical_streets, 'critical_streets': never_streets, 'never_streets': manual_streets, 'manual_suspect_streets': manual_cls_streets, 'manual_cls_streets': len(grp), 'n_segments': int(streets['street_name'].nunique()), 'n_streets': 0, 'streets_per_day': sector_streets_day}
    sectors_sorted = dict(sorted(sectors.items(), key=(lambda x: x[1]['miss_pct']), reverse=True))
    return {'contract': contract, 'turn': turn, 'date_from': date_from, 'date_to': date_to, 'has_street_names': street_col is not None, 'street_col_used': street_col, 'pct_strict': pct_strict, 'pct_gps': pct_gps, 'pct_manual_strict': pct_ms, 'sectors': sectors_sorted, 'total_segments': len(df)}

def _analyze_stops(result):
    return {'contract': result.get('contract', ''), 'turn': None, 'date_from': result.get('date_from'), 'date_to': result.get('date_to'), 'stops_mode': True, 'overall_stop_ratio': result.get('overall_stop_ratio'), 'total_stops': result.get('total_stops', 0)}

def generate_whatsapp_text(analyses):
    if not analyses:
        return '⚠️ Nenhum dado para gerar relatório.'
    lines = []
    first = analyses[0]
    d_from, d_to = first.get('date_from'), first.get('date_to')
    date_label = f"{d_from:%d/%m/%Y}" if d_from == d_to else f"{d_from:%d/%m/%Y} a {d_to:%d/%m/%Y}"
    lines.append('📊 *RELATÓRIO DE COLETA*')
    lines.append(f"📅 {date_label}")
    lines.append('')
    for a in analyses:
        if a is None:
            continue
        if a.get('stops_mode'):
            ratio = a.get('overall_stop_ratio')
            lines.append(f"🛑 *{a['contract']}*")
            if ratio is not None:
                lines.append(f"   Intensidade: {(ratio * 100):.1f}%")
            lines.append(f"   Paradas: {a.get('total_stops', 0)}")
            lines.append('')
        else:
            contract = a['contract']
            turn = a.get('turn')
            if turn == 'DIURNO':
                pass
            else:
                pass
            emoji = '🌙' if turn == 'NOTURNO' else '📋'
            lines.append(f"{'─' * 30}")
            lines.append(f"{emoji} *{contract}*")
            lines.append('')
            ps, pg = a['pct_strict'], a['pct_gps']
            pms = a.get('pct_manual_strict', ps)
            if abs((pg - ps)) > 0.1:
                lines.append(f"✅ Coleta domiciliar: *{ps:.1f}%* — *{pg:.1f}%*")
            else:
                lines.append(f"✅ Coleta domiciliar: *{ps:.1f}%*")
            if abs((pms - ps)) > 0.1:
                lines.append(f"📦 Com manual: *{pms:.1f}%*")
            lines.append('')
            sectors = a.get('sectors', {})
            shown = 0
            for sname, info in sectors.items():
                if info['miss_pct'] < 5.0 and len(info.get('critical_streets', [])) == 0:
                    continue
                shown += 1
                if shown > MAX_WORST_SECTORS_WA:
                    '☀️'
                    break
                lines.append(f"📍 *{sname}* — {info['collection_pct']:.0f}% coletado")
                crit = info.get('critical_streets')
                if crit is not None and not crit.empty:
                    for _, row in crit.head(MAX_STREETS_PER_SECTOR_WA).iterrows():
                        missed_m = row.get('missed_meters', 0)
                        m_label = f"{(missed_m / 1000):.1f}km" if missed_m >= 1000 else f"{missed_m}m"
                        lines.append(f"   🔴 {row['street_name']} — {row['miss_pct']:.0f}% sem coleta ({m_label})")
                        continue
                ms = info.get('manual_suspect_streets')
                if ms is not None and not ms.empty:
                    lines.append('   🔵 Suspeita coleta manual:')
                    for _, row in ms.head(MAX_MANUAL_STREETS_WA).iterrows():
                        lines.append(f"   • {row['street_name']}")
                        continue
                lines.append('')
            ok = sum((1 for info in sectors.values() if info['miss_pct'] < 5.0))
            if ok > 0:
                lines.append(f"✅ {ok} setor(es) com coleta adequada (>95%)")
                lines.append('')
    lines.append(f"{'─' * 30}")
    lines.append('_Gerado automaticamente — monitoramento de coleta._')
    return """
""".join(lines)

def _generate_pdf(analyses, output_path, password=DEFAULT_PDF_PASSWORD, secure_mode=True):
    """Gera PDF visual com reportlab. Se secure_mode, adiciona watermark e criptografa."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm, mm
        from reportlab.lib.colors import HexColor, white, black
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
        from pypdf import PdfReader, PdfWriter
    except ImportError as e:
        logger.warning(f"Dependência faltando para PDF: {e}")
        return False
    C_DARK = HexColor('#1B2631')
    C_PRIMARY = HexColor('#1B4F72')
    C_ACCENT = HexColor('#2E86C1')
    C_GREEN = HexColor('#27AE60')
    C_YELLOW = HexColor('#F39C12')
    C_RED = HexColor('#E74C3C')
    C_LIGHT_GREEN = HexColor('#D5F5E3')
    C_LIGHT_YELLOW = HexColor('#FEF9E7')
    C_LIGHT_RED = HexColor('#FADBD8')
    C_LIGHT_BLUE = HexColor('#D6EAF8')
    C_LIGHT_GRAY = HexColor('#F2F3F4')
    C_GRAY = HexColor('#888888')
    C_WHITE = white
    def _miss_bg(pct):
        if pct >= 50:
            return C_LIGHT_RED
        if pct >= 25:
            return HexColor('#FDEBD0')
        if pct >= 10:
            return C_LIGHT_YELLOW
        return C_LIGHT_GREEN

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle('CustomTitle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=20, textColor=C_PRIMARY, spaceAfter=6)
    style_subtitle = ParagraphStyle('CustomSubtitle', parent=styles['Normal'], fontName='Helvetica', fontSize=11, textColor=C_GRAY, spaceAfter=14)
    style_h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=14, textColor=C_PRIMARY, spaceBefore=16, spaceAfter=8)
    style_h3 = ParagraphStyle('H3', parent=styles['Heading3'], fontName='Helvetica-Bold', fontSize=11, textColor=C_ACCENT, spaceBefore=12, spaceAfter=4)
    style_body = ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=C_DARK, leading=12)
    style_small = ParagraphStyle('Small', parent=styles['Normal'], fontName='Helvetica', fontSize=8, textColor=C_GRAY)
    style_cell = ParagraphStyle('Cell', fontName='Helvetica', fontSize=8, textColor=C_DARK, leading=10)
    style_cell_bold = ParagraphStyle('CellBold', fontName='Helvetica-Bold', fontSize=8, textColor=C_DARK, leading=10)
    style_cell_center = ParagraphStyle('CellCenter', fontName='Helvetica-Bold', fontSize=8, textColor=C_DARK, leading=10, alignment=TA_CENTER)
    style_header_cell = ParagraphStyle('HeaderCell', fontName='Helvetica-Bold', fontSize=8, textColor=C_WHITE, leading=10, alignment=TA_CENTER)
    def _styled_table(header, data_rows, col_widths=None, cell_colors=None):
        """
        Cria tabela com estilo visual consistente.
        
        cell_colors: lista de dicts {(row_1based, col): HexColor}
        """
        table_data = [[Paragraph(h, style_header_cell) for h in header]]
        table_data.extend(data_rows)
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        style_cmds = [('BACKGROUND', (0, 0), (-1, 0), C_PRIMARY), ('TEXTCOLOR', (0, 0), (-1, 0), C_WHITE), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, 0), 8), ('ALIGN', (0, 0), (-1, 0), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#CCCCCC')), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [C_WHITE, C_LIGHT_GRAY]), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4), ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6)]
        if cell_colors:
            for (ri, ci), color in cell_colors.items():
                style_cmds.append(('BACKGROUND', (ci, ri), (ci, ri), color))
                continue
        t.setStyle(TableStyle(style_cmds))
        return t

    def _pct_cell(pct):
        """Cria Paragraph de porcentagem."""
        return Paragraph(f"{pct:.0f}%", style_cell_center)

    story = []
    first_a = analyses[0]
    d_from, d_to = first_a.get('date_from'), first_a.get('date_to')
    if isinstance(d_from, date):
        date_label = f"{d_from:%d/%m/%Y}" if d_from == d_to else f"{d_from:%d/%m/%Y} a {d_to:%d/%m/%Y}"
    else:
        date_label = str(d_from)
    story.append(Paragraph('Relatório de Análise de Coleta', style_title))
    story.append(Paragraph(f"Período: {date_label}", style_subtitle))
    story.append(Spacer(1, 8))
    for idx, a in enumerate(analyses):
        if a is None:
            continue
        if idx > 0:
            story.append(PageBreak())
        if a.get('stops_mode'):
            story.append(Paragraph(f"{a['contract']} — Paradas", style_h2))
            ratio = a.get('overall_stop_ratio')
            val = f"{(ratio * 100):.1f}%" if ratio else 'N/A'
            story.append(Paragraph(f"Intensidade: <b>{val}</b> | Paradas: <b>{a.get('total_stops', 0)}</b>", style_body))
        else:
            turn_label = ''
            if a.get('turn'):
                turn_label = ' — Diurno' if a['turn'] == 'DIURNO' else ' — Noturno'
            story.append(Paragraph(f"{a['contract']}{turn_label}", style_h2))
            ps, pg = a['pct_strict'], a['pct_gps']
            pms = a.get('pct_manual_strict', ps)
            pct_text = f"{ps:.1f}% — {pg:.1f}%" if abs((pg - ps)) > 0.1 else f"{ps:.1f}%"
            summary_lines = [f"Coleta domiciliar: <b>{pct_text}</b>"]
            if abs((pms - ps)) > 0.1:
                summary_lines.append(f"Com manual: <b>{pms:.1f}%</b>")
            story.append(Paragraph(' | '.join(summary_lines), style_body))
            story.append(Spacer(1, 10))
            sectors = a.get('sectors', {})
            if sectors:
                story.append(Paragraph('Resumo por Setor', style_h3))
                header = ['Setor', 'Coleta', 'Ruas', 'Críticas', 'Susp. manual']
                rows = []
                colors = {}
                w = [(6.5 * cm), (2.2 * cm), (1.8 * cm), (2.5 * cm), (3 * cm)]
                for ri, (sname, info) in enumerate(sectors.items(), 1):
                    crit_n = len(info.get('critical_streets', []))
                    ms_n = len(info.get('manual_suspect_streets', []))
                    n_streets = int((info.get('n_streets', 0) or 0))
                    rows.append([Paragraph(sname, style_cell_bold), _pct_cell(info['collection_pct']), Paragraph(str(n_streets) if n_streets > 0 else '—', style_cell_center), Paragraph(str(crit_n) if crit_n > 0 else '—', style_cell_center), Paragraph(str(ms_n) if ms_n > 0 else '—', style_cell_center)])
                    colors[(ri, 1)] = _miss_bg(info['miss_pct'])
                    if crit_n > 0:
                        colors[(ri, 3)] = C_LIGHT_RED
                    if ms_n > 0:
                        colors[(ri, 4)] = C_LIGHT_BLUE
                story.append(_styled_table(header, rows, w, colors))
                story.append(Spacer(1, 14))
                multi_day = any(((isinstance(info.get('streets_per_day'), pd.DataFrame) and len(info['streets_per_day']) > 1) for info in sectors.values()))
                if multi_day:
                    story.append(Paragraph('Ruas por Setor/Dia', style_h3))
                    header_d = ['Setor', 'Dia', 'Total ruas', 'Atendidas', 'Não atendidas']
                    w_d = [(6.0 * cm), (2.5 * cm), (2.0 * cm), (2.5 * cm), (3.0 * cm)]
                    rows_d = []
                    colors_d = {}
                    ri = 0
                    for sname, info in sectors.items():
                        spd = info.get('streets_per_day')
                        if spd is None or spd.empty:
                            continue
                        for _, drow in spd.iterrows():
                            ri += 1
                            d_val = drow['date']
                            d_lab = d_val.strftime('%d/%m/%Y') if isinstance(d_val, pd.Timestamp) else str(d_val)
                            n_t = int(drow['n_streets'])
                            n_v = int(drow['n_visited'])
                            n_m = int(drow['n_missed'])
                            miss_pct = ((n_m / n_t) * 100) if n_t else 0
                            rows_d.append([Paragraph(sname, style_cell), Paragraph(d_lab, style_cell_center), Paragraph(str(n_t), style_cell_center), Paragraph(str(n_v), style_cell_center), Paragraph(str(n_m) if n_m > 0 else '—', style_cell_center)])
                            colors_d[(ri, 4)] = _miss_bg(miss_pct)
                    story.append(_styled_table(header_d, rows_d, w_d, colors_d))
                    story.append(Spacer(1, 14))
            for sname, info in sectors.items():
                has_problem = (info['miss_pct'] >= 5 or len(info.get('critical_streets', [])) > 0 or len(info.get('manual_suspect_streets', [])) > 0)
                if not has_problem:
                    continue
                story.append(Paragraph(f"{sname} — {info['collection_pct']:.0f}% de coleta", style_h3))
                all_streets = info.get('all_streets')
                if all_streets is not None or not all_streets.empty:
                    bad_streets = all_streets[all_streets['miss_pct'] >= 10.0].head(MAX_STREETS_PER_SECTOR_PDF)
                    if not bad_streets.empty:
                        header = ['Rua', 'Sem coleta', 'Metros s/ coleta']
                        w_st = [(9.5 * cm), (3 * cm), (3.5 * cm)]
                        rows = []
                        colors = {}
                        for ri, (_, row) in enumerate(bad_streets.iterrows(), 1):
                            osmid_label = ''
                            if 'osmid' in row and row.get('osmid'):
                                osmid_label = f" <font size='6' color='#888888'>[{row['osmid']}]</font>"
                            missed_m = row.get('missed_meters', 0)
                            m_label = f"{(missed_m / 1000):.1f} km" if missed_m >= 1000 else f"{missed_m} m"
                            rows.append([Paragraph(f"{row['street_name']}{osmid_label}", style_cell), _pct_cell(row['miss_pct']), Paragraph(m_label, style_cell_center)])
                            colors[(ri, 1)] = _miss_bg(row['miss_pct'])
                            continue
                        story.append(KeepTogether([Paragraph('Ruas com déficit de coleta (ordenadas por metragem prejudicada):', style_small), Spacer(1, 2), _styled_table(header, rows, w_st, colors)]))
                        story.append(Spacer(1, 6))
                ms = info.get('manual_suspect_streets')
                if ms is not None and not ms.empty:
                    story.append(Paragraph('Suspeita de coleta manual (GPS não passou pelo trecho):', style_small))
                    for _, row in ms.head(8).iterrows():
                        story.append(Paragraph(f"&bull; {row['street_name']}", style_body))
                        continue
                    story.append(Spacer(1, 6))
    story.append(Spacer(1, 20))
    story.append(Paragraph('Gerado automaticamente pelo sistema de monitoramento de coleta. Documento protegido — reprodução proibida.', style_small))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=(1.5 * cm), rightMargin=(1.5 * cm), topMargin=(1.5 * cm), bottomMargin=(1.5 * cm), title='Relatório de Coleta', author='Departamento de Inteligência Estratégica - DIE')
    if secure_mode:
        def _page_watermark(canvas_obj, doc_obj):
            draw_pdf_watermark(canvas_obj, doc_obj.pagesize[0], doc_obj.pagesize[1])

        doc.build(story, onFirstPage=_page_watermark, onLaterPages=_page_watermark)
    else:
        doc.build(story)
    buf.seek(0)
    if secure_mode:
        reader = PdfReader(buf)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
            continue
        writer.encrypt(user_password=password, owner_password=(password + '_owner'), permissions_flag=4)
        with open(output_path, 'wb') as f:
            writer.write(f)
    else:
        with open(output_path, 'wb') as f:
            f.write(buf.read())
    return True

def generate_reports(results, output_dir=None, pdf_password=DEFAULT_PDF_PASSWORD, secure_mode=True):
    """
    Gera relatórios PDF + TXT a partir dos resultados.

    Se secure_mode=True: PDF com criptografia, marca d'água e assinatura digital.
    Se secure_mode=False: PDF simples, sem proteções.

    Apenas os dados do período selecionado são utilizados.

    Args:
        results: Lista de dicts retornados por process_contract()
        output_dir: Diretório de saída (default: HEATMAPS/)
        pdf_password: Senha para abrir o PDF (usado apenas com secure_mode)
        secure_mode: Se True, aplica medidas de segurança ao PDF

    Returns:
        Dict com "pdf", "txt", "whatsapp_text"
    """
    if not results:
        logger.warning('Nenhum resultado para gerar relatório')
        return {'pdf': None, 'txt': None, 'whatsapp_text': ''}
    if output_dir is None:
        output_dir = str(PATHS.heatmaps_dir)
    os.makedirs(output_dir, exist_ok=True)
    analyses = [a for r in results if r if (a := analyze_contract(r)) is not None]
    if not analyses:
        return {'pdf': None, 'txt': None, 'whatsapp_text': ''}
    d_from = results[0].get('date_from')
    d_to = results[0].get('date_to')
    if isinstance(d_from, date):
        dsuf = f"{d_from:%d.%m.%Y}" if d_from == d_to else f"{d_from:%d.%m.%Y}_a_{d_to:%d.%m.%Y}"
    else:
        dsuf = 'sem_data'
    contracts = list(set((a['contract'] for a in analyses if not a.get('stops_mode'))))
    if len(contracts) == 1:
        pass
    else:
        pass
    prefix = 'Consolidado' if contracts else 'Relatorio'
    base = f"Relatorio_{prefix}_{dsuf}"
    whatsapp_text = generate_whatsapp_text(analyses)
    txt_path = os.path.join(output_dir, f"{base}.txt")
    try:
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(whatsapp_text)
        logger.info(f"📝 Texto WhatsApp: {txt_path}")
    except Exception as e:
        logger.error(f"Falha TXT: {e}")
        txt_path = None
    pdf_path = os.path.join(output_dir, f"{base}.pdf")
    pdf_ok = False
    try:
        pdf_ok = _generate_pdf(analyses, pdf_path, pdf_password, secure_mode=secure_mode)
        if pdf_ok:
            if secure_mode:
                logger.info(f"🔒 PDF criptografado: {pdf_path}")
                try:
                    pfx_path = generate_self_signed_certificate()
                    signed = sign_pdf(pdf_path, pfx_path)
                    if signed:
                        logger.info(f"✍️  PDF assinado: {pdf_path}")
                    else:
                        logger.info('Assinatura digital não aplicada (instale endesive para habilitar)')
                except Exception as e:
                    logger.warning(f"Assinatura digital ignorada: {e}")
            logger.info(f"📄 PDF gerado (sem proteções): {pdf_path}")
    except Exception as e:
        logger.error(f"Erro PDF: {e}", exc_info=True)
    if pdf_ok:
        pass
    else:
        pass
    return {'pdf': None, 'txt': txt_path, 'whatsapp_text': whatsapp_text}

