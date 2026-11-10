# LOCAR OPERATIONS SUITE — MASTER SPECIFICATION & EXECUTABLE BLUEPRINT
## Documento Técnico Autossuficiente para Desenvolvimento Integral do Sistema Unificado

---
Aja como Engenheiro de Software Sênior e Especialista em Geoprocessamento Python. Com base no documento técnico em anexo (MASTER_BLUEPRINT_LOCAR_UNIFIED_APP.md), desenvolva 100% do código-fonte do aplicativo Locar Operations Suite, gerando todos os arquivos da árvore de pastas especificada, sem omitir regras de negócio, cálculos espaciais de buffers, rotinas de banco DuckDB ou interface gráfica PyQt6. O projeto deve estar completo para que eu apenas preencha o .env e rode o build_app.py para gerar o .exe pronto para a operação.

> **INSTRUÇÃO PARA O MODELO / DESENVOLVEDOR (CLAUDE SONNET / OPUS):**
> Este documento é uma especificação técnica fechada, exaustiva e autossuficiente para o desenvolvimento do software **Locar Operations Suite (Locar GIS)**. Ele consolida 6 ferramentas legadas (`HeatmapsGenerator`, `InlogAPI`, `InlogAutomation`, `RasColAutomation`, `GirosAutomation`, `Gerador de Template`) em um único aplicativo desktop moderno, modular e de alta performance.
> 
> **Requisito do Desenvolvedor:** Implementar a arquitetura completa descrita abaixo, gerando todos os arquivos de código-fonte especificados, sem omitir regras de negócio, cálculos espaciais ou integrações, permitindo que o usuário apenas insira suas credenciais no `.env` e execute a compilação do aplicativo final para `.exe`.

---

## 1. VISÃO GERAL DO PRODUTO

O **Locar Operations Suite** é uma plataforma desktop em Python (PyQt6) projetada para:
1. **Telemetria e Ingestão de Dados:** Conectar-se à API REST da Inlog e aos portais legados (Inlog Web, RasCol, Giros) para coletar dados GPS, eventos de frota e jornadas.
2. **Banco de Dados Geoespacial Otimizado:** Armazenar dados em banco colunar local DuckDB particionado por mês com compressão ZSTD, eliminando mais de 40 GB de shapefiles redundantes.
3. **Motor Central de Mapas de Calor (Heatmaps):** Projetar coordenadas (`EPSG:31985`), aplicar janelas operacionais, buffers espaciais (8m/15m), calcular aderência da coleta domiciliar contra rotas planejadas, paradas críticas e gerar visualizações interativas em HTML (Leaflet/Plotly) com basemaps offline.
4. **Relatórios e Auditoria:** Gerar relatórios de conformidade em XML, séries temporais em Parquet e cadernos de licitação em PDF com dados das 15 praças.

---

## 2. ESTRUTURA COMPLETA DO PROJETO

