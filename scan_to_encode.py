#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")

def main():
    for file in INPUT_DIR.glob("*"):
        base = file.stem
        output_path = OUTPUT_DIR / base

        if output_path.exists():
            print(f"▶ Déjà encodé : {file.name}")
            continue

        print(f"\n=== Nouvel encodage : {file.name} ===")

        subprocess.run(["python3", "encode_hls_av1.py"], check=True)

    print("\n✔ Vérification terminée.")

if __name__ == "__main__":
    main()
