# pycrefine: 2 places could not be reconstructed reliably; treat the source below as unverified there:
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in ScheduleRule at SETUP_ANNOTATIONS (offset 10); Python 3.11]
#   - unsupported_opcode: no handler for SETUP_ANNOTATIONS [in Schedule at SETUP_ANNOTATIONS (offset 10); Python 3.11]
"""
Funções de parsing de códigos, agendas e datas.

Este módulo contém toda a lógica de extração e interpretação de
códigos de rotas/setores e suas agendas.

CHANGELOG:
    - Adicionado suporte a agendamento ordinal (ex: SAB 1, SAB L, TER 1 2 E SAB 3 L)
    - Novas classes: ScheduleRule, Schedule
    - Nova função: parse_full_schedule_from_code()
    - parse_schedule_from_code() mantém retrocompatibilidade (retorna Set[int])
    - compute_schedule_days_for_code() agora respeita restrições ordinais
    - make_schedule_label() aceita Schedule e gera labels descritivos
"""

import re
import os
import calendar
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Set, List, Optional, FrozenSet, Union
import pandas as pd
from project_heatmaps.config import DAY_ORDER, WEEKDAY_MAP_DAY_TO_CODE, WEEKDAY_MAP_CODE_TO_DAY, PREFIX_DEFAULT_DAYS, PREFIX_SUFFIX_ONLY, OUTER_DATE_REGEX, INNER_CITY_REGEX
@dataclass(frozen=True)
class ScheduleRule:
    """
    Uma regra de agendamento: dia da semana + restrição temporal.
    
    Modos (mutuamente exclusivos):
        1. Sem restrição: ordinals vazio, anchor None → toda ocorrência do weekday
        2. Ordinal: ordinals preenchido → apenas Nª ocorrência no mês
        3. Quinzenal (QZ): anchor preenchido → semanas alternadas a partir da âncora
    
    Attributes:
        weekday: Dia da semana (0=SEG, 1=TER, ..., 6=DOM)
        ordinals: Ocorrências no mês. {1, 3} = 1ª e 3ª. {'L'} = última. Vazio = todas.
        anchor: Data de referência para modo quinzenal (QZ). None = não é quinzenal.
    
    Examples:
        >>> ScheduleRule(5, frozenset())                        # todo SAB
        >>> ScheduleRule(5, frozenset({1}))                     # 1º SAB do mês
        >>> ScheduleRule(5, frozenset({'L'}))                   # último SAB do mês
        >>> ScheduleRule(3, anchor=date(2025, 1, 2))            # QUI quinzenal (ref 02/01/2025)
    """
    weekday: int
    ordinals: FrozenSet[Union[(int, str)]] = field(default_factory=frozenset)
    anchor: Optional[date] = None

@dataclass
class Schedule:
    """
    Agenda completa de uma rota/setor, composta por uma ou mais regras.
    
    Examples:
        >>> # TER 1 2 E SAB 3 L
        >>> Schedule([
        ...     ScheduleRule(1, frozenset({1, 2})),
        ...     ScheduleRule(5, frozenset({3, 'L'})),
        ... ])
        >>> # QUI quinzenal a partir de 02/01/2025
        >>> Schedule([ScheduleRule(3, anchor=date(2025, 1, 2))])
    """
    rules: List[ScheduleRule] = field(default_factory=list)
    @property
    def weekday_set(self):
        """Retrocompatível: retorna set de weekdays (ignora ordinais/anchor)."""
        return {r.weekday for r in self.rules}

    def has_ordinals(self):
        """True se alguma regra tem restrição ordinal."""
        return any((r.ordinals for r in self.rules))

    def has_biweekly(self):
        """True se alguma regra é quinzenal (QZ)."""
        return any((r.anchor is not None for r in self.rules))

    def matches_date(self, d):
        """
        Verifica se uma data satisfaz a agenda.
        
        Args:
            d: Data a verificar
            
        Returns:
            True se a data corresponde a alguma regra
        """
        wd = d.weekday()
        for rule in self.rules:
            if rule.weekday != wd:
                continue
            if rule.anchor is not None:
                days_diff = (d - rule.anchor).days
                weeks_diff = days_diff // 7
                if (weeks_diff % 2) == 0:
                    return True
            else:
                if not rule.ordinals:
                    return True
                nth = _nth_weekday_in_month(d)
                if nth in rule.ordinals:
                    return True
                if 'L' in rule.ordinals and _is_last_weekday_in_month(d):
                    return True
        return False

