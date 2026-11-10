"""
Funções de gerenciamento de histórico de coleta.

Este módulo gerencia o armazenamento e leitura de histórico
de estatísticas diárias em formato Parquet.
"""

from datetime import date
from typing import Optional
from pathlib import Path
import pandas as pd
from project_heatmaps.config import PATHS, VALIDATION, simplify_contract_name

def save_daily_collection_stat(contract_name, overall_ratio, collection_date, day_and_night_mode, single_day_mode):
    """
    Salva estatística diária de coleta em Parquet.
    
    Formato do arquivo: HISTORICO/<simple_name>_stats.parquet
    Colunas: date (YYYY-MM-DD), pct (0-100)
    
    Aplica validação mínima antes de salvar.
    Remove entradas inválidas existentes.
    
    Args:
        contract_name: Nome do contrato
        overall_ratio: Ratio de coleta (0-1) ou None
        collection_date: Data da coleta
        day_and_night_mode: Se está em modo dia+noite
        single_day_mode: Se está em modo dia único
        
    Returns:
        Path do arquivo salvo, ou None se inválido
    """
    if not single_day_mode:
        return None
    if overall_ratio is None:
        return None
    if isinstance(overall_ratio, (tuple, list)):
        ratio_strict = float(overall_ratio[0])
        ratio_gps = float(overall_ratio[1]) if len(overall_ratio) > 1 else ratio_strict
    else:
        ratio_strict = float(overall_ratio)
        ratio_gps = ratio_strict
    pct = ratio_strict * 100.0
    pct_gps = ratio_gps * 100.0
    min_pct = VALIDATION.get_min_valid_pct(day_and_night_mode)
    simple_name, _ = simplify_contract_name(contract_name)
    stats_path = PATHS.history_dir / f"{simple_name}_stats.parquet"
    if pct < min_pct:
        print(f"[{contract_name}] Histórico IGNORADO ({pct:.1f}% < {min_pct:.1f}%)")
        if stats_path.exists():
            df = pd.read_parquet(stats_path)
            df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
            df = df[df['date'] != collection_date.strftime('%Y-%m-%d')]
            df.to_parquet(stats_path, index=False)
        return None
    row = {'date': collection_date.strftime('%Y-%m-%d'), 'pct': pct, 'pct_gps': pct_gps}
    if stats_path.exists():
        df = pd.read_parquet(stats_path)
        df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
        df = df[df['date'] != row['date']]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])
    df.sort_values('date', inplace=True)
    df.to_parquet(stats_path, index=False)
    return stats_path

def load_collection_history(contract_name, day_and_night_mode):
    """
    Lê histórico de coleta do contrato.
    
    Remove entradas abaixo do limite mínimo atual.
    
    Args:
        contract_name: Nome do contrato
        day_and_night_mode: Se está em modo dia+noite
        
    Returns:
        DataFrame com colunas (date, pct) ou None
    """
    simple_name, _ = simplify_contract_name(contract_name)
    stats_path = PATHS.history_dir / f"{simple_name}_stats.parquet"
    if not stats_path.exists():
        return None
    try:
        df = pd.read_parquet(stats_path)
    except Exception as e:
        print(f"[WARN] Falha ao ler histórico para {contract_name}: {e}")
        return None
    if df.empty or 'date' not in df.columns or 'pct' not in df.columns:
        return None
    min_pct = VALIDATION.get_min_valid_pct(day_and_night_mode)
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df[df['pct'] >= min_pct].copy()
    if df.empty:
        return None
    df = df.sort_values('date')
    return df

def cleanup_invalid_history_entries(contract_name, day_and_night_mode):
    """
    Remove entradas de histórico abaixo do limite mínimo.
    
    Args:
        contract_name: Nome do contrato
        day_and_night_mode: Se está em modo dia+noite
        
    Returns:
        Número de entradas removidas
    """
    simple_name, _ = simplify_contract_name(contract_name)
    stats_path = PATHS.history_dir / f"{simple_name}_stats.parquet"
    if not stats_path.exists():
        return 0
    try:
        df = pd.read_parquet(stats_path)
    except Exception:
        return 0
    if df.empty:
        return 0
    min_pct = VALIDATION.get_min_valid_pct(day_and_night_mode)
    original_count = len(df)
    df = df[df['pct'] >= min_pct].copy()
    removed_count = (original_count - len(df))
    if removed_count > 0:
        df.to_parquet(stats_path, index=False)
        print(f"[{contract_name}] Removidas {removed_count} entradas inválidas do histórico")
    return removed_count

def get_monthly_average(contract_name, target_date, day_and_night_mode):
    """
    Calcula média mensal de coleta até uma data específica.
    
    Args:
        contract_name: Nome do contrato
        target_date: Data alvo (usa mês desta data)
        day_and_night_mode: Se está em modo dia+noite
        
    Returns:
        Média percentual (0-100) ou None
    """
    df = load_collection_history(contract_name, day_and_night_mode)
    if df is None or df.empty:
        return None
    month_start = pd.Timestamp(target_date.year, target_date.month, 1)
    month_end = (month_start + pd.offsets.MonthEnd(0)).normalize()
    df_month = df[(df['date'] >= month_start & df['date'] <= month_end)]
    if df_month.empty:
        return None
    return float(df_month['pct'].mean())

def get_period_average(contract_name, date_from, date_to, day_and_night_mode):
    """
    Calcula média de coleta para um período específico.
    
    Args:
        contract_name: Nome do contrato
        date_from: Data inicial
        date_to: Data final
        day_and_night_mode: Se está em modo dia+noite
        
    Returns:
        Média percentual (0-100) ou None
    """
    df = load_collection_history(contract_name, day_and_night_mode)
    if df is None or df.empty:
        return None
    df_period = df[(df['date'] >= pd.to_datetime(date_from) & df['date'] <= pd.to_datetime(date_to))]
    if df_period.empty:
        return None
    return float(df_period['pct'].mean())