```
LocarOperationsSuite/
├── .env.exemplo                      # Arquivo de configuração modelo
├── requirements.txt                  # Dependências exatas
├── build_app.py                      # Script de compilação automatizada
├── LocarApp.spec                     # Configuração do PyInstaller
├── README.md                         # Instruções de setup
│
├── app/
│   ├── __init__.py
│   ├── main.py                       # Ponto de entrada (GUI Bootstrapper)
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py               # Leitor central do .env com tipagem Dataclass
│   │   └── constants.py              # Projeções CRS, cores, buffers e contratos
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── database.py               # Conexão DuckDB, migrações e consultas
│   │   ├── spatial_engine.py         # Motor de recorte, buffers e aderência
│   │   ├── janela_operacional.py     # Lógica de dia operacional e turnos
│   │   ├── parsers.py                # Regex de setores (SCD, SCP, SCI, etc.) e placas
│   │   └── stops_analyzer.py         # Detecção de dwell time e paradas em esquinas
│   │
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── inlog_api.py              # Cliente REST OAuth2 da API Inlog
│   │   ├── inlog_scraper.py          # Automação Web headless Inlog
│   │   ├── rascol_scraper.py         # Automação Web headless RasCol (Jaboatão)
│   │   └── giros_scraper.py          # Automação Web headless Giros (Ponto)
│   │
│   ├── exporters/
│   │   ├── __init__.py
│   │   ├── html_maps.py              # Gerador de mapas HTML (Desktop e Mobile)
│   │   ├── xml_reporter.py           # Gerador de XML de auditoria
│   │   ├── parquet_history.py        # Gestor de histórico de aderência
│   │   └── pdf_licitacao.py          # Gerador de templates municipais
│   │
│   └── ui/
│       ├── __init__.py
│       ├── main_window.py            # Janela principal com sidebar e stacking
│       ├── theme.py                  # Estilo Dark Mode profissional moderno
│       ├── tab_heatmaps.py           # Painel de geração de mapas de calor
│       ├── tab_sync.py               # Painel de sincronização de dados (API/RPA)
│       ├── tab_viewer.py             # Visualizador interno (QWebEngineView)
│       ├── tab_reports.py            # Painel de relatórios e exportações
│       └── tab_settings.py           # Gestão de credenciais e contratos
│
└── assets/
    ├── icon.ico                      # Ícone do aplicativo
    ├── logo_locar.png                # Logo da empresa
    └── logo_DIE.png                  # Logo da diretoria
```

---

## 3. BANCO DE DADOS E ESTRUTURA DE DADOS (DUCKDB)

O banco colunar de telemetria deve ser implementado no arquivo `dados/banco_telemetria.duckdb`.

### 3.1 Esquema DDL

```sql
-- Histórico bruto de coordenadas GPS
CREATE TABLE IF NOT EXISTS gps_history (
    placa_normalizada VARCHAR NOT NULL,
    dataHora TIMESTAMP NOT NULL,
    latitude DOUBLE NOT NULL,
    longitude DOUBLE NOT NULL,
    velocidade_kmh DOUBLE DEFAULT 0.0,
    gps_date DATE NOT NULL,
    origem VARCHAR DEFAULT 'inlog_api' -- 'inlog_api' ou 'rascol_shape'
);
CREATE INDEX IF NOT EXISTS idx_gps_lookup ON gps_history (placa_normalizada, dataHora);
CREATE INDEX IF NOT EXISTS idx_gps_date ON gps_history (gps_date);

-- Linha do tempo de garagens (vinculação veículo <-> contrato <-> turno)
CREATE TABLE IF NOT EXISTS vehicle_contract_timeline (
    placa VARCHAR NOT NULL,
    contrato VARCHAR NOT NULL,     -- Ex: 'CABO', 'CUIABA', 'OLINDA'
    tipo VARCHAR NOT NULL,         -- Ex: 'COLETA', 'GARAGEM', 'MANUTENCAO'
    dt_inicio TIMESTAMP NOT NULL,
    dt_fim TIMESTAMP NOT NULL,
    n_pontos_coleta INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_timeline_lookup ON vehicle_contract_timeline (contrato, dt_inicio, dt_fim);

-- Cadastro consolidado de frota
CREATE TABLE IF NOT EXISTS vehicles (
    placa VARCHAR PRIMARY KEY,
    placa_normalizada VARCHAR NOT NULL,
    tipo VARCHAR NOT NULL,         -- 'COMPACTADOR', 'POLIGUINDASTE', 'BASCULANTE', etc.
    filial VARCHAR,
    ativo BOOLEAN DEFAULT TRUE,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Histórico de métricas diárias de aderência
CREATE TABLE IF NOT EXISTS heatmap_stats (
    contrato VARCHAR NOT NULL,
    data_operacional DATE NOT NULL,
    turno VARCHAR NOT NULL,        -- 'DIURNO', 'NOTURNO', 'AMBOS'
    percentual_coleta DOUBLE NOT NULL,
    percentual_com_manual DOUBLE,
    total_km_planejado DOUBLE,
    total_km_executado DOUBLE,
    pontos_gps_processados INTEGER,
    processado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (contrato, data_operacional, turno)
);
```

