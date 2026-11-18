# Glossary

| Term | Meaning |
|---|---|
| **Epi week** | ISO 8601 week, Monday to Sunday. `week_id` = year × 100 + week (e.g. 202405). |
| **Sitrep** | NCDC situation report for one disease and week (PDF). |
| **WER** | NCDC Weekly Epidemiological Report (several diseases). |
| **P-code** | HDX administrative code: `NG` national, `NG001`-`NG037` states. |
| **Raw / staging / curated** | Storage zones: original files; validated rows waiting to load; analysis-ready star schema. |
| **Fact / dimension** | Star schema: measurements (cases) vs the things that describe them (disease, place, week, report). |
| **Grain** | What one fact row means: one disease × location × epi week. |
| **Upsert** | Insert a row, or update it if it already exists. |
| **Revision** | NCDC changing a figure it published earlier; stored in `revision_history`. |
| **Quarantine** | Rows (or whole reports) held back because they failed an error-level rule. |
| **Flag** | A warning-level result: the row is loaded but marked. |
| **Provisional** | One of the four most recent weeks for a disease: likely to change. |
| **Manual queue** | Documents the parser could not read; need a template or hand entry. |
| **Template** | Instructions for reading one family of report layouts. |
| **CFR** | Case fatality rate = deaths ÷ cases × 100 (confirmed or suspected, by disease). |
| **STL** | Seasonal-Trend decomposition using Loess. |
| **EARS C1/C2/C3** | CDC Early Aberration Reporting System statistics, adapted to weeks. |
| **SARIMA** | Seasonal ARIMA model; here on log(1 + cases) with a 52-week season. |
| **Rolling origin** | Evaluation that forecasts repeatedly from successive past weeks using only data available then. |
| **MASE** | Mean absolute scaled error; < 1 beats the seasonal naive benchmark. |
| **Diebold-Mariano** | Test of whether two models' forecast errors differ significantly. |
| **Synthetic** | Generated demo data; never real NCDC figures. |
