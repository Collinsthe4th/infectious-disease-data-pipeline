# Changelog

## 0.1.0 (2026-11-18): working prototype
- Seven-layer pipeline: NCDC crawler and inbox, template-driven PDF parsing, 12 validation rules with quarantine, PostgreSQL star schema with trigger-based revision history.
- Analytics: STL, EARS C1-C3, SARIMA residual z-scores, Lassa fever 1-4 week forecasts (seasonal naive, SARIMA, NB, RF, GB, XGBoost, SVR) with rolling-origin MAE/RMSE/MASE and Diebold-Mariano tests.
- Streamlit dashboard (overview, trends, states, forecasts, alerts, pipeline health).
- Docker Compose, scheduler container, Airflow DAG, GitHub Actions CI (unit + PostgreSQL integration + dashboard), weekly real-data parse check.
- Synthetic demo dataset loaded through the real pipeline.
