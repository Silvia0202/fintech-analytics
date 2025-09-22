# scripts/fetch_yahoo.py
import sys
from pathlib import Path
import yfinance as yf
import pandas as pd

print("Descargando datos de AMZN ...")

try:
    # group_by='column' evita el MultiIndex tipo ('AMZN','Close')
    df = yf.download(
        "AMZN",
        period="1y",
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="column",
    )

    if df is None or df.empty:
        print("No se descargaron datos (df vacío). Revisa tu conexión a internet o intenta de nuevo.")
        sys.exit(1)

    # Asegurar que el índice se llame 'Date' y pase a columna
    if df.index.name is None:
        df.index.name = "Date"
    df = df.reset_index()

    out_dir = Path("data/raw")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "amzn.csv"

    df.to_csv(out_path, index=False)  # CSV limpio con encabezado Date,Open,High,Low,Close,Adj Close,Volume
    print(f"Guardado: {out_path} ({len(df)} filas)")
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
