"""
Dia operacional: recorte dos tracks pela janela do contrato e o 'day' de cada ponto.

Separado de `main.py` para o Job 4 (`locar_mysql/heatmap_dia.py`) usar a MESMA regra
sem importar a pilha de desenho do mapa. `main._filter_tracks_by_contract_time` é este
recorte com a janela lida do config (INICIAL/FINAL do contrato).
"""

from datetime import date
import pandas as pd
_DEFAULT_INICIAL_MIN = 300
_DEFAULT_FINAL_MIN = 300

def _hhmm_to_minutes(s):
    if not s:
        return None
    try:
        h, m = s.split(':')
        return ((int(h) * 60) + int(m))
    except Exception:
        return None

def minutos_da_janela(cfg):
    """(ini_min, fin_min) de um ContractTimeConfig — sempre com defaults aplicados."""
    if cfg is None:
        return (_DEFAULT_INICIAL_MIN, _DEFAULT_FINAL_MIN)
    ini = _hhmm_to_minutes(getattr(cfg, 'hora_inicial', None))
    fin = _hhmm_to_minutes(getattr(cfg, 'hora_final', None))
    if ini is None:
        ini = _DEFAULT_INICIAL_MIN
    if fin is None:
        fin = _DEFAULT_FINAL_MIN
    return (ini, fin)

def recortar_dia_operacional(tracks, ini_min, fin_min, date_from, date_to):
    """
    Janela = [date_from + INICIAL, (date_to + 1) + FINAL). 'day' = (DATAHORA − INICIAL).date();
    com FINAL > INICIAL, a extensão do último dia — [(date_to+1)+INICIAL, (date_to+1)+FINAL) —
    fica com date_to. Devolve (recortado, n_antes).
    """
    start_dt = (pd.Timestamp(date_from) + pd.Timedelta(minutes=ini_min))
    end_dt = (pd.Timestamp(date_to) + pd.Timedelta(days=1, minutes=fin_min))
    dt = pd.to_datetime(tracks['DATAHORA'], errors='coerce', dayfirst=True)
    mask = (dt >= start_dt) & (dt < end_dt)
    mask = mask.fillna(False)
    n_before = len(tracks)
    out = tracks[mask].copy()
    out_dt = dt[mask]
    op_day = (out_dt - pd.Timedelta(minutes=ini_min)).dt.normalize()
    if fin_min > ini_min:
        next_day = (pd.Timestamp(date_to) + pd.Timedelta(days=1))
        ext_start = (next_day + pd.Timedelta(minutes=ini_min))
        ext_end = (next_day + pd.Timedelta(minutes=fin_min))
        in_ext = (out_dt >= ext_start) & (out_dt < ext_end)
        op_day = op_day.where((~in_ext), pd.Timestamp(date_to))
    out['day'] = op_day.values
    return (out, n_before)

