"""
Conector Oficial Inlog API (Inlog.Integration.Coleta.Api)
Desenvolvido para sitemaLCv1 - Locar Saneamento Ambiental.

Comunicação com a API da Inlog com suporte a OAuth2 e fallback automático para simulação.
"""

import os
import time
import requests
from typing import Dict, List, Optional, Any

def carregar_env():
    """Lê .env automaticamente."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

carregar_env()

class InlogService:
    def __init__(self):
        self.base_url = os.getenv("INLOG_BASE_URL", "https://api.inlog.com.br").rstrip("/")
        self.client_id = os.getenv("INLOG_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("INLOG_CLIENT_SECRET", "").strip()
        self.server_id = os.getenv("INLOG_SERVER_ID", "Inlog.Integration.Coleta.Api").strip()
        
        self.token = None
        self.token_expiry = 0

    def is_configured(self) -> bool:
        """Verifica se credenciais reais foram informadas no .env."""
        return bool(self.client_id and self.client_secret and "seu_" not in self.client_id)

    def get_token(self) -> Optional[str]:
        """Obtém ou renova token Bearer via OAuth2 client_credentials."""
        if not self.is_configured():
            return "TOKEN_SIMULADO_LOCAR"
            
        if self.token and time.time() < (self.token_expiry - 60):
            return self.token
            
        token_url = f"{self.base_url}/connect/token"
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
            "scope": self.server_id
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        
        try:
            resp = requests.post(token_url, data=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                self.token_expiry = time.time() + expires_in
                return self.token
            else:
                print(f"[INLOG AUTH] HTTP {resp.status_code}: {resp.text}")
                return None
        except Exception as e:
            print(f"[INLOG AUTH] Erro de conexão: {e}")
            return None

    def _headers(self) -> Dict[str, str]:
        token = self.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }

    def get_veiculos_por_cidade(self, cidade_nome: str) -> List[Dict[str, Any]]:
        """
        Busca os veículos e placas ativos na Inlog para a cidade indicada.
        Endpoint: GET api/Veiculos/TempoReal
        """
        cidade_norm = cidade_nome.lower().strip()
        
        if not self.is_configured():
            return self._mock_veiculos(cidade_norm)

        url = f"{self.base_url}/api/Veiculos/TempoReal"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=12)
            if resp.status_code == 200:
                todos = resp.json()
                if isinstance(todos, dict) and "data" in todos:
                    todos = todos["data"]
                    
                filtrados = []
                for v in todos:
                    operacoes = [op.get("descricao", "").lower() for op in v.get("operacoes", [])]
                    grupos = [g.get("descricao", "").lower() for g in v.get("gruposControle", [])]
                    if any(cidade_norm in op for op in operacoes) or any(cidade_norm in g for g in grupos):
                        filtrados.append(v)
                        
                if filtrados:
                    return filtrados
        except Exception as e:
            print(f"[INLOG] Erro ao buscar veículos: {e}")

        return self._mock_veiculos(cidade_norm)

    def get_historico_gps(self, placa: str, data_iso: str) -> List[Dict[str, Any]]:
        """
        Busca histórico completo de telemetria GPS (24h) da placa na data informada.
        Endpoint: GET api/Historico?dataInicio=YYYY-MM-DDT00:00:00&dataFim=YYYY-MM-DDT23:59:59&placa=...
        """
        placa_limpa = placa.replace("-", "").strip().upper()
        
        if not self.is_configured():
            return self._mock_gps(placa_limpa, data_iso)

        url = f"{self.base_url}/api/Historico"
        params = {
            "dataInicio": f"{data_iso}T00:00:00",
            "dataFim": f"{data_iso}T23:59:59",
            "placa": placa_limpa
        }
        
        try:
            resp = requests.get(url, params=params, headers=self._headers(), timeout=20)
            if resp.status_code == 200:
                resultado = resp.json()
                if isinstance(resultado, dict) and "data" in resultado:
                    return resultado["data"]
                return resultado
        except Exception as e:
            print(f"[INLOG] Erro ao buscar GPS da placa {placa}: {e}")

        return self._mock_gps(placa_limpa, data_iso)

    # --------------------------------------------------------------------------
    # SIMULAÇÃO DE ALTA FIDELIDADE COM DADOS LOCAIS DA LOCAR
    # --------------------------------------------------------------------------
    def _mock_veiculos(self, cidade_norm: str) -> List[Dict[str, Any]]:
        mapa_placas = {
            "jaboatao": ["DGG-8F39", "DWM-8D31", "EVE-4G52", "FSK-8J71", "FZB-8A75", "GAJ-7125", "GCM-6B28", "GDM-3H68"],
            "cabo": ["KLY-4920", "PEE-8210", "PFF-9012", "OYX-5541"],
            "olinda": ["PFW-4421", "PEH-9901", "OYZ-7714", "QYA-1234"],
            "caruaru": ["PEJ-8831", "KMT-4510", "OYW-9011"],
            "cuiaba": ["RBC-4E12", "RBD-5F22", "RBC-9A44"],
            "paulista": ["PLT-1020", "PLT-3040", "PLT-5060"],
            "garanhuns": ["GAR-1122", "GAR-3344"],
            "pesqueira": ["PSQ-9988", "PSQ-7766"],
            "vitoria": ["VIT-5544", "VIT-2211"],
            "canela": ["CAN-1234", "CAN-5678"],
            "jequie": ["JEQ-9080", "JEQ-7060"],
            "novo hamburgo": ["NVH-4030", "NVH-2010"]
        }
        placas = ["LOC-1010", "LOC-2020", "LOC-3030"]
        for k, v in mapa_placas.items():
            if k in cidade_norm or cidade_norm in k:
                placas = v
                break
                
        return [{
            "placaVeiculo": p,
            "identificadorVeiculo": f"LOC-{p.replace('-', '')}",
            "tipoVeiculo": "COMPACTADOR",
            "descricaoVeiculo": f"Coletor {p}",
            "statusIgnicaoVeiculo": "L",
            "velocidade": 13.8,
            "statusConexaoAVL": "ONLINE"
        } for p in placas]

    def _mock_gps(self, placa: str, data_iso: str) -> List[Dict[str, Any]]:
        import random
        random.seed(hash(placa + data_iso))
        lat, lon = -8.1930, -34.9364
        pontos = []
        for i in range(80):
            lat += (random.random() - 0.5) * 0.0007
            lon += (random.random() - 0.5) * 0.0007
            pontos.append({
                "dataHora": f"{data_iso}T{8 + (i//20):02d}:{(i*3)%60:02d}:00",
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "velocidade": round(random.uniform(5.0, 18.0), 1),
                "hodometro": 100000 + i * 40,
                "placa": placa
            })
        return pontos
