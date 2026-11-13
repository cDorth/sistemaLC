# LOCAR SANEAMENTO AMBIENTAL — SISTEMA LC v1
**Monitoramento Integrado de Rotas, Auditoria de Coleta & Modo de Edição GIS (QGIS Web) via Inlog API**

---

### 1. Visão Geral
O **SISTEMA LC v1** é a plataforma operacional unificada da Locar para auditoria diária das rotas de coleta de resíduos sólidos e edição cartográfica de malhas viárias. 

Ele conecta-se diretamente à API da Inlog (`Inlog.Integration.Coleta.Api`), busca frotas e telemetria GPS, cruza com as malhas viárias e roda sobre **SQLite otimizado** — sem gerar arquivos desnecessários no disco e com ferramentas completas de edição GIS.

---

### 2. Recursos Recém-Adicionados

#### A. Rótulos dos Setores Ultra Sutis (Hover Only)
- Eliminada a aglomeração de caixas brancas e bordas grossas.
- Agora, cada setor é representado por um **ponto discreto e elegante**.
- O nome do setor (*ex: SCID 15, SCIN 18*) é revelado suavemente apenas ao **passar o mouse por cima (`hover`)** ou ao filtrar o setor no menu lateral.

#### B. Modo de Edição GIS Estilo QGIS (QGIS Web)
Clique no botão **`✏ Modo de Edição GIS (QGIS Web)`** na barra superior para habilitar a edição completa:
1. **🔍 Selecionar Trecho**: Clique em qualquer rua para carregar seus atributos no formulário lateral.
2. **➕ Desenhar Nova Rua**: Clique no mapa para traçar os pontos da nova rua e dê duplo clique para finalizar (o sistema calcula a extensão em metros automaticamente).
3. **Editar Atributos**:
   - Nome do Logradouro / Rua.
   - Código do Setor.
   - Tipo de Via (*Coleta Regular, Coleta Manual / Beco, Deslocamento*).
   - Rua Prioritária (*Avenidas / Comércio*).
4. **🗑 Apagar Rua**: Remove trechos desnecessários da malha viária com 1 clique.
5. **💾 Salvar no SQLite & Inlog**: Persiste as alterações no banco de dados e sincroniza os formatos oficiais exigidos pela Inlog.

---

### 3. Como Rodar no Dia a Dia

1. Dê um duplo clique no arquivo:
   ```cmd
   iniciar.bat
   ```
2. O navegador abrirá automaticamente em `http://127.0.0.1:8080`.
3. Caso a tela anterior esteja aberta, feche o terminal antigo e aperte **`Ctrl + F5`** no Chrome para recarregar a versão atualizada.
