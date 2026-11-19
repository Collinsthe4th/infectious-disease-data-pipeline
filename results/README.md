# Results

> **Real NCDC data, as of 19 November 2026.** All figures were read automatically from the situation reports of the Nigeria Centre for Disease Control and Prevention (NCDC) and checked by the pipeline's validation rules. The four most recent weeks may still be revised by later reports. No synthetic (demo) data is included.

This page is rebuilt automatically after every database load, so every number below comes from the database at the time of writing. The research questions (RQ1 to RQ4) are the ones in the thesis.

## 1. Data collected

The pipeline downloaded 913 NCDC report files. It read 525 of them automatically, set aside 248 (mostly reports from before the 2021 to 2026 study period) and sent 126 to the manual queue because their layout could not be read with confidence. Table 1 shows what was stored for each disease. Lassa fever had the most complete record, with 238 weeks. This is because NCDC publishes a Lassa fever report almost every week, while the other diseases are reported in short runs during outbreaks or once a month.

Table 1- National figures stored for each disease

| Disease | First week | Last week | Weeks | weekly figures |
|---|---|---|---|---|
| Cerebrospinal meningitis | 29 May 2023 | 31 Mar 2025 | 8 | yes |
| Cholera | 29 Mar 2021 | 24 Feb 2025 | 77 | yes |
| Diphtheria | 27 Nov 2023 | 12 Jan 2026 | 14 | yes |
| Lassa fever | 04 Jan 2021 | 26 Oct 2026 | 238 | yes |
| Measles | 30 Jun 2025 | 28 Sep 2026 | 16 | yes |
| Mpox | 24 May 2021 | 28 Oct 2024 | 112 | yes |
| Yellow fever | 25 Jan 2021 | 26 May 2025 | 40 | yes |


## 2. Pipeline runs (RQ1)

Out of 5 recorded runs, 1 finished successfully, 4 finished partly (the run completed but at least one report could not be read or loaded) and 0 failed. A partial run still saves everything it could read, and the reports it could not read are listed for review.

Table 2- The ten most recent pipeline runs

| started_at | run_type | status | files_new | rows_extracted | rows_passed | rows_warned | rows_quarantined | revisions_detected | seconds |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-19 17:31 | manual | partial | 0 | 311 | 307 | 27 | 4 | 7 | 1977 |
| 2026-11-19 13:17 | manual | partial | 0 | 5732 | 5439 | 84 | 293 | 17 | 15154 |
| 2026-11-19 10:34 | manual | partial | 13 | 11283 | 10878 | 389 | 360 | 2059 | 1513 |
| 2026-11-19 10:07 | backfill | partial | 892 | 331 | 283 | 31 | 48 | 0 | 5474 |
| 2026-11-19 09:34 | backfill | success | 21 | 21 | 20 | 4 | 1 | 0 | 218 |


## 3. Data quality

The validation rules held back 1,032 rows in quarantine (a holding area for figures that fail a check, so they never reach the dashboard). Table 3 shows which rule stopped them. Rows that passed but looked unusual were loaded with a warning flag instead (Table 4).

Table 3- Rows held back, by rule

| rule | rows_held_back | example |
|---|---|---|
| R09_LOCATION | 512 | location 'Abia ▼ 4 1' not in the HDX reference list |
| parse | 392 | parse failure: could not read the epidemiological week/year from the title page ('Nigeria Centre for Disease Control') |
| R02_NON_NEGATIVE | 89 | cum_confirmed fell from 10 to 2.0; weekly confirmed would be -8 |
| R04_DEATHS_LE_CASES | 32 | cum_deaths (2) > cum_confirmed (1) |
| R03_CONFIRMED_LE_SUSP | 5 | confirmed (10) > suspected (3.0) |
| R08_EPI_WEEK | 2 | printed dates 2020-12-27–2021-01-02 do not match ISO 2021-W52 (2021-12-27–2022-01-02) |


Table 4- Warnings on loaded rows

| flag_type | n |
|---|---|
| R12_COMPLETENESS | 6733 |
| R07_STATE_SUM | 154 |
| R06_CUM_DECREASE | 130 |
| R05_CFR_MISMATCH | 50 |


## 4. Revisions (RQ2)

