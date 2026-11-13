@echo off
title LOCAR - SISTEMA LC v1
chcp 65001 > nul
cls

echo ==============================================================================
echo                 LOCAR SANEAMENTO AMBIENTAL - SISTEMA LC v1
echo          Monitoramento Integrado de Rotas, Auditoria e Heatmap Viario
echo ==============================================================================
echo.

echo [1/3] Encerrando instancias anteriores na porta 8080 (se houver)...
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| find ":8080" ^| find "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
)

echo [2/3] Verificando configuracao (.env)...
if not exist "%~dp0.env" (
    echo [AVISO] Criando .env a partir de .env.example...
    copy "%~dp0.env.example" "%~dp0.env" > nul
)

echo [3/3] Iniciando Servidor Operacional Locar atualizado...
echo.
echo Abrindo navegador em: http://127.0.0.1:8080
start http://127.0.0.1:8080?t=%RANDOM%

python "%~dp0app.py"
pause
