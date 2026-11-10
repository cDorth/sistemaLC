"""
Verifica se todos os arquivos necessários existem antes da compilação.
"""

import os
import sys
from pathlib import Path
REQUIRED_MODULES = ['config.py', 'parsers.py', 'geo_processing.py', 'stats_module.py', 'history.py', 'plotting.py', 'html_templates.py', 'main.py', 'heatmaps_gui.py']
REQUIRED_RESOURCES = ['logo_DIE.ico']
OPTIONAL_RESOURCES = ['logo_DIE.png', 'logo_locar.png']
OPTIONAL_DOCS = ['requirements.txt', 'README.md']

def check_imports():
    """Verifica se pode importar módulos principais."""
    print('\n🔍 Testando imports...')
    errors = []
    tests = [('config', 'CONTRACTS'), ('parsers', 'extract_route_or_sector_code'), ('geo_processing', 'read_zipped_shapefile'), ('stats_module', 'compute_segment_missed_days'), ('history', 'save_daily_collection_stat'), ('plotting', 'export_segments_osm_html'), ('html_templates', 'get_styles'), ('main', 'process_all_contracts')]
    for module, attribute in tests:
        try:
            mod = __import__(module)
            if not hasattr(mod, attribute):
                errors.append(f"  ❌ {module}.py existe mas não tem '{attribute}'")
            else:
                print(f"  ✓ {module}.{attribute}")
        except ImportError as e:
            errors.append(f"  ❌ Não consegue importar {module}: {e}")
            continue
    return errors

def main():
    print('\n============================================================')
    print('🔍 VERIFICAÇÃO PRÉ-BUILD')
    print('=' * 60)
    all_good = True
    warnings = []
    print('\n📦 Módulos Python (OBRIGATÓRIOS):')
    for module in REQUIRED_MODULES:
        exists = Path(module).exists()
        status = '✓' if exists else '❌'
        print(f"  {status} {module}")
        if not exists:
            all_good = False
    print('\n🖼️  Recursos Obrigatórios:')
    for resource in REQUIRED_RESOURCES:
        exists = Path(resource).exists()
        status = '✓' if exists else '❌'
        print(f"  {status} {resource}")
        if not exists:
            all_good = False
            print('     CRÍTICO: Necessário para o ícone do executável!')
    print('\n🖼️  Recursos Opcionais (Logos nos mapas):')
    for resource in OPTIONAL_RESOURCES:
        exists = Path(resource).exists()
        status = '✓' if exists else '⚠️ '
        print(f"  {status} {resource}")
        if not exists:
            warnings.append(f"Logo {resource} não encontrado - mapas não terão logo")
    print('\n📄 Documentação:')
    for doc in OPTIONAL_DOCS:
        exists = Path(doc).exists()
        status = '✓' if exists else '○'
        print(f"  {status} {doc}")
    import_errors = check_imports()
    if import_errors:
        all_good = False
        print('\n❌ ERROS DE IMPORT:')
        for error in import_errors:
            print(error)
            continue
    print('\n🔧 Ferramentas:')
    try:
        import PyInstaller
        print(f"  ✓ PyInstaller {PyInstaller.__version__} instalado")
    except ImportError:
        print('  ❌ PyInstaller não instalado!')
        print('     Execute: pip install pyinstaller')
        all_good = False
    print('\n============================================================')
    if all_good:
        print('✅ TUDO OK! Pronto para compilar.')
        print('\nExecute:')
        print('  Windows: build.bat')
        print('  Linux/Mac: ./build.sh')
        print('  Ou: pyinstaller heatmaps.spec')
    else:
        print('❌ CORRIJA OS ERROS ANTES DE COMPILAR!')
        sys.exit(1)
    if warnings:
        print('\n⚠️  AVISOS:')
        for warning in warnings:
            print(f"  • {warning}")
            continue
    print('============================================================\n')
    return 0 if all_good else 1

if __name__ == "__main__":
    sys.exit(main())