def _nth_weekday_in_month(d):
    """
    Retorna qual ocorrência do dia da semana esta data é no mês.
    
    Ex: se d é a 2ª terça-feira do mês, retorna 2.
    """
    return (((d.day - 1) // 7) + 1)

def _is_last_weekday_in_month(d):
    """
    Verifica se esta data é a última ocorrência do dia da semana no mês.
    """
    days_in_month = calendar.monthrange(d.year, d.month)[1]
    return (d.day + 7) > days_in_month

def parse_date_arg(date_str):
    """Converte string 'DD.MM.YYYY' em datetime.date."""
    return datetime.strptime(date_str, '%d.%m.%Y').date()

def parse_date_from_outer(path):
    """Extrai data do nome de arquivo outer zip."""
    fname = os.path.basename(path)
    m = OUTER_DATE_REGEX.match(fname)
    if not m:
        raise ValueError(f"Outer zip does not match 'Shapes - dd.mm.yyyy.zip': {fname}")
    ds = m.group('date')
    return datetime.strptime(ds, '%d.%m.%Y').date()

def parse_city_and_date_from_inner(name):
    """Extrai cidade e data do nome de arquivo inner zip."""
    m = INNER_CITY_REGEX.match(os.path.basename(name))
    if not m:
        return (None, None)
    city = m.group('city')
    ds = m.group('date')
    return (city, datetime.strptime(ds, '%d.%m.%Y').date())

PLATE_REGEX = re.compile('[A-Z]{3}-?[A-Z0-9]{4}', re.IGNORECASE)

def normalize_plate_key(plate):
    """
    Chave canônica de placa: maiúscula, sem hífen/espaço/ponto.

    Usada dos DOIS lados de qualquer comparação de placa (track × allowlist)
    para que a origem do dado (Inlog ou RasCol) não altere o resultado.

    Examples:
        >>> normalize_plate_key('FJP-7H44')
        'FJP7H44'
        >>> normalize_plate_key('dgg8f39')
        'DGG8F39'
    """
    return re.sub('[^A-Z0-9]', '', str(plate).upper())

def extract_plate_key(vehicle_id):
    """
    Extrai a placa do `vehicle_id` de um shapefile e devolve a chave canônica.

    O `vehicle_id` é o nome do .shp sem extensão, no formato
    "DD.MM.YYYY_PLACA_CONTRATO". A busca é feita primeiro token a token
    (separados por "_") com match completo — assim um nome de contrato longo
    como "JABOATAO" nunca é confundido com uma placa. Só se nenhum token for
    uma placa é que caímos para uma busca solta na string inteira.

    Returns:
        Chave canônica da placa, ou a chave do `vehicle_id` inteiro se nenhuma
        placa for reconhecida.

    Examples:
        >>> extract_plate_key('16.03.2026_FJP-7H44_CABO')
        'FJP7H44'
        >>> extract_plate_key('16.03.2026_DGG8F39_JABOATAO')
        'DGG8F39'
        >>> extract_plate_key('DGG-8F39')
        'DGG8F39'
    """
    vid = str(vehicle_id)
    for token in re.split('[_\\s]+', vid):
        if PLATE_REGEX.fullmatch(token):
            return normalize_plate_key(token)
    m = PLATE_REGEX.search(vid)
    return normalize_plate_key(m.group(0) if m else vid)

def format_plate(plate):
    """
    Formata a placa para EXIBIÇÃO no padrão do Inlog: "ABC-1234".

    Usada nos popups/relatórios para que o mesmo caminhão apareça com uma
    identidade só, venha o dado do DuckDB ("DGG-8F39") ou do shapefile do
    RasCol ("16.03.2026_DGG8F39_JABOATAO").

    Valores irreconhecíveis são devolvidos sem alteração — melhor mostrar o
    nome cru do arquivo do que inventar uma placa.

    Examples:
        >>> format_plate('DGG8F39')
        'DGG-8F39'
        >>> format_plate('DGG-8F39')
        'DGG-8F39'
    """
    key = normalize_plate_key(plate)
    if len(key) == 7:
        return f"{key[:3]}-{key[3:]}"
    return str(plate)

def extract_route_or_sector_code(path):
    """
    Extrai código de rota/setor do nome de arquivo ou pasta.
    
    Formato esperado: PREFIXO + ESPAÇO + NÚMERO + (SUFIXO opcional)
    Sufixo pode conter dias da semana, conectores E/A e ordinais (1-5, L).
    
    Examples:
        >>> extract_route_or_sector_code('Rota - SCPV 05.zip')
        'SCPV 05'
        >>> extract_route_or_sector_code('Setor - SCA 03 SAB 1.zip')
        'SCA 03 SAB 1'
        >>> extract_route_or_sector_code('Setor - SCA 10 TER 1 2 E SAB 3 L.gpkg')
        'SCA 10 TER 1 2 E SAB 3 L'
    """
    name = os.path.splitext(os.path.basename(path))[0]
    name = name.replace('_', ' ').upper().strip()
    if ' - ' in name:
        name = name.split(' - ', 1)[1].strip()
    regex = re.compile("""
        (?P<prefix>
            SC
            (?:DOM | DIST | FEIRA | RURAL | EXTRA | D | P | I | A | S)
            [A-Z]*
        )
        \s+
        (?P<number>\d{1,})
        (?P<suffix>
            (?:\s+(?:DOM|SEG|TER|QUA|QUI|SEX|SAB|A|À|E|,|[1-5]|L|QZ|\d{2}\.\d{2}\.\d{4}))*
        )
        """, re.VERBOSE)
    m = regex.search(name)
    if not m:
        return ''
    prefix = m.group('prefix').strip()
    number = m.group('number').strip()
    suffix = m.group('suffix').strip()
    suffix = re.sub('\\s+', ' ', suffix)
    return (f"{prefix} {number}" + (f" {suffix}" if suffix else ''))

def split_code_parts(code):
    """
    Divide código em suas partes componentes.
    
    Examples:
        >>> split_code_parts('SCPV 05')
        ('SCPV', '05', '')
        >>> split_code_parts('SCA 03 SAB 1')
        ('SCA', '03', 'SAB 1')
    """
    if not isinstance(code, str):
        return ('', '', '')
    s = code.strip()
    m = re.match('^(SC[A-Z0-9]*)(?:\\s+(\\d{1,3}))?(?:\\s*(.*))?$', s, flags=re.IGNORECASE)
    if not m:
        return (s, '', '')
    prefix_raw = (m.group(1) or '').upper()
    num = (m.group(2) or '')
    rest = (m.group(3) or '').strip().upper()
    return (prefix_raw, num, rest)

def sector_code_sort_key(code):
    """
    Chave de ordenação dos filtros de setor: NÚMERO primeiro, prefixo depois.

    A ordem alfabética pura agrupa por prefixo ("SCA 10" antes de "SCD 02") e
    a operação procura setor pelo número — por isso o número manda e o prefixo
    só desempata. Códigos sem número vão para o fim, em ordem alfabética.

    Examples:
        >>> sorted(['SCD 10', 'SCA 02', 'SCD 02', 'SCA 10'], key=sector_code_sort_key)
        ['SCA 02', 'SCD 02', 'SCA 10', 'SCD 10']
    """
    text = str(code).strip().upper()
    m = re.match('^([^\\d\\s]*)\\s*(\\d+)(.*)$', text)
    if not m:
        return (1, 0, text, '')
    prefix, number, rest = m.groups()
    return (0, int(number), prefix, rest.strip())

def get_sc_base_prefix(code):
    """
    Extrai prefixo base do código SC.
    Remove sufixos de turno (D, N, V, M) e retorna o prefixo.
    
    Examples:
        >>> get_sc_base_prefix('SCPD 05')
        'SCP'
        >>> get_sc_base_prefix('SCDOM 12')
        'SCDOM'
    """
    if not code:
        return ''
    base = code.split()[0].upper()
    if base in ('SCDOM', 'SCDIST'):
        return base
    base = re.sub('(D|N|V|M)$', '', base)
    return base

_WEEKDAY_PATTERN = '(?:DOM|SEG|TER|QUA|QUI|SEX|SAB)'
_ORDINAL_PATTERN = '(?:[1-5]|L)'

def parse_weekday_tokens_from_text(text):
    """
    Extrai dias da semana de texto (retrocompatível, ignora ordinais).
    
    Examples:
        >>> parse_weekday_tokens_from_text('SEG E QUA')
        {0, 2}
        >>> parse_weekday_tokens_from_text('TER A QUI')
        {1, 2, 3}
        >>> parse_weekday_tokens_from_text('SAB 1 3')
        {5}
    """
    schedule = parse_schedule_rules(text)
    return schedule.weekday_set

def parse_schedule_rules(text):
    """
    Extrai agenda completa com regras ordinais/quinzenais a partir de texto.
    
    Gramática:
        SUFIXO  = REGRA ( " E " REGRA )*
        REGRA   = DIA_SEMANA ORDINAL*
                | DIA_SEMANA " A " DIA_SEMANA
                | DIA_SEMANA " QZ " DATA
        ORDINAL = 1 | 2 | 3 | 4 | 5 | L
        DATA    = DD.MM.YYYY
    
    Examples:
        >>> s = parse_schedule_rules('SAB 1')
        >>> s.rules[0].weekday, s.rules[0].ordinals
        (5, frozenset({1}))
        
        >>> s = parse_schedule_rules('QUI QZ 02.01.2025')
        >>> s.rules[0].anchor
        datetime.date(2025, 1, 2)
        
        >>> s = parse_schedule_rules('TER 1 2 E SAB 3 L')
        >>> len(s.rules)
        2
    """
    if (not isinstance(text, str) or not text.strip()):
        return Schedule()
    text = text.upper().strip()
    rules = []
    qz_re = re.compile(f"\\b({_WEEKDAY_PATTERN})\\s+QZ\\s+(\\d{{2}}\\.\\d{{2}}\\.\\d{{4}})\\b")
    qz_matches = list(qz_re.finditer(text))
    qz_spans = set()
    for m in qz_matches:
        qz_spans.update(range(m.start(), m.end()))
        day_name = m.group(1)
        date_str = m.group(2)
        wd = WEEKDAY_MAP_DAY_TO_CODE[day_name]
        try:
            anchor = datetime.strptime(date_str, '%d.%m.%Y').date()
            if anchor.weekday() != wd:
                import logging
                logging.getLogger(__name__).warning(f"Âncora QZ {date_str} ({WEEKDAY_MAP_CODE_TO_DAY[anchor.weekday()]}) não corresponde ao dia declarado ({day_name}). Usando mesmo assim.")
            rules.append(ScheduleRule(wd, frozenset(), anchor=anchor))
        except ValueError:
            pass
        continue
    range_re = re.compile(f"\\b({_WEEKDAY_PATTERN})\\s*[AÀ]\\s*({_WEEKDAY_PATTERN})\\b")
    range_matches = list(range_re.finditer(text))
    range_spans = set()
    for m in range_matches:
        if any((i in qz_spans for i in range(m.start(), m.end()))):
            continue
        range_spans.update(range(m.start(), m.end()))
        start_day, end_day = m.group(1), m.group(2)
        s = WEEKDAY_MAP_DAY_TO_CODE[start_day]
        e = WEEKDAY_MAP_DAY_TO_CODE[end_day]
        if s <= e:
            for wd in range(s, (e + 1)):
                rules.append(ScheduleRule(wd, frozenset()))
                continue
        else:
            for wd in range(s, 7):
                rules.append(ScheduleRule(wd, frozenset()))
            for wd in range(0, (e + 1)):
                rules.append(ScheduleRule(wd, frozenset()))
    consumed_spans = qz_spans | range_spans
    remaining = text
    for span_start in sorted(consumed_spans, reverse=True):
        pass
    remaining = list(text)
    for i in consumed_spans:
        if i < len(remaining):
            remaining[i] = ' '
    remaining = ''.join(remaining)
    day_group_re = re.compile(f"({_WEEKDAY_PATTERN})((?:\\s+{_ORDINAL_PATTERN})*)")
    for m in day_group_re.finditer(remaining):
        if (m.start() in consumed_spans or (m.end() - 1) in consumed_spans):
            continue
        day_name = m.group(1)
        ordinal_text = m.group(2).strip()
        wd = WEEKDAY_MAP_DAY_TO_CODE[day_name]
        ordinals = set()
        if ordinal_text:
            for token in ordinal_text.split():
                token = token.strip()
                if token == 'L':
                    ordinals.add('L')
                else:
                    if token.isdigit():
                        if 1 <= int(token):
                            if int(token) <= 5:
                                pass
                        else:
                            continue
                        ordinals.add(int(token))
                    continue
        rules.append(ScheduleRule(wd, frozenset(ordinals)))
    consolidated = {}
    qz_rules = [r for r in rules if r.anchor is not None]
    non_qz = [r for r in rules if r.anchor is None]
    for _item in non_qz:
        rule = _item
        if any((qr.weekday == rule.weekday for qr in qz_rules)):
            continue
        if rule.weekday in consolidated:
            existing = consolidated[rule.weekday]
            if not existing.ordinals and not rule.ordinals:
                continue
            if not existing.ordinals or not rule.ordinals:
                consolidated[rule.weekday] = ScheduleRule(rule.weekday, frozenset())
            else:
                merged = existing.ordinals | rule.ordinals
                consolidated[rule.weekday] = ScheduleRule(rule.weekday, merged)
                continue
                consolidated[rule.weekday] = rule
    return Schedule((qz_rules + list(consolidated.values())))

def parse_schedule_from_code(code_full):
    """
    Determina agenda (dias da semana) a partir do código.
    Retrocompatível: retorna Set[int]. Para ordinais, use parse_full_schedule_from_code().
    
    Examples:
        >>> parse_schedule_from_code('SCD 05')
        {0, 1, 2, 3, 4, 5}
        >>> parse_schedule_from_code('SCA 03 SAB 1')
        {5}
    """
    schedule = parse_full_schedule_from_code(code_full)
    return schedule.weekday_set

def parse_full_schedule_from_code(code_full):
    """
    Determina agenda completa (com ordinais) a partir do código.
    
    Lógica:
    1. Busca dias explícitos no sufixo do código
    2. Se não houver, usa mapeamento de prefixo padrão
    
    Examples:
        >>> s = parse_full_schedule_from_code('SCA 03 SAB 1')
        >>> s.rules[0].weekday, s.rules[0].ordinals
        (5, frozenset({1}))
        
        >>> s = parse_full_schedule_from_code('SCD 05')
        >>> s.weekday_set
        {0, 1, 2, 3, 4, 5}
    """
    if not isinstance(code_full, str):
        return Schedule()
    code_u = code_full.strip().upper()
    if not code_u:
        return Schedule()
    m_head = re.match('^SC[A-ZÀ-Ÿ]*\\s*\\d*\\s*', code_u)
    suffix_text = code_u[m_head.end():] if m_head else code_u
    schedule = parse_schedule_rules(suffix_text)
    if schedule.rules:
        return schedule
    base = get_sc_base_prefix(code_u)
    if base.startswith('SCDOM'):
        return Schedule([ScheduleRule(WEEKDAY_MAP_DAY_TO_CODE['DOM'], frozenset())])
    if base.startswith('SCD') and not base.startswith('SCDOM'):
        return Schedule([ScheduleRule(WEEKDAY_MAP_DAY_TO_CODE[d], frozenset()) for d in ('SEG', 'TER', 'QUA', 'QUI', 'SEX', 'SAB')])
    if base.startswith('SCP'):
        return Schedule([ScheduleRule(WEEKDAY_MAP_DAY_TO_CODE[d], frozenset()) for d in ('SEG', 'QUA', 'SEX')])
    if base.startswith('SCI'):
        return Schedule([ScheduleRule(WEEKDAY_MAP_DAY_TO_CODE[d], frozenset()) for d in ('TER', 'QUI', 'SAB')])
    if base in frozenset({'SCA', 'SCDIST', 'SCEXTRA', 'SCFEIRA', 'SCRURAL', 'SCS'}):
        return Schedule()
    return Schedule()

def compute_schedule_days_for_code(code_full, date_from, date_to):
    """
    Retorna lista de datas no intervalo que correspondem à agenda do código.
    Suporta regras ordinais (ex: 'SAB 1' = somente 1º sábado).
    
    Examples:
        >>> from pandas import Timestamp
        >>> code = 'SCA 01 SAB 1'  # 1º sábado do mês
        >>> start = Timestamp('2025-01-01')
        >>> end = Timestamp('2025-01-31')
        >>> days = compute_schedule_days_for_code(code, start, end)
        >>> len(days)
        1  # apenas 04/01/2025 (1º sábado)
    """
    schedule = parse_full_schedule_from_code(code_full)
    if not schedule.rules:
        return []
    days = []
    for d in pd.date_range(date_from, date_to, freq='D'):
        d_date = d.date() if hasattr(d, 'date') else d
        if schedule.matches_date(d_date):
            days.append(d.normalize())
    return days

def make_schedule_label(days_or_schedule):
    """
    Converte agenda em label legível.
    Aceita tanto Set[int] (retrocompatível) quanto Schedule.
    
    Examples:
        >>> make_schedule_label({0, 2, 4})
        'SEG, QUA e SEX'
        >>> make_schedule_label({6})
        'DOM'
    """
    if isinstance(days_or_schedule, Schedule):
        return _schedule_to_label(days_or_schedule)
    days = days_or_schedule
    if not days:
        return 'Desconhecido'
    if not isinstance(days, (set, list, tuple)):
        return 'Desconhecido'
    try:
        day_idxs = sorted((int(d) for d in set(days)))
    except Exception:
        return 'Desconhecido'
    names = [WEEKDAY_MAP_CODE_TO_DAY[d] for d in day_idxs if d in WEEKDAY_MAP_CODE_TO_DAY]
    if not names:
        return 'Desconhecido'
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} e {names[1]}"
    return ((', '.join(names[:-1]) + ' e ') + names[-1])

def _schedule_to_label(schedule):
    """
    Gera label detalhado de um Schedule (com ordinais e/ou quinzenais).
    
    Examples:
        'SAB'
        '1º SAB'
        '1º e 3º SAB'
        '1ª e 2ª TER e 3º e último SAB'
        'QUI quinzenal (ref 02/01/2025)'
    """
    if not schedule.rules:
        return 'Desconhecido'
    if not schedule.has_ordinals() and not schedule.has_biweekly():
        return make_schedule_label(schedule.weekday_set)
    parts = []
    for rule in sorted(schedule.rules, key=(lambda r: r.weekday)):
        day_name = WEEKDAY_MAP_CODE_TO_DAY.get(rule.weekday, '?')
        if rule.anchor is not None:
            anchor_str = rule.anchor.strftime('%d/%m/%Y')
            parts.append(f"{day_name} quinzenal (ref {anchor_str})")
        elif not rule.ordinals:
            parts.append(day_name)
        else:
            feminine = day_name in ('TER', 'QUA', 'QUI', 'SEX')
            ord_labels = []
            for o in sorted(rule.ordinals, key=(lambda x: (isinstance(x, str), x))):
                if o == 'L':
                    ord_labels.append('última' if feminine else 'último')
                else:
                    ord_labels.append(f"{o}ª" if feminine else f"{o}º")
            if len(ord_labels) == 1:
                ord_str = ord_labels[0]
            else:
                ord_str = f"{ord_labels[0]} e {ord_labels[1]}" if len(ord_labels) == 2 else ((', '.join(ord_labels[:-1]) + ' e ') + ord_labels[-1])
            parts.append(f"{ord_str} {day_name}")
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} e {parts[1]}"
    return ((', '.join(parts[:-1]) + ' e ') + parts[-1])