---

## 4. ESPECIFICAÇÃO DE INTEGRAÇÕES EXTERNAS

### 4.1 Inlog REST API (`app/integrations/inlog_api.py`)

#### Autenticação OAuth2:
* **Endpoint de Token:** `POST https://locar.inlog.com.br/autenticacao-services/connect/token`
* **Headers:** `Content-Type: application/x-www-form-urlencoded`
* **Body:**
  ```
  grant_type=client_credentials&client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}&scope={SERVER_ID}
  ```
* **Armazenamento:** Cachear o `access_token` e renovar 5 minutos antes da expiração (`expires_in`).

#### Endpoints de Coleta:
1. **Lista de Veículos:**
   - `GET https://locar.inlog.com.br/IntegrationColeta/api/v1/Veiculos`
   - Extrai: Placa, ID do veículo, Descrição do equipamento (`COMPACTADOR`, etc.).
2. **Timeline de Contratos / Garagens:**
   - `GET https://locar.inlog.com.br/IntegrationColeta/api/v1/Contratos/Timeline?dataInicio={ISO}&dataFim={ISO}`
   - Mapeia quais placas pertenciam a quais contratos em cada período.
3. **Telemetria GPS Bruta:**
   - `GET https://locar.inlog.com.br/IntegrationColeta/api/v1/Rastreamento/Historico`
   - Parâmetros: `placa`, `dataInicio`, `dataFim`.
   - Inserção em lote no DuckDB utilizando `appender` para máxima velocidade (1 milhão de pontos em < 2 segundos).

### 4.2 Web Scrapers Headless (`app/integrations/*_scraper.py`)
Utilizar Playwright (ou Selenium WebDriver sem interface gráfica) com credenciais do `.env`:
* **`InlogWebScraper` (`inlog_scraper.py`):**
  - Acessa `https://locar.inlog.com.br/Rastreamento/Apresentacao/Autenticador`.
  - Exporta relatórios de contingência e shapefiles zipados quando solicitado.
* **`RasColScraper` (`rascol_scraper.py`):**
  - Acessa `https://id.rassystem.com.br/cas/login`.
  - Autentica na filial `JABOATAO`.
  - Baixa os shapefiles diários de Jaboatão, extrai as coordenadas e as ingere diretamente na tabela `gps_history` do DuckDB com tag `origem='rascol_shape'`.
* **`GirosScraper` (`giros_scraper.py`):**
  - Acessa `https://locar.giros.com.br`.
  - Exporta registros de ponto de motoristas para validação de turnos.

---

## 5. MOTOR GEOESPACIAL DE MAPAS DE CALOR (ALGORITMOS CRÍTICOS)

### 5.1 Janela Operacional Diária (`app/core/janela_operacional.py`)
* O dia de coleta inicia às 05:00 do dia `D` e termina às 05:00 do dia `D+1`.
* **Exceções configuradas no `.env`:**
  - Cuiabá: Inicia às 04:00 (dia operacional = `04:00 D` até `04:00 D+1`).
  - Olinda / Paulista: Estendem-se até às 07:00 (dia operacional = `05:00 D` até `07:00 D+1`).
* **Fórmula de Normalização do Dia Operacional:**
  $$\text{Dia Operacional} = (\text{DATAHORA} - \text{INICIAL\_MINUTOS}).\text{date()}$$
  Pontos entre 00:00 e 05:00 da madrugada do dia `D+1` pertencem ao dia de coleta `D`.

### 5.2 Projeção Cartográfica e Resolução Espacial
* Coordenadas de GPS entram como `EPSG:4326` (WGS84).
* Todas as geometrias de vias e polígonos são reprojetadas para **`EPSG:31985` (UTM Sirgas 2000 Zona 25S)** para cálculo de distâncias reais em metros.
* Para renderização final nos mapas HTML interativos, reconverte-se para `EPSG:4326`.

