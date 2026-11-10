# pycrefine: 1 place could not be reconstructed reliably; treat the source below as unverified there:
#   - stack_underflow: an operand is missing from the stack [in _parse_contract_line at CALL (offset 446); Python 3.11]
"""
Leitura de configurações externas do usuário (config.txt).

Procura o config.txt na seguinte ordem de prioridade:
  1. Pasta do executável (PyInstaller) — para todos os .exe na mesma pasta
  2. Diretório de trabalho atual        — config específico em modo dev
  3. Pasta deste arquivo e até 3 níveis acima — config compartilhado em pasta pai

Isso permite que project_heatmaps, inlog_api_project e inlog_automation
compartilhem um único config.txt quando deployados juntos como executáveis.
"""

import os
import re
import sys
from pathlib import Path
from typing import Optional, List, Dict, Set, Tuple
CONFIG_FILENAME = '.env'
CONFIG_FILENAME_LEGACY = 'config.txt'

def _find_config_file():
    """
    Procura o arquivo de configuração na seguinte ordem de prioridade:

      1. Pasta do executável (PyInstaller/frozen) — executáveis deployados
      2. Diretório de trabalho atual (os.getcwd()) — config por projeto (dev)
      3. Pasta deste arquivo e até 3 níveis acima  — config compartilhado

    Busca primeiro por ".env", depois por "config.txt" (legado).
    Retorna o primeiro encontrado, ou None se nenhum existir.
    """
    seen = set()
    def _try(p):
        try:
            r = p.resolve()
            if r in seen:
                return None
            seen.add(r)
            return r if r.is_file() else None
        except Exception:
            return None

    def _search(directory):
        for fname in (CONFIG_FILENAME, CONFIG_FILENAME_LEGACY):
            found = _try((directory / fname))
            if found:
                return found
        for fname in (CONFIG_FILENAME, CONFIG_FILENAME_LEGACY):
            found = _try(((directory / 'dependencias') / fname))
            if found:
                return found

    found = _search(Path(sys.executable).parent) if getattr(sys, 'frozen', False) else _search(Path(os.getcwd()))
    if found:
        return found
    script_dir = Path(__file__).resolve().parent
    for ancestor in [script_dir, *list(script_dir.parents)[:5]]:
        found = _search(ancestor)
        if found:
            return found

class ContractTimeConfig:
    """
    Horários personalizados de shapefile para um contrato.

    Attributes:
        hora_inicial: Horário de início (ex: "5:00"). None = usar padrão.
        hora_final:   Horário de fim   (ex: "6:30"). None = usar padrão.
    """
    def __init__(self, hora_inicial=None, hora_final=None):
        self.hora_inicial = hora_inicial
        self.hora_final = hora_final

    def __repr__(self):
        parts = []
        if self.hora_inicial is not None:
            parts.append(f"INICIAL={self.hora_inicial}")
        if self.hora_final is not None:
            parts.append(f"FINAL={self.hora_final}")
        return f"ContractTimeConfig({(', '.join(parts) or 'default')})"

class UserConfig:
    """Resultado da leitura do config.txt."""
    def __init__(self):
        self.username = None
        self.password = None
        self.contracts = None
        self.contract_times = {}
        self.ignored_plates = {}
        self.gps_db_path = None
        self.estacionario_types = {}
        self.loaded = False
        self.filepath = None

    @property
    def has_credentials(self):
        """Retorna True se ambas as credenciais foram preenchidas."""
        return bool((self.username and self.password))

    @property
    def has_contracts(self):
        """Retorna True se há pelo menos um contrato definido."""
        return bool(self.contracts)

    def get_ignored_plates(self, contract):
        """Chaves canônicas das placas de [PLACAS_IGNORADAS] do contrato (mesmo fallback de nome base)."""
        key = contract.upper().strip()
        if key in self.ignored_plates:
            return self.ignored_plates[key]
        base = re.split('\\s*[-\\[]', key, maxsplit=1)[0].strip()
        return self.ignored_plates.get(base, set())

    def get_time_config(self, contract):
        """
        Retorna a configuração de horários para um contrato.

        Tenta casar pelo nome completo (ex: "OLINDA - DIURNO") e, se não
        encontrar, faz fallback para o nome base (ex: "OLINDA"), permitindo
        configurar horários no nível do contrato sem precisar repetir para
        cada turno.
        """
        key = contract.upper().strip()
        if key in self.contract_times:
            return self.contract_times[key]
        base = re.split('\\s*[-\\[]', key, maxsplit=1)[0].strip()
        if base and base != key and base in self.contract_times:
            return self.contract_times[base]
        return ContractTimeConfig()

    def __repr__(self):
        return f"UserConfig(loaded={self.loaded}, has_credentials={self.has_credentials}, contracts={len(self.contracts) if self.contracts else 0}, custom_times={len(self.contract_times)})"

