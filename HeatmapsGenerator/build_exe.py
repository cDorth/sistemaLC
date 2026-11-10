import subprocess
import sys
import os

def build():
    print("=" * 60)
    print("Iniciando compilação do HeatmapsGenerator...")
    print("=" * 60)
    
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        "HeatmapsGenerator.spec"
    ]
    
    print(f"Executando: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\n" + "=" * 60)
        print("✅ Compilação concluída com sucesso!")
        print("O executável atualizado está em: dist/HeatmapsGenerator.exe")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print(f"❌ Erro durante a compilação (código {result.returncode})")
        print("=" * 60)

if __name__ == "__main__":
    build()