Later reports changed 29 figures that had already been stored. The median revision lag (the number of weeks between the first report and the correction) was 0 weeks. Every change is kept in the revision history table with the old value, the new value and both source reports.

Table 5- Revisions by disease

| Disease | revisions | median_lag_weeks |
|---|---|---|
| Cholera | 2 | 0.0 |
| Lassa fever | 22 | 0.0 |
| Mpox | 5 | 0.0 |


## 5. Trends (RQ3)

Fig. 1 shows the weekly national figures for every disease that has them. Gaps are weeks with no published report, not weeks with zero cases.

![Weekly cases](figures/01_weekly_cases_all_diseases.png)

Fig. 1- Weekly national cases by disease. Source: the researcher, from NCDC situation reports.

I split the Lassa fever series into a trend and a seasonal pattern with STL (Seasonal-Trend decomposition using Loess). The seasonal pattern is highest in February, which matches the dry-season peak that NCDC describes every year (Fig. 2).

![Lassa STL](figures/02_lassa_stl.png)

Fig. 2- Lassa fever trend and seasonal pattern (STL). Source: the researcher.

## 6. Outbreak alerts (RQ3)

I ran the EARS C1, C2 and C3 checks (simple statistical tests from the US CDC's Early Aberration Reporting System that compare a week with the weeks before it) on every national series, and a SARIMA residual check on the longest series. Together they flagged 192 weeks out of 1,534 checks (Fig. 3).

![Alerts](figures/03_alerts_by_disease.png)

Fig. 3- Weeks flagged by each alert method. Source: the researcher.

Table 6- Alert checks by disease and method

| Disease | Method | Alerts | Weeks checked |
|---|---|---|---|
| Cerebrospinal meningitis | EARS C1 | 1 | 7 |
| Cerebrospinal meningitis | EARS C2 | 2 | 7 |
| Cerebrospinal meningitis | EARS C3 | 2 | 7 |
| Cholera | EARS C1 | 12 | 71 |
| Cholera | EARS C2 | 14 | 71 |
| Cholera | EARS C3 | 24 | 71 |
| Cholera | SARIMA residual | 0 | 44 |
| Diphtheria | EARS C1 | 3 | 12 |
| Diphtheria | EARS C2 | 5 | 12 |
| Diphtheria | EARS C3 | 8 | 12 |
| Lassa fever | EARS C1 | 14 | 231 |
| Lassa fever | EARS C2 | 29 | 229 |
| Lassa fever | EARS C3 | 45 | 227 |
| Lassa fever | SARIMA residual | 14 | 193 |
| Mpox | EARS C1 | 2 | 87 |
| Mpox | EARS C2 | 7 | 87 |
| Mpox | EARS C3 | 10 | 87 |
| Mpox | SARIMA residual | 0 | 79 |


## 7. Forecasting (RQ4)

I compared seven forecasting methods: a seasonal naive benchmark (the same week last year), SARIMA, a negative binomial regression, Random Forest, Gradient Boosting, XGBoost and support vector regression (SVR). Each method was tested with a rolling origin (it forecasts 1 to 4 weeks ahead from a point in the past, using only the data that existed at that point, then the point moves forward one week). Accuracy is reported as MAE (mean absolute error, in cases), RMSE (root mean squared error) and MASE (the error divided by the error of the seasonal naive benchmark, so a value below 1 means the method beat it). I used the Diebold-Mariano test (a test of whether two sets of forecast errors are really different) with the Harvey-Leybourne-Newbold correction for small samples to compare each method with SARIMA.

### Lassa fever

The models were trained on confirmed cases. Each was tested over 55 forecast origins. One week ahead, the most accurate method was **SVR** with a MASE of 0.557 and an average error of 6.8 cases per week. The next best was Negative binomial (MASE 0.602), so the best method was 7.5% more accurate. For comparison, SARIMA scored 0.619 and the seasonal naive benchmark scored 1.093. 6 of the 7 methods beat the seasonal naive benchmark one week ahead.

![MASE Lassa fever](figures/04_lassa_mase.png)

Fig. 4- Lassa fever: MASE by method and forecast horizon (the red line marks the seasonal naive benchmark). Source: the researcher.

Table 7- Lassa fever: MASE by method and weeks ahead

| Method | MASE 1 wk | MASE 2 wk | MASE 3 wk | MASE 4 wk |
|---|---|---|---|---|
| SVR | 0.557 | 0.632 | 0.715 | 0.755 |
| Negative binomial | 0.602 | 0.713 | 0.760 | 0.798 |
| SARIMA | 0.619 | 0.711 | 0.793 | 0.861 |
| Random Forest | 0.631 | 0.722 | 0.742 | 0.691 |
| Gradient Boosting | 0.657 | 0.739 | 0.836 | 0.833 |
| XGBoost | 0.706 | 0.762 | 0.802 | 0.757 |
| Seasonal naive | 1.093 | 1.109 | 1.125 | 1.134 |


The difference from SARIMA was statistically significant at the 5% level for Seasonal naive (p = 0.013). For the other methods the difference could be due to chance.

Table 8- Lassa fever: one-week-ahead accuracy and Diebold-Mariano test against SARIMA

| Method | MAE | RMSE | MASE | DM statistic | p-value |
|---|---|---|---|---|---|
| Gradient Boosting | 7.965 | 10.048 | 0.657 | -0.477 | 0.635 |
| Negative binomial | 7.300 | 9.656 | 0.602 | -1.007 | 0.318 |
| Random Forest | 7.653 | 9.529 | 0.631 | -0.752 | 0.456 |
| Seasonal naive | 13.255 | 18.254 | 1.093 | 2.577 | 0.013 |
| SVR | 6.753 | 9.150 | 0.557 | -1.110 | 0.272 |
| XGBoost | 8.556 | 10.687 | 0.706 | -0.017 | 0.986 |


![Forecast Lassa fever](figures/05_lassa_forecast.png)

Fig. 5- Lassa fever: one-week-ahead backtest and the forecast for the next four weeks. Source: the researcher.

### Diseases without a forecast

The methods need about two years of recent, regular weekly reports to learn the seasonal pattern and to be tested fairly. The diseases below did not meet that condition, so no forecast was made rather than one based on too little data.

Table 9- Diseases without a forecast

| Disease | Reason |
|---|---|
| Cholera | no weekly report since 24 Feb 2025, so a forecast would not be about the coming weeks |
| Cerebrospinal meningitis | no weekly report since 31 Mar 2025, so a forecast would not be about the coming weeks |
| Diphtheria | no weekly report since 12 Jan 2026, so a forecast would not be about the coming weeks |
| Measles | no weekly report since 29 Dec 2025, so a forecast would not be about the coming weeks |
| Mpox | no weekly report since 28 Oct 2024, so a forecast would not be about the coming weeks |
| Yellow fever | no weekly report since 30 May 2022, so a forecast would not be about the coming weeks |


## 8. Reproducibility

The forecasting results can be reproduced. Every method that uses randomness (Random Forest, Gradient Boosting and XGBoost) uses a fixed random seed (42), and the weekly figures the models were trained on are saved with this page in [`results/data`](data). The settings and the code version are saved in [`settings.json`](data/settings.json). Because NCDC revises its figures and the database is updated every week, a re-run on the live database can differ, but a re-run on the saved snapshot should give the same numbers.

When this page was built, I re-ran the full comparison from the snapshot without the database (`scripts/reproduce_results.py`) and compared the two accuracy tables (Table 10).

Table 10- Reproducibility check

| Disease | Reproduced | Rows compared | Largest relative difference |
|---|---|---|---|
| Lassa fever | yes | 48 | 3.8e-15 |


To reproduce the results yourself:

```bash
pip install -r requirements.txt
python scripts/reproduce_results.py results/data
```

## 9. Limitations

- Only diseases with about two years of regular weekly reports are forecast. For the others the dashboard shows trends and alerts only.
- State-level figures come from the Lassa fever state table, which is an image in the PDF and is read with OCR (optical character recognition). A state table is used only when every column adds up to its own total and the national confirmed total matches Table 1 of the same report, so some weeks are left out.
- Measles and yellow fever are now reported monthly, so only their year-to-date totals are stored.
- The Diebold-Mariano test is based on a limited number of test weeks, so small differences between methods may not be detected.