### 5.3 Decodificação de Agendas dos Setores (`app/core/parsers.py`)
Os arquivos de setores possuem códigos que determinam os dias de coleta:
* `SCD`: Coleta Diária (Segunda a Sábado: `SEG, TER, QUA, QUI, SEX, SAB`).
* `SCP`: Coleta Par (Segunda, Quarta, Sexta: `SEG, QUA, SEX`).
* `SCI`: Coleta Ímpar (Terça, Quinta, Sábado: `TER, QUI, SAB`).
* `SCDOM`: Coleta Dominical (`DOM`).
* `SCA`, `SCS`, `SCFEIRA`, `SCRURAL`, `SCDIST`: Coletas alternadas ou especiais.
* **Validação:** Um setor só entra no mapa se o dia selecionado for compatível com sua agenda.

### 5.4 Classificação de Cobertura de Trechos Viários (`app/core/spatial_engine.py`)
Para cada segmento de rota:
1. Cria-se um buffer de **8 metros** ao redor da linha central da via.
2. Cria-se uma zona de tolerância de **15 metros** nas extremidades e esquinas.
3. Se houver pontos GPS suficientes dentro do buffer trafegando em velocidade operacional (3 km/h a 40 km/h) no sentido correto:
   - **Status Verde (`collected`):** Trecho cumprido ($\ge 35\%$ de cobertura do segmento).
   - **Status Amarelo (`possible_gps_gap`):** Interrupção transitória de sinal ou passagem rápida.
   - **Status Vermelho (`not_collected`):** Inadimplência do trecho de coleta.
   - **Status Ciano/Azul (`manual`):** Coleta seletiva ou complementar.

### 5.5 Filtro de Caminhões de Outro Contrato
Um caminhão alocado em Olinda pode passar por Paulista ao voltar para a garagem. O motor deve cruzar o trajeto com a tabela `vehicle_contract_timeline` e **descartar os pontos de placas que não pertenciam ao contrato ativo** naquele dia/horário.

---

## 6. ESPECIFICAÇÃO DA INTERFACE GRÁFICA (PyQt6)

A interface deve adotar padrão visual **Dark Theme** moderno (tons `#121212`, `#1E1E1E`, `#2D2D2D` com destaque verde Locar `#00C853`).

### 6.1 Telas e Abas

1. **Aba 1: Gerador de Mapas de Calor (`tab_heatmaps.py`)**
   - Seletor de Contratos (checkboxes individuais e "Todos").
   - Seleção de Período (Data Inicial e Data Final com date pickers).
   - Modos:
     - [x] Diurno + Noturno agregados.
     - [ ] Gerar apenas histórico estatístico.
     - [x] Versão Mobile simplificada (vetores comprimidos).
     - [ ] Mapa de pontos de paradas críticas.
     - [ ] Separar por tipo de coleta (Domiciliar vs Seletiva).
   - Botão de Ação: **"🚀 Gerar Mapas de Execução"**.
   - Barra de progresso com log em tempo real das etapas.

2. **Aba 2: Sincronização & Telemetria (`tab_sync.py`)**
   - Status da conexão Inlog API (Online/Offline).
   - Status da última extração de dados.
   - Botões:
     - `Atualizar Frota e Garagens (Inlog API)`.
     - `Baixar Histórico GPS Faltante`.
     - `Executar Scraping RasCol (Jaboatão)`.
   - Tabela com resumo de pontos disponíveis no banco por contrato e data.

3. **Aba 3: Visualizador Integrado (`tab_viewer.py`)**
   - Componente `QWebEngineView` embutido.
   - Lista lateral dos mapas HTML gerados na pasta de saída.
   - Ao clicar no arquivo, o mapa interativo abre diretamente dentro do app, com zoom, camadas e estatísticas, sem abrir navegadores externos.

