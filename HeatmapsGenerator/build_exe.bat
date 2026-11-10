@echo off
chcp 65001 > nul
echo ============================================================
echo Compilando HeatmapsGenerator...
echo ============================================================
python -m PyInstaller --clean --noconfirm HeatmapsGenerator.spec
if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================================
    echo ✅ Sucesso! O novo executavel esta em dist\HeatmapsGenerator.exe
    echo ============================================================
) else (
    echo.
    echo ============================================================
    echo ❌ Falha ao compilar o executavel.
    echo ============================================================
)
pause