_TIME_PARAM_RE = re.compile('\\b(INICIAL|FINAL)\\s*=\\s*(\\d{1,2}(?::\\d{2})?)\\b', re.IGNORECASE)

def _normalize_time(raw):
    """
    Normaliza um horário para o formato HH:MM.

    Aceita '5', '05', '5:00', '05:00', '14', '23:30', etc.
    """
    raw = raw.strip()
    if ':' in raw:
        h, m = raw.split(':', 1)
        return f"{int(h)}:{m.zfill(2)}"
    return f"{int(raw)}:00"

def _parse_contract_line(line):
    """
    Parseia uma linha da seção [CONTRATOS].

    Formatos aceitos:
        CABO
        OLINDA, FINAL=6:30
        CUIABA, INICIAL=6
        PAULISTA, INICIAL=5:30, FINAL=14

    Returns:
        Tupla (nome_contrato, ContractTimeConfig)
    """
    params = {k.upper(): v for k, v in _TIME_PARAM_RE.findall(line)}
    clean = _TIME_PARAM_RE.sub('', line)
    clean = re.sub('[,]+', ' ', clean).strip()
    contract_name = re.sub('\\s+', ' ', clean).upper().strip()
    hora_inicial = _normalize_time(params['INICIAL']) if 'INICIAL' in params else None
    hora_final = _normalize_time(params['FINAL']) if 'FINAL' in params else None
    time_cfg = ContractTimeConfig(hora_inicial=hora_inicial, hora_final=hora_final)
    return (contract_name, time_cfg)

def _parse_ignored_plates(value):
    """Placas de uma linha de [PLACAS_IGNORADAS] (separadas por ';', ',' ou espaço) como chave canônica."""
    return {re.sub('[^A-Z0-9]', '', p.upper()) for p in re.split('[;,\\s]+', value) if p.strip()}

def _parse_config(text):
    """
    Faz o parsing do conteúdo do config.txt.

    Formato esperado:
        [CREDENCIAIS]
        usuario = meu_usuario
        senha = minha_senha

        [CONTRATOS]
        CABO
        CANELA
        CARUARU
    """
    config = UserConfig()
    current_section = None
    contracts_list = []
    contract_times = {}
    estacionario_types = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if (not line or line.startswith('#')):
            continue
        if line.startswith('[') and line.endswith(']'):
            current_section = line[1:-1].strip().upper()
        elif current_section == 'CREDENCIAIS':
            if '=' in line:
                key, _, value = line.partition('=')
                key = key.strip().lower()
                value = value.strip()
                if not value:
                    continue
                if key in ('usuario', 'usuário', 'user', 'username'):
                    config.username = value
                else:
                    if key in ('senha', 'password', 'pass'):
                        config.password = value
                    continue
                    if current_section == 'CONTRATOS':
                        contract_name, time_cfg = _parse_contract_line(line)
                        if contract_name:
                            contracts_list.append(contract_name)
                            if time_cfg.hora_inicial is not None or time_cfg.hora_final is not None:
                                contract_times[contract_name] = time_cfg
                    elif current_section == 'PLACAS_IGNORADAS':
                        if '=' in line:
                            key, _, value = line.partition('=')
                            key = key.strip().upper()
                            placas = _parse_ignored_plates(value.partition('#')[0])
                            if key and placas:
                                config.ignored_plates[key] = placas
                    elif current_section == 'GPS_DATABASE':
                        if '=' in line:
                            key, _, value = line.partition('=')
                            key = key.strip().lower()
                            value = value.strip()
                            if key in ('caminho', 'path', 'db_path') and value:
                                config.gps_db_path = value
                    elif current_section == 'ESTACIONARIOS' and '=' in line:
                        key, _, value = line.partition('=')
                        key = key.strip()
                        value = value.strip()
                        if key and value:
                            estacionario_types[key] = value.upper()
    if contracts_list:
        config.contracts = contracts_list
        config.contract_times = contract_times
    if estacionario_types:
        config.estacionario_types = estacionario_types
    return config

def load_user_config():
    """
    Procura e carrega o config.txt.

    Retorna um UserConfig sempre — se o arquivo não existir ou estiver
    vazio, os campos ficam None e o sistema usa os padrões hard coded.
    """
    config = UserConfig()
    config_path = _find_config_file()
    if config_path is None:
        return config
    try:
        text = config_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        try:
            text = config_path.read_text(encoding='latin-1')
        except Exception:
            pass
        return config
    config = _parse_config(text)
    config.loaded = True
    config.filepath = config_path
    return config

