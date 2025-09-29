# scripts/app_streamlit.py
import io
import os
import smtplib, ssl
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional
from math import sqrt
from io import BytesIO

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

import plotly.express as px
import plotly.graph_objects as go

from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error
from statsmodels.tsa.arima.model import ARIMA

# Exportables
from fpdf import FPDF
import matplotlib.pyplot as plt



# Configuración de la app
st.set_page_config(page_title="FinTech Data Analytics", page_icon="📈", layout="wide")

st.title("📊 FinTech Data Analytics – (Dashboard + Indicadores + Predicción + Walk-Forward + Alertas)")



# Helpers export/PDF 
def _ascii_safe(s: str) -> str:
    """Convierte a string y reemplaza símbolos fuera de latin-1 por equivalentes ASCII."""
    s = str(s)
    return (
        s.replace("σ", "sigma")
         .replace("–", "-")
         .replace("—", "-")
         .replace("↑", "up")
         .replace("↓", "down")
         .replace("⚡", "*")
         .replace("📧", "email")
    )

def build_summary(df: pd.DataFrame, price_col: str) -> pd.DataFrame:
    rets = df[price_col].pct_change().dropna()
    if rets.empty:
        return pd.DataFrame({"Métrica": [], "Valor": []})
    summary = {
        "Observaciones": len(rets),
        "Precio actual": float(df[price_col].iloc[-1]),
        "Retorno medio (día)": float(rets.mean()),
        "Volatilidad (día, sigma)": float(rets.std()),  # <- usar 'sigma' (ASCII)
        "VaR 95% (día)": float(np.percentile(rets, 5)),
    }
    return pd.DataFrame({"Métrica": summary.keys(), "Valor": summary.values()})

def make_matplotlib_line(df: pd.DataFrame, price_col: str, sma_windows=None, bb=None):
    """Devuelve un PNG en memoria con la línea + SMAs + (opcional) Bollinger."""
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(df["Date"], df[price_col], label=price_col)
    if sma_windows:
        for w in sma_windows:
            col = f"SMA{int(w)}"
            if col in df.columns:
                ax.plot(df["Date"], df[col], label=col)
    if bb:
        up, low, ma = bb
        if up in df.columns and low in df.columns:
            ax.plot(df["Date"], df[up],  label=up)
            ax.plot(df["Date"], df[low], label=low)
        if ma in df.columns:
            ax.plot(df["Date"], df[ma], label=ma)
    ax.set_title("Precio y Medias Móviles")
    ax.set_xlabel("Fecha")
    ax.set_ylabel("Precio (USD)")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.25)
    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf

def export_excel_bytes(df: pd.DataFrame, price_col: str, sma_windows, bb_cols, title="Reporte"):
    """Crea un Excel con datos, resumen y una pestaña con la figura incrustada."""
    summary_df = build_summary(df, price_col)
    img_buf = make_matplotlib_line(df, price_col, sma_windows, bb_cols)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Datos")
        summary_df.to_excel(writer, index=False, sheet_name="Resumen")

        workbook  = writer.book
        sheet     = workbook.add_worksheet("Gráfica")
        writer.sheets["Gráfica"] = sheet
        sheet.write(0, 0, _ascii_safe(title))
        sheet.insert_image("A3", "grafico.png", {"image_data": img_buf, "x_scale": 1.0, "y_scale": 1.0})
    output.seek(0)
    return output

def export_pdf_bytes(df: pd.DataFrame, price_col: str, sma_windows, bb_cols, title="Reporte"):
    """Crea un PDF sencillo con título, resumen y una imagen de la gráfica (latin-1 safe)."""
    summary_df = build_summary(df, price_col)
    img_buf = make_matplotlib_line(df, price_col, sma_windows, bb_cols)

    safe_title = _ascii_safe(title)
    safe_price_col = _ascii_safe(price_col)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, safe_title, ln=True)

    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(0, 8, _ascii_safe(f"Activo: {safe_title} | Columna de precio: {safe_price_col}"))

    pdf.ln(4)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, _ascii_safe("Resumen estadístico"), ln=True)
    pdf.set_font("Arial", "", 11)
    for _, row in summary_df.iterrows():
        k = _ascii_safe(row["Métrica"])
        v = row["Valor"]
        if "Retorno" in k or "Volatilidad" in k or "VaR" in k:
            line = f"- {k}: {v:.3%}"
        elif "Precio" in k:
            line = f"- {k}: {v:.2f}"
        else:
            line = f"- {k}: {v}"
        pdf.cell(0, 7, _ascii_safe(line), ln=True)

    # Imagen
    tmp_path = Path("data/processed/_tmp_plot.png")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "wb") as f:
        f.write(img_buf.getvalue())

    pdf.ln(4)
    pdf.image(str(tmp_path), x=10, w=190)

    out = pdf.output(dest="S")  # puede devolver bytes o str según la versión
    if isinstance(out, (bytes, bytearray)):
        pdf_bytes = bytes(out)
    else:
        pdf_bytes = out.encode("latin-1", "ignore")

    return BytesIO(pdf_bytes)


