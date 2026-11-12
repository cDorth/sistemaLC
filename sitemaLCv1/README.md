# LOCAR SANEAMENTO AMBIENTAL — SISTEMA LC v1
**Monitoramento Integrado de Rotas, Auditoria de Coleta e Heatmap Viário via Inlog API**

---

### 1. Visão Geral
O **SISTEMA LC v1** é a plataforma operacional unificada da Locar para auditoria diária das rotas de coleta de resíduos sólidos. 

Ele conecta-se diretamente à API da Inlog (`Inlog.Integration.Coleta.Api`), busca automaticamente as frotas e coordenadas GPS de cada caminhão, cruza com a malha viária oficial das cidades e renderiza o mapa de calor em tempo real sobre banco de dados **SQLite otimizado** — **sem gerar arquivos soltos nem acumular dados desnecessários em disco**.

---

### 2. Filtros e Recursos Operacionais na Barra Lateral

Seguindo exatamente o padrão oficial da Locar:

1. **Filtros de Coleta**:
   - **Por coleta**: *Todos*, *Coletadas (Verde)*, *Não coletadas (Vermelho / Omissão)*, *Possível falha de GPS (Amarelo)* e *Possível coleta manual (Laranja)*.
   - **Por setor**: Filtre a qualquer momento por setores específicos (ex: *SCDD 09*, *SCID 15*, *SCID 20*). O mapa centraliza automaticamente no setor e recalcula a quilometragem e o percentual de cobertura do setor selecionado.

2. **Carros (Filtro por Veículo)**:
   - **Veículo no mapa**: Selecione qualquer placa trazida da Inlog para auditar exclusivamente onde aquele caminhão coletou no período.

3. **Visualizações (Códigos dos Setores)**:
   - Botões **[Mostrar]** e **[Ocultar]** que desenham sobre o mapa os rótulos de identificação de cada setor em seus respectivos centróides geográficos.

4. **Inspeção de Logradouro**:
   - Clique em qualquer trecho de rua para abrir a gaveta de detalhes com: *Nome da Rua*, *Setor*, *Extensão em Metros*, *Situação Operacional* e *Placas dos Caminhões*.

5. **Download de Omissões**:
   - Botão **`Baixar Áreas Vermelhas (CSV)`** para exportação instantânea em memória para envio à prefeitura e despacho de repasse.

---

### 3. Como Configurar o `.env` (Único Passo)

Abra o arquivo [`.env`](file:///c:/Users/NELMAC/CarlosDorth/sistemaLC/sitemaLCv1/.env) e insira suas credenciais da Inlog:

```env
INLOG_BASE_URL=https://api.inlog.com.br
INLOG_CLIENT_ID=seu_client_id_aqui
INLOG_CLIENT_SECRET=seu_public_token_aqui
INLOG_SERVER_ID=Inlog.Integration.Coleta.Api

PORT=8080
```

> **Nota:** Se as credenciais forem deixadas em branco, o sistema executa automaticamente em **Modo Simulado** com os dados reais de Jaboatão, Cabo e demais contratos.

---

### 4. Como Iniciar no Dia a Dia

Dê um duplo clique no atalho:
```cmd
iniciar.bat
```
O navegador abrirá automaticamente em `http://127.0.0.1:8080`.
