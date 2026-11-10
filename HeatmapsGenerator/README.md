# HeatmapsGenerator — Código-Fonte Completo e Ambiente de Desenvolvimento

Este projeto foi desmembrado e recuperado através de engenharia reversa do executável `HeatmapsGenerator.exe`, contendo toda a estrutura de módulos, interface gráfica Tkinter e regras de processamento geoespacial.

---

## 📁 Estrutura de Pastas e Arquivos

```text
HeatmapsGenerator/
│
├── heatmaps_gui.py                 # Ponto de entrada (Interface Gráfica Tkinter)
├── .env                            # Configurações reais (banco DuckDB, contratos, credenciais)
├── .env.example                    # Modelo de configuração
├── requirements.txt                # Dependências Python necessárias
├── HeatmapsGenerator.spec          # Especificação PyInstaller para compilar o .exe
├── build_exe.py                    # Script de compilação automática
├── build_exe.bat                   # Atalho de compilação por duplo clique
├── logo_DIE.ico / .png             # Ícones do projeto
├── logo_locar.png                  # Logo da empresa
│
├── dependencias/                   # Pasta de ativos auxiliares
│   ├── logo_DIE.ico
│   ├── logo_DIE.png
│   └── logo_locar.png
│
└── project_heatmaps/               # Pacote Python com toda a lógica da aplicação
    ├── __init__.py
    ├── ai_poison.py                # Módulo de proteção e integridade de dados
    ├── basemap_cache.py            # Cache de mapas base (OpenStreetMap / Carto)
    ├── check_files.py              # Validação de arquivos de entrada
    ├── config.py                   # Configurações de contratos, rotas e constantes
    ├── data_security.py            # Validação e segurança de dados operacionais
    ├── estacionarios.py            # Lógica de caixas estacionárias / poliguindastes
    ├── estacionarios_plotting.py   # Renderização cartográfica de estacionários
    ├── geo_processing.py           # Processamento espacial (CRS, buffers, DuckDB)
    ├── history.py                  # Processamento de dados históricos de coleta
    ├── html_templates.py           # Templates HTML/Leaflet para os heatmaps interativos
    ├── janela.py                   # Utilidades de janelas e componentes visuais
    ├── main.py                     # Orquestrador central de processamento de contratos
    ├── mapa_mysql.py               # Integração e consulta de bases MySQL
    ├── parsers.py                  # Leitura e parsing de rotas e coordenadas
    ├── plotting.py                 # Geração principal dos mapas interativos Leaflet
    ├── plotting_mobile_enhanced.py # Versão otimizada e simplificada para mobile
    ├── pontos_viciados.py          # Mapeamento e plotagem de pontos de descarte irregular
    ├── priorities.py               # Definição e pesos de setores prioritários
    ├── report_generator.py         # Geração de relatórios PDF e TXT
    ├── sector_labels.py            # Algoritmo de rotulagem e posicionamento de setores
    ├── secure_integration.py       # Integração com DuckDB e verificações seguras
    ├── stats_module.py             # Estatísticas de cobertura, percentuais e passagens
    ├── stops_plotting.py           # Mapa de calor de paradas críticas
    ├── stops_stats.py              # Estatísticas de tempo de parada e clusters
    ├── user_config.py              # Leitura dinâmica do arquivo .env / config.txt
    ├── watermark.py                # Inserção de marcas d'água nos mapas
    ├── web_assets.py               # Ativos web embutidos (JS, CSS)
    └── xml_report.py               # Exportação de relatórios analíticos em XML
```

---

## 🚀 Como Rodar em Modo de Desenvolvimento

1. **Instale as dependências:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Execute a interface:**
   ```bash
   python heatmaps_gui.py
   ```

---

## 🛠️ Como Gerar o Executável Atualizado (.exe)

Você pode compilar o novo executável de qualquer uma das seguintes maneiras:

- **Opção 1 (Atalho):** Dê um duplo clique no arquivo `build_exe.bat`.
- **Opção 2 (Python):** Execute no terminal:
  ```bash
  python build_exe.py
  ```
- **Opção 3 (Direto via PyInstaller):**
  ```bash
  pyinstaller HeatmapsGenerator.spec
  ```

O novo executável compilado será salvo automaticamente na pasta:
```text
HeatmapsGenerator/dist/HeatmapsGenerator.exe
```

---

## ⚙️ Configurações (.env)

O arquivo `.env` já está configurado na raiz com a mesma estrutura operacional do sistema:
- `[GPS_DATABASE]`: Caminho do banco DuckDB (`./dependencias/outputs/gps.duckdb`).
- `[CONTRATOS]`: Lista de contratos ativos (Cabo, Canela, Caruaru, Cuiabá, etc.).
- `[ESTACIONARIOS]`: Configurações de veículos para caixas Brooks.
- `[CREDENCIAIS]`, `[SERVIDORES]`, etc.