# Notificaciones (Email)
def _get_secret(key: str, default=None):
    # 1) Variables de entorno, 2) st.secrets (Streamlit Cloud), 3) default
    v = os.getenv(key, None)
    if v is not None:
        return v
    try:
        return st.secrets[key]
    except Exception:
        return default

def send_email(subject: str, body: str, to_email: str) -> str:
    user = _get_secret("SMTP_USER")
    password = _get_secret("SMTP_PASS")
    host = _get_secret("SMTP_HOST", "smtp.gmail.com")
    port = int(_get_secret("SMTP_PORT", "465"))
    if not (user and password and to_email):
        return "Faltan credenciales SMTP o destinatario."

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context) as server:
            server.login(user, password)
            server.send_message(msg)
        return "OK"
    except Exception as e:
        return f"Error email: {e}"



# Utilidades de normalización
def normalize_yahoo_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza DataFrames de yfinance/CSV a columnas estándar:
    Date, Open, High, Low, Close, Adj Close, Volume.
    Maneja índice datetime, MultiIndex y columnas duplicadas.
    """
    if df is None or df.empty:
        return df
    df = df.copy()

    # Aplanar MultiIndex si aparece
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[-1] if isinstance(c, tuple) else c for c in df.columns]

    # Índice datetime -> columna Date
    if isinstance(df.index, pd.DatetimeIndex):
        idx = df.index.tz_localize(None) if getattr(df.index, "tz", None) else df.index
        if "Date" not in df.columns:
            df.insert(0, "Date", pd.to_datetime(idx))
        df = df.reset_index(drop=True)

    # Si aún no hay Date, renombrar candidata
    if "Date" not in df.columns:
        first = df.columns[0]
        if str(first).lower() in ("date", "fecha", "index"):
            df = df.rename(columns={first: "Date"})

    # Coercionar y ordenar por fecha
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date")

    # Columnas únicas (evita error en pyarrow/Streamlit)
    seen, new_cols = {}, []
    for c in map(str, df.columns):
        if c in seen:
            seen[c] += 1
            new_cols.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            new_cols.append(c)
    df.columns = new_cols

    # Asegurar numéricos
    for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Eliminar duplicados residuales
    df = df.loc[:, ~df.columns.duplicated()]
    return df

def load_from_yahoo(tickers: List[str], period: str, interval: str) -> dict:
    """
    Descarga 1+ tickers. Devuelve dict[ticker] -> DataFrame normalizado.
    Maneja el caso MultiIndex cuando son varios tickers.
    """
    out = {}
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    data = yf.download(
        tickers,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        group_by="ticker",
    )
    if data is None or data.empty:
        return out

    if len(tickers) == 1:
        df = normalize_yahoo_df(data)
        out[tickers[0]] = df
        return out

    if isinstance(data.columns, pd.MultiIndex):
        for t in tickers:
            if t in data.columns.levels[0]:
                df_t = data[t].copy().reset_index()
                df_t = normalize_yahoo_df(df_t)
                out[t] = df_t
    else:
        for t in tickers:
            cols_t = [c for c in data.columns if str(c).startswith(t)]
            if cols_t:
                df_t = data[cols_t].copy()
                df_t.insert(0, "Date", data.index)
                df_t = normalize_yahoo_df(df_t)
                out[t] = df_t

    return out

# --- Fallback: Stooq via pandas-datareader ---


def _period_to_days(period: str) -> int:
    # '1mo','3mo','6mo','1y','2y','5y','max'
    mapping = {
        "1mo": 30, "3mo": 90, "6mo": 180,
        "1y": 365, "2y": 730, "5y": 1825
    }
    return mapping.get(str(period).lower(), 365)

def load_from_stooq(tickers: List[str], period: str, interval: str) -> dict:
    """
    Descarga precios diarios desde Stooq (solo '1d').
    Ignora intervalos intradía/weekly y usa último N días.
    """
    # Importación perezosa para evitar romper en Python 3.12/3.13 (sin distutils)
    try:
        from pandas_datareader import data as pdr
    except Exception as e:
        # Si falla (p. ej. por distutils), deshabilita el fallback silenciosamente
        print("Stooq fallback deshabilitado (pandas-datareader no disponible):", e)
        return {}

    out = {}
    days = _period_to_days(period)
    end = pd.Timestamp.utcnow().normalize()
    start = end - pd.Timedelta(days=days)
    for t in tickers:
        try:
            df = pdr.DataReader(t, "stooq", start=start, end=end)
            if df is None or df.empty:
                continue
            df = df.sort_index().reset_index()
            df = df.rename(columns={
                "Date": "Date",
                "Open": "Open", "High": "High", "Low": "Low",
                "Close": "Close", "Volume": "Volume"
            })
            if "Adj Close" not in df.columns:
                df["Adj Close"] = df["Close"]
            out[t] = normalize_yahoo_df(df)
        except Exception:
            continue
    return out


def load_data_with_fallback(tickers: List[str], period: str, interval: str) -> dict:
    """
    1) Intenta Yahoo (yfinance)
    2) Si falla / vacío, intenta Stooq (diario)
    """
    data = load_from_yahoo(tickers, period, interval)
    if data:
        return data
    # Fallback: forzamos diario porque Stooq no soporta otros intervalos
    return load_from_stooq(tickers, period, "1d")


def pick_close_col(df: pd.DataFrame, prefer_adj=True) -> Optional[str]:
    """Devuelve 'Adj Close' si existe; si no, 'Close'."""
    cols = {c.lower(): c for c in df.columns}
    if prefer_adj and "adj close" in cols:
        return cols["adj close"]
    if "close" in cols:
        return cols["close"]
    for k in ("adjclose", "adj_close"):
        if k in cols:
            return cols[k]
    return None



# Indicadores técnicos
def compute_sma(df: pd.DataFrame, price_col: str, windows):
    for w in windows:
        df[f"SMA{int(w)}"] = df[price_col].rolling(int(w), min_periods=1).mean()

def compute_bbands(df: pd.DataFrame, price_col: str, period: int = 20, k: float = 2.0):
    ma = df[price_col].rolling(int(period), min_periods=1).mean()
    std = df[price_col].rolling(int(period), min_periods=1).std()
    df[f"BB_MA_{int(period)}"]  = ma
    df[f"BB_UP_{int(period)}"]  = ma + float(k) * std
    df[f"BB_LOW_{int(period)}"] = ma - float(k) * std

def stats_block(df: pd.DataFrame, price_col: str):
    rets = df[price_col].pct_change().dropna()
    if rets.empty:
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Precio actual", f"{df[price_col].iloc[-1]:.2f} USD")
    c2.metric("Retorno medio (día)", f"{rets.mean():.3%}")
    c3.metric("Volatilidad (día, σ)", f"{rets.std():.3%}")
    c4.metric("VaR 95% (día)", f"{np.percentile(rets, 5):.3%}")



# Predicción 
def eval_metrics(y_true, y_pred):
    y_true = pd.Series(y_true).astype(float).values
    y_pred = pd.Series(y_pred).astype(float).values
    mae = mean_absolute_error(y_true, y_pred)
    rmse = sqrt(mean_squared_error(y_true, y_pred))
    mape = (np.abs((y_true - y_pred) / y_true).mean()) * 100
    return mae, rmse, mape

def make_time_index(df: pd.DataFrame):
    df = df.copy().reset_index(drop=True)
    df["t"] = np.arange(len(df))
    return df

def predict_linear(df: pd.DataFrame, price_col: str, horizon=7, backtest=30):
    df = make_time_index(df)
    X = df[["t"]].values
    y = df[price_col].values

    n = len(df)
    bt = min(backtest, n // 3) if n > 10 else 0
    if bt > 0:
        model_bt = LinearRegression().fit(X[: n - bt], y[: n - bt])
        yhat_bt = model_bt.predict(X[n - bt : n])
        mae, rmse, mape = eval_metrics(y[n - bt : n], yhat_bt)
    else:
        mae = rmse = mape = np.nan

    model = LinearRegression().fit(X, y)
    last_t = df["t"].iloc[-1]
    future_t = np.arange(last_t + 1, last_t + int(horizon) + 1)
    y_fut = model.predict(future_t.reshape(-1, 1))

    last_date = pd.to_datetime(df["Date"].iloc[-1])
    fut_dates = pd.date_range(last_date + pd.Timedelta(days=1), periods=int(horizon), freq="D")
    forecast_df = pd.DataFrame({"Date": fut_dates, "Forecast": y_fut, "lo95": np.nan, "hi95": np.nan})

    yhat_insample = model.predict(X)
    insample_df = df[["Date"]].copy()
    insample_df["Fitted"] = yhat_insample

    return forecast_df, insample_df, (mae, rmse, mape)

def select_arima_order(series: pd.Series, p_max=3, d_max=2, q_max=3):
    """Búsqueda rápida por AIC en rejilla pequeña (p,d,q)."""
    best_aic = np.inf
    best_order = (1, 1, 1)
    s = series.dropna()
    for p in range(p_max + 1):
        for d in range(d_max + 1):
            for q in range(q_max + 1):
                if p == 0 and d == 0 and q == 0:
                    continue
                try:
                    model = ARIMA(s, order=(p, d, q))
                    res = model.fit(method_kwargs={"warn_convergence": False})
                    if res.aic < best_aic:
                        best_aic = res.aic
                        best_order = (p, d, q)
                except Exception:
                    continue
    return best_order

def predict_arima(df: pd.DataFrame, price_col: str, horizon=7, backtest=30, use_auto=True):
    """ARIMA con statsmodels. Si use_auto=True, elige (p,d,q) por AIC."""
    s = df.set_index("Date")[price_col].asfreq("D").interpolate(limit_direction="both")
    order = select_arima_order(s) if use_auto else (1, 1, 1)

    bt = min(backtest, max(7, len(s)//5)) if len(s) > 20 else 0
    if bt > 0:
        train, test = s.iloc[:-bt], s.iloc[-bt:]
        try:
            res_bt = ARIMA(train, order=order).fit()
            yhat_bt = res_bt.forecast(steps=len(test))
            mae, rmse, mape = eval_metrics(test.values, yhat_bt.values)
        except Exception:
            mae = rmse = mape = np.nan
    else:
        mae = rmse = mape = np.nan

    res = ARIMA(s, order=order).fit()
    fc = res.get_forecast(steps=int(horizon))
    y_fut = fc.predicted_mean
    ci = fc.conf_int(alpha=0.05)
    lo95 = ci.iloc[:, 0].values
    hi95 = ci.iloc[:, 1].values

    fut_idx = pd.date_range(s.index[-1] + pd.Timedelta(days=1), periods=int(horizon), freq="D")
    forecast_df = pd.DataFrame({"Date": fut_idx, "Forecast": y_fut.values, "lo95": lo95, "hi95": hi95})
    insample_df = pd.DataFrame({"Date": s.index, "Fitted": res.fittedvalues})

    return forecast_df, insample_df, (mae, rmse, mape), order


# Walk-forward backtest 
def walk_forward_forecast(series: pd.Series, model_fn, splits=5, horizon=7):
    """
    series: pd.Series con índice Date y valores float (frecuencia diaria).
    model_fn: callable que recibe (train_series) y devuelve un modelo con .forecast(steps).
    """
    s = series.dropna()
    n = len(s)
    if n < (splits + 1) * horizon:
        splits = max(1, n // (2 * horizon))
    if splits <= 0:
        return np.nan, np.nan, np.nan

    fold_size = n // (splits + 1)
    results = []
    for i in range(1, splits + 1):
        end = fold_size * i
        train = s.iloc[:end]
        test = s.iloc[end : end + horizon]
        if len(test) == 0:
            break
        try:
            model = model_fn(train)
            yhat = model.forecast(steps=len(test))
            mae, rmse, mape = eval_metrics(test.values, pd.Series(yhat, index=test.index).values)
            results.append((mae, rmse, mape))
        except Exception:
            continue

    if not results:
        return np.nan, np.nan, np.nan

    arr = np.array(results)
    return float(arr[:, 0].mean()), float(arr[:, 1].mean()), float(arr[:, 2].mean())

def arima_wrapper(order=(1, 1, 1)):
    def _fit(train_series: pd.Series):
        return ARIMA(train_series, order=order).fit()
    return _fit

def linear_wrapper():
    def _fit(train_series: pd.Series):
        df = train_series.reset_index()
        df["t"] = np.arange(len(df))
        lr = LinearRegression().fit(df[["t"]].values, df.iloc[:, 1].values)

        class _Model:
            def forecast(self, steps: int):
                last_t = df["t"].iloc[-1]
                fut = np.arange(last_t + 1, last_t + steps + 1).reshape(-1, 1)
                return lr.predict(fut)
        return _Model()
    return _fit


#  Alertas (Semana 5) 
def daily_change_alert(df: pd.DataFrame, price_col: str, pct: float = 5.0):
    """Alerta si el último retorno diario supera +/-pct%."""
    if len(df) < 2:
        return None
    ret = df[price_col].pct_change().iloc[-1]
    if pd.isna(ret):
        return None
    hit = abs(ret) >= (pct / 100.0)
    direction = "↑" if ret > 0 else "↓"
    return {
        "active": bool(hit),
        "type": "Movimiento diario",
        "message": f"{direction} {ret:.2%} vs ayer (umbral {pct:.1f}%)",
        "severity": "success" if ret > 0 else "error",
        "value": ret,
    }

def sma_cross_alert(df: pd.DataFrame, price_col: str, fast: int = 20, slow: int = 50):
    """Alerta si hay cruce de SMA (fast cruza slow) en la última vela."""
    if len(df) < slow + 2:
        return None
    s_fast = df[price_col].rolling(fast, min_periods=1).mean()
    s_slow = df[price_col].rolling(slow, min_periods=1).mean()
    prev_diff = s_fast.iloc[-2] - s_slow.iloc[-2]
    last_diff = s_fast.iloc[-1] - s_slow.iloc[-1]
    crossed_up   = prev_diff <= 0 and last_diff > 0
    crossed_down = prev_diff >= 0 and last_diff < 0
    if crossed_up:
        return {"active": True, "type": "Cruce SMA", "message": f"⚡ Cruce alcista: SMA{fast} ↑ SMA{slow}", "severity": "success"}
    if crossed_down:
        return {"active": True, "type": "Cruce SMA", "message": f"⚡ Cruce bajista: SMA{fast} ↓ SMA{slow}", "severity": "error"}
    return {"active": False, "type": "Cruce SMA", "message": "Sin cruce reciente", "severity": "info"}



# Sidebar / Controles
with st.sidebar:
    st.header("⚙️ Configuración")
    source = st.radio("Fuente de datos", ["Yahoo Finance", "Subir CSV"], index=0)
    chart_type = st.selectbox(
        "Tipo de visualización",
        [
            "Línea",
            "Velas (candlestick) + SMA",
            "Histograma de retornos",
            "Comparar varios (línea + correlación)",
            "Predicción 7 días (Regresión / ARIMA)",
        ],
        index=0,
    )

    if source == "Yahoo Finance":
        tickers_input = st.text_input(
            "Ticker(s)",
            value="AMZN" if "Comparar" not in chart_type else "AMZN, GOOGL, MSFT",
            help="Separa con coma para varios (ej: AMZN, GOOGL)",
        )
        period = st.selectbox("Período", ["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"], index=3)
        interval = st.selectbox("Intervalo", ["1d", "1wk", "1mo"], index=0)
        fetch = st.button("Descargar datos")
    else:
        uploaded = st.file_uploader("Subir CSV", type="csv")

    st.subheader("Medias móviles / Bollinger")
    sma_input = st.text_input("SMAs (días, coma)", value="20,50", help="Ej: 10,20,50,200")
    use_bbands = st.checkbox("Mostrar Bandas de Bollinger", value=True)
    bb_period  = st.number_input("BB período", min_value=5, max_value=200, value=20, step=1)
    bb_k       = st.number_input("BB multiplicador (k)", min_value=0.5, max_value=4.0, value=2.0, step=0.1)

    st.subheader("Predicción (Semana 4)")
    model_kind = st.selectbox("Modelo", ["Regresión lineal", "ARIMA (auto)"])
    horizon = st.number_input("Horizonte (días)", min_value=3, max_value=30, value=7, step=1)
    backtest_days = st.number_input("Backtest simple (días)", min_value=0, max_value=120, value=30, step=1)

    st.subheader("Alertas (Semana 5)")
    alert_pct = st.number_input("Umbral movimiento diario (%)", min_value=0.5, max_value=50.0, value=5.0, step=0.5)
    fast_sma = st.number_input("SMA rápida (cruce)", min_value=2, max_value=200, value=20, step=1)
    slow_sma = st.number_input("SMA lenta (cruce)", min_value=5, max_value=400, value=50, step=1)

    st.subheader("Notificaciones")
    enable_email = st.checkbox("Enviar email al disparar alerta", value=False)
    email_to = st.text_input("Email destino", value="", placeholder="alguien@correo.com")

    st.subheader("Descargas")
    show_downloads = st.checkbox("Mostrar botones de descarga", value=True)

st.divider()

# Parsear SMAs
try:
    SMA_WINDOWS = [int(x.strip()) for x in sma_input.split(",") if x.strip()]
except Exception:
    SMA_WINDOWS = [20, 50]

# Cargar datos

data_dict = {}

if source == "Yahoo Finance":
    if 'fetch_clicked' not in st.session_state:
        st.session_state['fetch_clicked'] = False
    if fetch:
        st.session_state['fetch_clicked'] = True

    if st.session_state['fetch_clicked']:
        tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]
        data_dict = load_data_with_fallback(tickers, period, interval)  # ✅ con fallback
        if not data_dict:
            st.warning("No se descargaron datos. Revisa tickers / conexión.")
else:
    if uploaded is not None:
        df = pd.read_csv(uploaded)
        df = normalize_yahoo_df(df)  # ✅ corregido
        data_dict = {"CSV": df}

if not data_dict:
    st.info("Carga un CSV o descarga datos desde Yahoo para comenzar.")
    st.stop()


# Primer dataset como activo principal
first_key = next(iter(data_dict.keys()))
df0 = data_dict[first_key]
df0 = df0.loc[:, ~df0.columns.duplicated()]  # safety

st.subheader("📄 Vista previa")
st.dataframe(df0.head(30), use_container_width=True)



st.subheader("📈 Visualización")

# VISTAS=
if chart_type == "Línea":
    price_col = pick_close_col(df0)
    if price_col is None:
        st.warning("No encontré columna 'Close' o 'Adj Close'.")
    else:
        if len(data_dict) == 1:
            df_plot = df0.copy()
            compute_sma(df_plot, price_col, SMA_WINDOWS)
            if use_bbands:
                compute_bbands(df_plot, price_col, period=int(bb_period), k=float(bb_k))

            fig = px.line(df_plot, x="Date", y=price_col, title=f"{first_key}: {price_col}")

            for w in SMA_WINDOWS:
                col = f"SMA{w}"
                if col in df_plot.columns:
                    fig.add_scatter(x=df_plot["Date"], y=df_plot[col], mode="lines", name=col)

            if use_bbands:
                up  = f"BB_UP_{int(bb_period)}"
                low = f"BB_LOW_{int(bb_period)}"
                ma  = f"BB_MA_{int(bb_period)}"
                if up in df_plot.columns and low in df_plot.columns:
                    fig.add_traces([
                        go.Scatter(x=df_plot["Date"], y=df_plot[up],  mode="lines", name=f"BB Upper ({int(bb_period)},{bb_k})"),
                        go.Scatter(x=df_plot["Date"], y=df_plot[low], mode="lines", name=f"BB Lower ({int(bb_period)},{bb_k})",
                                   fill='tonexty')
                    ])
                if ma in df_plot.columns:
                    fig.add_scatter(x=df_plot["Date"], y=df_plot[ma], mode="lines", name=f"BB MA ({int(bb_period)})")

            st.plotly_chart(fig, use_container_width=True)
            stats_block(df_plot, price_col)

        else:
            merged = None
            for k, dfk in data_dict.items():
                col = pick_close_col(dfk)
                if col:
                    d = dfk[["Date", col]].rename(columns={col: k})
                    merged = d if merged is None else pd.merge_asof(
                        merged.sort_values("Date"),
                        d.sort_values("Date"),
                        on="Date"
                    )
            if merged is not None:
                fig = px.line(merged, x="Date", y=merged.columns[1:], title="Comparación (línea)")
                st.plotly_chart(fig, use_container_width=True)

elif chart_type == "Velas (candlestick) + SMA":
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(set(df0.columns)):
        st.warning("Para velas necesito columnas: Open, High, Low, Close.")
    else:
        price_col = "Close" if "Close" in df0.columns else pick_close_col(df0, prefer_adj=False)
        df_plot = df0.copy()
        compute_sma(df_plot, price_col, SMA_WINDOWS)
        if use_bbands:
            compute_bbands(df_plot, price_col, period=int(bb_period), k=float(bb_k))

        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=df_plot["Date"],
                    open=df_plot["Open"],
                    high=df_plot["High"],
                    low=df_plot["Low"],
                    close=df_plot["Close"],
                    name=first_key,
                )
            ]
        )

        for w in SMA_WINDOWS:
            col = f"SMA{w}"
            if col in df_plot.columns:
                fig.add_trace(go.Scatter(x=df_plot["Date"], y=df_plot[col], mode="lines", name=col))

        if use_bbands:
            up  = f"BB_UP_{int(bb_period)}"
            low = f"BB_LOW_{int(bb_period)}"
            ma  = f"BB_MA_{int(bb_period)}"
            if up in df_plot.columns and low in df_plot.columns:
                fig.add_trace(go.Scatter(x=df_plot["Date"], y=df_plot[up],  mode="lines", name=f"BB Upper ({int(bb_period)},{bb_k})"))
                fig.add_trace(go.Scatter(x=df_plot["Date"], y=df_plot[low], mode="lines", name=f"BB Lower ({int(bb_period)},{bb_k})",
                                         fill='tonexty'))
            if ma in df_plot.columns:
                fig.add_trace(go.Scatter(x=df_plot["Date"], y=df_plot[ma], mode="lines", name=f"BB MA ({int(bb_period)})"))

        fig.update_layout(title=f"{first_key}: Velas + SMA(s) + Bollinger", xaxis_title="Fecha", yaxis_title="Precio (USD)")
        st.plotly_chart(fig, use_container_width=True)
        stats_block(df_plot, price_col)

elif chart_type == "Histograma de retornos":
    price_col = pick_close_col(df0)
    if price_col is None:
        st.warning("No encontré columna de precio para calcular retornos.")
    else:
        returns = df0[price_col].pct_change().dropna()
        fig = px.histogram(returns, nbins=50, title=f"{first_key}: Retornos diarios")
        fig.update_xaxes(tickformat=".1%")
        st.plotly_chart(fig, use_container_width=True)
        stats_block(df0, price_col)
        st.write(f"**Observaciones:** n={len(returns)}, media={returns.mean():.3%}, σ={returns.std():.3%}, VaR95={np.percentile(returns,5):.3%}")

elif chart_type == "Comparar varios (línea + correlación)":
    if len(data_dict) < 2:
        st.warning("Escribe 2+ tickers en la barra lateral, separados por coma, y presiona Descargar datos.")
    else:
        merged = None
        names = []
        for k, dfk in data_dict.items():
            col = pick_close_col(dfk)
            if col:
                d = dfk[["Date", col]].rename(columns={col: k})
                names.append(k)
                merged = d if merged is None else pd.merge_asof(
                    merged.sort_values("Date"), d.sort_values("Date"), on="Date"
                )
        if merged is not None and len(names) >= 2:
            fig = px.line(merged, x="Date", y=names, title=f"Comparación: {', '.join(names)}")
            st.plotly_chart(fig, use_container_width=True)

            st.write("### Correlación de retornos diarios")
            rets = merged[names].pct_change().dropna()
            corr = rets.corr()
            figc = px.imshow(corr, text_auto=True, title="Matriz de correlación", aspect="auto", zmin=-1, zmax=1)
            st.plotly_chart(figc, use_container_width=True)
            st.dataframe(corr.style.format("{:.2f}"), use_container_width=True)


elif chart_type == "Predicción 7 días (Regresión / ARIMA)":
    price_col = pick_close_col(df0)
    if price_col is None:
        st.warning("No encontré columna 'Close' o 'Adj Close'.")
    else:
        st.write(f"Usando columna de precio: **{price_col}**")
        if model_kind == "Regresión lineal":
            fcast_df, fitted_df, (mae, rmse, mape) = predict_linear(
                df0, price_col, horizon=int(horizon), backtest=int(backtest_days)
            )
            title = f"{first_key}: Predicción (Regresión lineal) {int(horizon)}d"
            fig = px.line(df0, x="Date", y=price_col, title=title)
            fig.add_scatter(x=fitted_df["Date"], y=fitted_df["Fitted"], mode="lines", name="Fitted (in-sample)")
            fig.add_scatter(x=fcast_df["Date"], y=fcast_df["Forecast"], mode="lines+markers", name="Forecast")
            st.plotly_chart(fig, use_container_width=True)
            st.success(f"MAE={mae:.3f} | RMSE={rmse:.3f} | MAPE={mape:.2f}%")
        else:
            fcast_df, fitted_df, (mae, rmse, mape), order = predict_arima(
                df0, price_col, horizon=int(horizon), backtest=int(backtest_days), use_auto=True
            )
            title = f"{first_key}: Predicción (ARIMA {order}) {int(horizon)}d"
            fig = px.line(df0, x="Date", y=price_col, title=title)
            fig.add_scatter(x=fitted_df["Date"], y=fitted_df["Fitted"], mode="lines", name="Fitted (in-sample)")
            fig.add_scatter(x=fcast_df["Date"], y=fcast_df["Forecast"], mode="lines+markers", name="Forecast")
            fig.add_scatter(x=fcast_df["Date"], y=fcast_df["hi95"], mode="lines", line=dict(dash="dot"), name="hi95")
            fig.add_scatter(x=fcast_df["Date"], y=fcast_df["lo95"], mode="lines", fill="tonexty", line=dict(dash="dot"), name="lo95")
            st.plotly_chart(fig, use_container_width=True)
            st.success(f"Orden ARIMA elegido: {order} | MAE={mae:.3f} | RMSE={rmse:.3f} | MAPE={mape:.2f}%")

        # Comparativa por walk-forward
        st.write("## 📊 Comparativa por walk-forward")
        with st.spinner("Calculando backtests (walk-forward)…"):
            s_daily = df0.set_index("Date")[price_col].asfreq("D").interpolate(limit_direction="both")
            arima_order = select_arima_order(s_daily)

            mae_lr,  rmse_lr,  mape_lr  = walk_forward_forecast(
                s_daily, linear_wrapper(), splits=5, horizon=int(horizon)
            )
            mae_ar,  rmse_ar,  mape_ar  = walk_forward_forecast(
                s_daily, arima_wrapper(order=arima_order), splits=5, horizon=int(horizon)
            )

        comp_df = pd.DataFrame(
            {"Modelo": ["Regresión lineal", f"ARIMA {arima_order}"],
             "MAE": [mae_lr, mae_ar], "RMSE": [rmse_lr, rmse_ar], "MAPE (%)": [mape_lr, mape_ar]}
        )
        styler = (
            comp_df.style
            .format({"MAE": "{:.3f}", "RMSE": "{:.3f}", "MAPE (%)": "{:.2f}"})
            .highlight_min(subset=["MAE", "RMSE", "MAPE (%)"], color="#d4edda")
        )
        st.dataframe(styler, use_container_width=True)

        st.caption("Walk-forward: 5 folds, horizonte = "
                   f"{int(horizon)} días. Verde = mejor (menor) por métrica.")


# ==== Panel de Alertas (siempre visible) ====
st.divider()
st.subheader("🚨 Alertas")

price_col_global = pick_close_col(df0)
if price_col_global:
    a1 = daily_change_alert(df0, price_col_global, pct=float(alert_pct))
    a2 = sma_cross_alert(df0, price_col_global, fast=int(fast_sma), slow=int(slow_sma))
    alerts = [a for a in [a1, a2] if a]

    if not alerts:
        st.info("No hay alertas calculadas.")
    else:
        # Mostrar en UI
        for a in alerts:
            if not a.get("active", True) and a.get("severity") == "info":
                st.info(a["message"])
            elif a.get("severity") == "success":
                st.success(f"[{_ascii_safe(a['type'])}] {_ascii_safe(a['message'])}")
            elif a.get("severity") == "error":
                st.error(f"[{_ascii_safe(a['type'])}] {_ascii_safe(a['message'])}")
            else:
                st.warning(f"[{_ascii_safe(a['type'])}] {_ascii_safe(a['message'])}")

        # Enviar email (si está habilitado) solo si hay alertas activas
        active_alerts = [a for a in alerts if a.get("active", False)]
        if enable_email and email_to and active_alerts:
            body = "\n".join([f"{_ascii_safe(a['type'])}: {_ascii_safe(a['message'])}" for a in active_alerts])
            subject = f"ALERTA {_ascii_safe(first_key)}"
            result = send_email(subject, body, email_to)
            if result == "OK":
                st.toast("Email enviado con éxito", icon="✉️")
            else:
                st.error(f"No se pudo enviar email: {result}")


# ==== Descarga (Excel / PDF) ====
if show_downloads:
    st.divider()
    st.subheader("⬇️ Exportar reporte (Excel / PDF)")
    price_col_dl = pick_close_col(df0) or "Close"
    bb_cols = None
    if use_bbands:
        bb_cols = (f"BB_UP_{int(bb_period)}", f"BB_LOW_{int(bb_period)}", f"BB_MA_{int(bb_period)}")
        # Si aún no existen (p.ej. no entraste a vista 'Línea'), calcúlalas para el export
        if bb_cols[0] not in df0.columns or bb_cols[1] not in df0.columns or bb_cols[2] not in df0.columns:
            df0_tmp = df0.copy()
            compute_bbands(df0_tmp, price_col_dl, period=int(bb_period), k=float(bb_k))
            df_for_export = df0_tmp
        else:
            df_for_export = df0
    else:
        df_for_export = df0

    col1, col2 = st.columns(2)
    with col1:
        excel_bytes = export_excel_bytes(df_for_export, price_col_dl, SMA_WINDOWS, bb_cols, title=f"Reporte {first_key}")
        st.download_button(
            label="📘 Descargar Excel",
            data=excel_bytes,
            file_name=f"reporte_{first_key}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col2:
        pdf_bytes   = export_pdf_bytes(df_for_export, price_col_dl, SMA_WINDOWS, bb_cols, title=f"Reporte {first_key}")
        st.download_button(
            label="📄 Descargar PDF",
            data=pdf_bytes,
            file_name=f"reporte_{first_key}.pdf",
            mime="application/pdf",
        )
