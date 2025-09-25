# FinTech Analytics Dashboard

Dashboard interactivo en **Streamlit** para analizar activos financieros con:
- Descarga de datos desde Yahoo Finance
- Cálculo de indicadores técnicos (SMAs, Bandas de Bollinger, etc.)
- Predicciones de precios (Regresión lineal, ARIMA)
- Backtesting con walk-forward
- Alertas automáticas (cambios diarios, cruces de medias)
- Notificaciones por email
- Exportación a PDF y Excel

## Instalación

```bash
git clone https://github.com/Silvia0202/fintech-analytics.git
cd fintech-analytics
python -m venv .venv
.venv\Scripts\activate   # en Windows
pip install -r requirements.txt