4. **Aba 4: Relatórios & Auditoria (`tab_reports.py`)**
   - Exportação em lote de relatórios XML para prefeituras.
   - Consulta e exportação de tabelas de aderência histórica para Excel/CSV.
   - Gerador de cadernos e templates de licitação em PDF com os logos municipais correspondentes.

5. **Aba 5: Configurações (`tab_settings.py`)**
   - Edição facilitada das credenciais do `.env`.
   - Ajuste das janelas operacionais por praça.
   - Botão para **"Otimizar Banco de Dados (Vacuum & Compress)"**, que limpa fragmentos do DuckDB e garante tamanho reduzido.

---

## 7. ARQUIVO `.ENV.EXEMPLO` COMPLETO (PRONTO PARA USO)

O arquivo `.env.exemplo` deve ser copiado para `.env` na raiz da aplicação:

```ini
# ==============================================================================
# LOCAR OPERATIONS SUITE — CONFIGURAÇÃO CENTRAL (.env)
# ==============================================================================

[CREDENCIAIS_API]
INLOG_API_URL        = https://locar.inlog.com.br/IntegrationColeta
INLOG_TOKEN_URL      = https://locar.inlog.com.br/autenticacao-services/connect/token
INLOG_CLIENT_ID      = Locar.Client.Api.Coleta
INLOG_CLIENT_SECRET  = TG9jYXIuQ2xpZW50LkFwaS5Db2xldGE=
INLOG_SERVER_ID      = Inlog.Integration.Coleta.Api

[CREDENCIAIS_WEB]
INLOG_WEB_USER       = die
INLOG_WEB_PASS       = inlog2025
RASCOL_WEB_USER      = jorge.edson
RASCOL_WEB_PASS      = RasCol2025
RASCOL_FILIAL        = JABOATAO
GIROS_WEB_USER       = jorgeedson
GIROS_WEB_PASS       = Jorge096*

[DIRETORIOS]
BANCO_DADOS          = ./dados/banco_telemetria.duckdb
PASTA_CONTRATOS      = ./dados/CONTRATOS
PASTA_SHAPES_BACKUP  = ./dados/shapes
CACHE_TILES_OFFLINE  = ./dados/cache_mapas
SAIDA_HEATMAPS       = ./saidas/HEATMAPS
SAIDA_RELATORIOS_XML = ./saidas/RELATORIOS_XML
SAIDA_HISTORICO      = ./saidas/HISTORICO

[PARAMETROS_OPERACIONAIS]
DEFAULT_HORA_INICIAL = 05:00
DEFAULT_HORA_FINAL   = 05:00
CUIABA_HORA_INICIAL  = 04:00
OLINDA_HORA_FINAL    = 07:00
PAULISTA_HORA_FINAL  = 07:00

[CONTRATOS]
LISTA = CABO, CANELA, CARUARU, CUIABA, GARANHUNS, JABOATAO, JEQUIE, NOVO HAMBURGO, OLINDA, PAULISTA, PESQUEIRA, VITORIA

[PLACAS_IGNORADAS]
PAULISTA = JBH-3D32

[ESTACIONARIOS]
Caixa Brooks = POLIGUINDASTE
```

---

## 8. ARQUIVO `REQUIREMENTS.TXT`

```txt
PyQt6>=6.6.1
PyQt6-WebEngine>=6.6.0
duckdb>=0.10.0
geopandas>=0.14.3
shapely>=2.0.3
pyogrio>=0.7.2
pyproj>=3.6.1
pandas>=2.2.1
numpy>=1.26.4
folium>=0.16.0
plotly>=5.19.0
reportlab>=4.1.0
pypdf>=4.1.0
Pillow>=10.2.0
requests>=2.31.0
playwright>=1.42.0
openpyxl>=3.1.2
pyinstaller>=6.5.0
```

---

## 9. SCRIPT DE COMPILAÇÃO AUTOMATIZADA (`build_app.py`)

