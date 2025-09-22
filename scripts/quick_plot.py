# scripts/quick_plot.py
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

CSV_PATH = Path("data/raw/amzn.csv")

df = pd.read_csv(CSV_PATH)

# Asegurar que tenga la columna Date
if "Date" not in df.columns:
    df = pd.read_csv(CSV_PATH, header=0)

# Convertir fechas
df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
df = df.dropna(subset=["Date"]).sort_values("Date")

# Graficar Close y Adj Close si existen
plt.figure(figsize=(12, 5))

if "Close" in df.columns:
    plt.plot(df["Date"], df["Close"], label="Close", color="blue")

if "Adj Close" in df.columns:
    plt.plot(df["Date"], df["Adj Close"], label="Adj Close", color="orange")

plt.title("AMZN – Precio de Cierre vs Ajustado")
plt.xlabel("Fecha")
plt.ylabel("Precio (USD)")
plt.legend()
plt.grid(True, linestyle="--", alpha=0.5)

# Mejorar ticks del eje X
plt.xticks(rotation=45)
plt.tight_layout()

# Guardar imagen
out_img = Path("data/processed/amzn_close_vs_adj.png")
out_img.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(out_img, dpi=150)
print(f"✅ Gráfica comparativa guardada en: {out_img}")