def _find_veiculos_contratos_xlsx():
    """Localiza dependencias/outputs/Veículos - Inlog/veiculos_contratos.xlsx."""
    cfg = load_user_config()
    candidates = []
    if cfg.filepath:
        candidates.append((((cfg.filepath.parent / 'outputs') / 'Veículos - Inlog') / 'veiculos_contratos.xlsx'))
        candidates.append(((((cfg.filepath.parent.parent / 'dependencias') / 'outputs') / 'Veículos - Inlog') / 'veiculos_contratos.xlsx'))
        candidates.append(((((cfg.filepath.parent / 'dependencias') / 'outputs') / 'Veículos - Inlog') / 'veiculos_contratos.xlsx'))
    candidates.append(Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE\dependencias\outputs\Veículos - Inlog\veiculos_contratos.xlsx'))
    start = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    for directory in ([start] + list(start.parents)[:6]):
        candidates.append(((((directory / 'dependencias') / 'outputs') / 'Veículos - Inlog') / 'veiculos_contratos.xlsx'))
    for c in candidates:
        try:
            if c.is_file():
                return c
        except Exception:
            continue

def _get_plates_by_type_from_xlsx(vehicle_type):
    """
    Lê veiculos_contratos.xlsx (gerado pelo InlogAutomation) e retorna as placas
    cujo campo Equipamento contém vehicle_type (case-insensitive).

    Usado como fallback quando os parquets do InlogAPI não estão disponíveis.
    """
    try:
        import pandas as pd
    except ImportError:
        return None
    xlsx = _find_veiculos_contratos_xlsx()
    if xlsx is None:
        return None
    try:
        df = pd.read_excel(xlsx, dtype=str)
    except Exception:
        return None
    if 'Equipamento' not in df.columns or 'Veiculo' not in df.columns:
        return None
    needle = vehicle_type.upper()
    mask = df['Equipamento'].apply((lambda v: (isinstance(v, str) and needle in v.upper())))
    plates = set(df.loc[(mask, 'Veiculo')].dropna().unique())
    return plates if plates else None

def get_plates_by_vehicle_type(vehicle_type):
    """
    Retorna o conjunto de placas cujo tipo de veículo (campo 'tipo' do parquet
    ou 'Equipamento' do xlsx) contém vehicle_type (case-insensitive).

    Fontes consultadas em ordem de prioridade:
      1. Parquet do InlogAPI — campo 'tipo' contém vehicle_type
      2. veiculos_contratos.xlsx do InlogAutomation (fallback)
         — campo 'Equipamento' contém vehicle_type

    Args:
        vehicle_type: Termo a buscar (ex: "COMPACTADOR", "POLIGUINDASTE",
                      "BASCULANTE"). Match é case-insensitive e por substring.

    Returns:
        Set de placas normalizadas (ex: "ABC-1234"), ou None se nenhuma fonte
        for encontrada — nesse caso o chamador usa todas as placas (sem filtro).
    """
    try:
        import pandas as pd
    except ImportError:
        return None
    needle = vehicle_type.upper()
    cfg = load_user_config()
    candidates = []
    if cfg.filepath:
        candidates.append((((cfg.filepath.parent / 'outputs') / 'raw') / 'vehicles'))
        candidates.append(((((cfg.filepath.parent.parent / 'dependencias') / 'outputs') / 'raw') / 'vehicles'))
        candidates.append(((((cfg.filepath.parent / 'dependencias') / 'outputs') / 'raw') / 'vehicles'))
    candidates.append(Path(r'C:\Users\NELMAC\CarlosDorth\sistemaLC\FS - DIE\dependencias\outputs\raw\vehicles'))
    start = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    for directory in ([start] + list(start.parents)[:6]):
        candidates.append(((((directory / 'dependencias') / 'outputs') / 'raw') / 'vehicles'))
        candidates.append((((directory / 'outputs') / 'raw') / 'vehicles'))
    vehicles_dir = None
    for c in candidates:
        try:
            if c.is_dir() and any(c.glob('*.parquet')):
                vehicles_dir = c
                break
        except Exception:
            continue
    if vehicles_dir is not None:
        parquets = sorted(vehicles_dir.glob('*.parquet'))
        if parquets:
            try:
                df = pd.read_parquet(parquets[-1])
                if 'placa_normalizada' in df.columns:
                    plate_col = 'placa_normalizada'
                elif 'placa' in df.columns:
                    plate_col = 'placa'
                else:
                    plate_col = None
                if plate_col and 'tipo' in df.columns:
                    mask = df['tipo'].apply((lambda v: (isinstance(v, str) and needle in v.upper())))
                    plates = set(df.loc[(mask, plate_col)].dropna().unique())
                    if plates:
                        return plates
            except Exception:
                pass
    return _get_plates_by_type_from_xlsx(vehicle_type)

def get_compactor_plates():
    """Atalho para get_plates_by_vehicle_type('COMPACTADOR')."""
    return get_plates_by_vehicle_type('COMPACTADOR')