```python
import os
import sys
import shutil
import subprocess

def build():
    print("=" * 70)
    print("COMPILANDO: LOCAR OPERATIONS SUITE (APP UNIFICADO)")
    print("=" * 70)
    
    # 1. Limpeza de artefatos anteriores
    for p in ["build", "dist"]:
        if os.path.exists(p):
            shutil.rmtree(p)
            
    # 2. Comando PyInstaller
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=LocarOperationsSuite",
        "--windowed",
        "--onedir",
        "--icon=assets/icon.ico",
        "--clean",
        "--noconfirm",
        # Coleta de pacotes geoespaciais e WebEngine
        "--collect-data=pyogrio",
        "--collect-data=geopandas",
        "--collect-data=reportlab",
        "--collect-data=folium",
        "--collect-all=PyQt6_WebEngine",
        # Inclusão de assets
        "--add-data=assets;assets",
        "app/main.py"
    ]
    
    print(f"Executando build: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        # Cria diretórios essenciais no dist
        dist_dir = os.path.join("dist", "LocarOperationsSuite")
        for sub in ["dados/CONTRATOS", "dados/shapes", "dados/cache_mapas", "saidas/HEATMAPS", "saidas/RELATORIOS_XML", "saidas/HISTORICO"]:
            os.makedirs(os.path.join(dist_dir, sub), exist_ok=True)
            
        # Copia o .env.exemplo para o dist
        shutil.copy(".env.exemplo", os.path.join(dist_dir, ".env.exemplo"))
        
        print("\n" + "=" * 70)
        print("✅ BUILD CONCLUÍDO COM SUCESSO!")
        print(f"Executável pronto em: {os.path.abspath(os.path.join(dist_dir, 'LocarOperationsSuite.exe'))}")
        print("=" * 70)
    else:
        print(f"\n❌ Erro durante o build (código {result.returncode})")

if __name__ == "__main__":
    build()
```

---

## 10. GUIA DE EXECUÇÃO PARA O CLAUDE SONNET / OPUS

Ao receber este documento, o Claude Sonnet/Opus deve proceder na seguinte ordem:

1. **Passo 1 — Módulos Base:**
   - Implementar `app/config/settings.py` e `app/config/constants.py` tipando todas as configurações do `.env`.
   - Implementar `app/core/parsers.py` com o decodificador de dias de coleta (`SCD`, `SCP`, etc.) e normalização de placas.
   - Implementar `app/core/janela_operacional.py` com a lógica estrita de dia operacional.

2. **Passo 2 — Banco de Dados e Integrações:**
   - Implementar `app/core/database.py` inicializando as tabelas do DuckDB com conexões thread-safe e métodos de ingestão em massa.
   - Implementar `app/integrations/inlog_api.py` com cliente HTTP OAuth2 e paginação de telemetria.
   - Implementar `app/integrations/rascol_scraper.py` e `giros_scraper.py` usando Playwright headless.

3. **Passo 3 — Motor Geoespacial:**
   - Implementar `app/core/spatial_engine.py` realizando: leitura de rotas/setores (`pyogrio`/`geopandas`), reprojeção para `EPSG:31985`, buffering métrico (8m e 15m), cruzamento com GPS do DuckDB e classificação de trechos viários.
   - Implementar `app/exporters/html_maps.py` para gerar os mapas interativos Leaflet/Plotly em HTML.

4. **Passo 4 — Interface Gráfica PyQt6:**
   - Implementar `app/ui/theme.py` aplicando estilo Dark profissional.
   - Implementar as abas de interface (`tab_heatmaps.py`, `tab_sync.py`, `tab_viewer.py`, `tab_reports.py`, `tab_settings.py`).
   - Implementar `app/ui/main_window.py` conectando as abas e executando processamentos pesados em threads separadas (`QThread`) para não travar a UI.
   - Implementar `app/main.py`.

5. **Passo 5 — Conclusão:**
   - Gerar `requirements.txt`, `build_app.py` e `.env.exemplo`.
   - O aplicativo estará 100% pronto para compilação.
