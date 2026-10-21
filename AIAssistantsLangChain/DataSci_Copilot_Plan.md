# Plan: DataSci Copilot — an AI data science and data observability platform

## Context
The starting point was a 2023 class notebook (`AIAssistantsLangChain/GA_NLP_LVC_10_...ipynb`), hard-wired to `diabetes.csv`, built on deprecated LangChain APIs, and running arbitrary LLM-written code. The user wants a **full application** instead:
- **Connectors:** connect many data sources and combine them.
- **Rules:** upload and manage **business rules** and **data-validation rules** (create, edit, version, test, roll back).
- **Data scientist assistant:** chat with an AI assistant that, given a target, does EDA, feature selection and engineering, model selection, the split, training and evaluation (F1, precision, recall, ROC-AUC, confusion-matrix heatmap and more).
- **Weekly runs:** scheduled runs with full **data observability** (quality, drift, trends, model performance) and email alerts.
- **Storage:** Bronze raw Parquet files, plus Silver and Gold in **ArcadeDB**, which runs in Docker on the user's other laptop.
- **Later:** fine-tune and compare open-source LLMs on a 36 GB Apple-silicon Mac.

Decisions made with the user: a **small team** of users with roles; the app runs **on the ArcadeDB laptop** in Docker; Phase 1 connectors are **files + SQL Server + Oracle**; Claude as the AI; a hybrid framework (LangGraph orchestration + the official Anthropic SDK); a medallion architecture with ArcadeDB for Silver/Gold; drift checks built in-house; email + report alerts; label timing configured per project.

## Core design principles
1. **Claude decides and explains; tested Python code does the work.** No free-form LLM-written code. Every step is a deterministic, typed function.
2. **Rules are enforced in code**, not only in prompts. Claude can't override them.
3. **A human approves the important steps:** the target and plan, the feature list, rule activation, and swapping in a new production model.
4. **Everything is versioned and traceable:** each run records its source snapshot, rule versions, code version and model version.
5. **The deterministic pipeline runs without the AI.** If Claude is unavailable, data loading, validation, drift checks, scoring and reports still run; only the written summaries are skipped.

## Application architecture
```
┌──────────────┐    HTTP    ┌───────────────────────────────┐        ┌────────────────────┐
│ Streamlit UI │ ─────────► │ FastAPI backend               │ ─────► │ ArcadeDB (remote   │
│ (pages below)│            │  • REST API + auth            │  HTTP  │ laptop, Docker)    │
└──────────────┘            │  • LangGraph agent runtime    │        │ Silver + Gold +    │
                            │  • Job worker + scheduler     │        │ rules, configs,    │
                            │    (APScheduler, persistent)  │        │ lineage, audit log │
                            │  • Connector, rule, pipeline, │        └────────────────────┘
                            │    observability services     │
                            └──────┬───────────────┬────────┘
                                   │               │
                       Bronze Parquet files    MLflow (models) + SQLite (sessions, job queue, local outbox)
```
- **Why a backend and not Streamlit alone:** a full application needs background jobs that keep running when the browser is closed, a scheduler, authentication, an API that other tools can call, and one place that enforces rules. Streamlit stays as the UI and could later be replaced by a React UI without changing the backend.
- **Scheduler:** APScheduler with a persistent job store runs inside the backend. Missed runs (e.g. the laptop was asleep) run when the machine wakes, and a per-project lock prevents overlapping runs.
- **Deployment (user decision): everything runs on the ArcadeDB laptop.**
  - `docker-compose.yml` starts `api`, `worker`, `ui` and `mlflow`, and attaches them to the Docker network of the user's **existing** ArcadeDB container, so the app reaches it at `http://arcadedb:2480`. There's no LAN dependency between the app and the database.
  - If ArcadeDB isn't running in Docker, the app uses `host.docker.internal:2480` instead.
  - Bronze files and MLflow data go on host-mounted volumes, so they're backed up with the laptop.
  - Team members open the UI at `http://<laptop-hostname>:8501` over the LAN or Tailscale.
- **Development:** the code is built and tested on this Mac against a throwaway local ArcadeDB container, then deployed to the laptop with `docker compose pull && up -d`. `docs/deploy.md` covers this, and `.env` differs per machine.
- **Docker resources:** Docker Desktop on that laptop needs at least 8–12 GB of RAM for training jobs; the docs state this.
- **Apple GPU caveat:** Docker on macOS can't use the Apple GPU, so Phase 4 MLX fine-tuning runs **natively on the laptop, outside Docker**. It's driven by a small host-side runner, and results are logged to the same MLflow.

## UI pages
1. **Connections:** add, edit, test or delete data-source connections; browse schemas and tables; preview rows.
2. **Datasets:** define a dataset from one or more sources (query or file + joins), its load mode (full or incremental) and its schedule.
3. **Rules:**
   - Validation rules and business rules: list, create or edit in a form, upload a file, see versions and differences between them, dry-run, activate, roll back.
   - Rule templates are downloadable as Excel and YAML.
4. **Assistant (chat):** the AI data scientist, with approval buttons, inline charts and results.
5. **Models:** leaderboard, model cards, champion vs challenger, approve a promotion, scoring history.
6. **Monitoring:** health score, data-quality and drift trends, alerts, run history, the weekly reports.
7. **Admin:** users and roles, SMTP settings, ArcadeDB connection status, retention, audit log.

## Connector framework
**Plugin interface** (`copilot/connectors/base.py`). Each connector implements:
- `test_connection()`
- `discover()`: lists schemas, tables and files, with column types
- `preview(n)`
- `read(spec)`: full read
- `read_incremental(spec, watermark)`

It also declares capabilities (incremental support, pushdown, size limits). Connectors register through an entry-point registry, so new ones can be added without changing the core code.

**Connectors by phase:**
| Phase | Connectors |
|---|---|
| Phase 1 (user priority) | **Files** (upload: CSV, Excel with sheet picker, Parquet, JSON; local or network folder); **SQL Server** (SQLAlchemy + `pyodbc` with Microsoft ODBC Driver 18 baked into the Docker image, arm64 build; supports SQL and Windows/Kerberos auth where available); **Oracle** (`python-oracledb` in *thin* mode, so no Oracle Instant Client is needed; service name or SID; wallet/TLS supported); ArcadeDB as a source. PostgreSQL, MySQL and SQLite come almost free through the same SQL connector. |
| Phase 3 | Object storage / data lake (S3, ADLS, GCS: Parquet, Delta, Iceberg, via DuckDB); Snowflake, BigQuery, Databricks SQL; REST API (paginated JSON, API-key/OAuth); SFTP; Google Sheets |

**Connections:**
- Stored as config documents in ArcadeDB.
- **Secrets are never stored in plain text:** they're encrypted with Fernet using a master key from `.env` (or kept in the macOS Keychain via `keyring`). They're never shown again in the UI, and they're redacted from logs and from Claude's context.

**Datasets** (a logical input, possibly built from several sources):
- One or more source reads, plus join definitions (keys and join type).
- Join safety checks: row count before and after, a warning on duplicate keys, and blocking of many-to-many joins unless approved.

**Load modes:**
- Full refresh.
- Incremental by watermark column (e.g. `updated_at`), stored per dataset.
- File-arrival (new files in a folder or bucket path).

**Data contract per dataset:** the expected columns, types and keys. A schema change produces a clear "contract broken" result, either blocking or warning depending on configuration.

**SQL Server / Oracle specifics:**
- Each dialect has its own type mapping: `DECIMAL`/`NUMBER`, `DATETIME2`/`TIMESTAMP WITH TIME ZONE`, `UNIQUEIDENTIFIER`, `NVARCHAR`/`NCLOB`, Oracle `DATE` (which includes a time), empty string = NULL in Oracle.
- Large reads are chunked with server-side cursors.
- Identifiers are schema-qualified, with case handled per dialect (upper-case by default in Oracle).
- Incremental watermarks are compared in the database's own time zone.

**Safety:**
- SQL connections should use a **read-only database user**.
- Queries are also checked so that only a single `SELECT`/`WITH` statement is allowed.
- Every query has a statement timeout and a row limit for previews.
- Retries with backoff and per-source rate limits.
- Upload size and type limits; Excel is read without running macros; file paths are sanitised.

## Rule management (business rules + data-validation rules)
There are two rule families, each **versioned in ArcadeDB** and editable in the Rules page:

**A. Data-validation rules (data quality):**
| Scope | Examples |
|---|---|
| Column | not null, type, range, allowed values, regex, uniqueness, max % missing |
| Row | cross-column conditions (`end_date >= start_date`) |
| Dataset | row count within expected range, freshness (newest record < N days old), no duplicate keys |
| Cross-source | referential integrity (every `customer_id` in orders exists in customers) |

- Each rule has a **severity**:
  - `block`: stop the pipeline
  - `quarantine`: move the bad rows to a Silver quarantine table, then continue
  - `fix`: e.g. set to missing, clip to a range, standardise case
  - `warn`: record only
- Each rule also has an owner, a description and a tag.

**B. Business rules (domain knowledge):**
- Target definition and positive class.
- Column dictionary (meaning, unit, role).
- Disguised-missing values (e.g. `Glucose == 0 → missing`).
- Columns not available at prediction time.
- Excluded or protected columns.
- Domain feature formulas.
- Segment definitions.
- Business costs (false negative vs false positive), which set the metric and decision threshold.
- Model constraints (explainable only, minimum recall).

**Ways to provide rules:**
1. **Upload a file:**
   - YAML or JSON
   - An Excel/CSV template (one row per rule)
   - A data dictionary
   - A policy document (PDF/Word), from which Claude extracts **draft** rules that the user reviews and edits before they're saved
2. Create or edit in a form in the UI.
3. Say it in chat; Claude drafts the rule and the user confirms.

**Lifecycle** (applies to both families):
`Draft → Dry-run → Approved/Active → Superseded`, with roll-back to any earlier version.
- **Dry-run** runs the rule set on the latest data and shows its impact before activation: how many rows would fail, be quarantined or be changed, and how the drift baselines and features would change.
- **Static checks before activation:**
  - Syntax
  - References to columns that don't exist
  - Contradictions (e.g. `x > 10` and `x < 5`)
  - Circular feature definitions
  - Business-rule conflicts (e.g. a column that is both `excluded` and needed by a domain feature)
- **Safe expressions:** rule expressions are parsed into an allow-listed expression tree (comparisons, arithmetic, `in`, `between`, `isnull`, date functions, regex). There is no `eval` of arbitrary code.
- **Version recording:** every pipeline run records the exact rule-set versions it used.
- **No silent history rewrites:** changing a rule never changes past results. Re-processing history with new rules is an explicit "backfill" action.
- **Audit log:** who changed what and when, with a diff.

## Storage: medallion architecture
| Layer | Where | Contents |
|---|---|---|
| **Bronze** | Parquet files: `data/bronze/<project>/<dataset>/<source>/ingest_date=YYYY-MM-DD/run=<id>/part-*.parquet` + `_manifest.json` (rows, schema, SHA-256, source spec, watermark) | Raw data exactly as received. **Immutable**: never edited. |
| **Silver** (ArcadeDB) | `silver_<dataset>` (cleaned, typed, rule-validated rows with `_run_id`, `_ingest_date`, `_bronze_file`, `_quality_flags`), `silver_<dataset>_quarantine`, `ColumnProfile`, `QualityCheckResult`, `RuleViolation` | EDA and quality layer |
| **Gold** (ArcadeDB) | `gold_features_<project>`, `ModelRun`, `ModelMetric`, `DriftResult`, `TrendPoint`, `Prediction` (+ outcomes when they arrive), `Insight`, `Report`, `Alert` | Business-ready layer; read by the UI, the reports and the alerts |
| **Control** (ArcadeDB) | `Connection`, `Dataset`, `RuleSet`/`RuleVersion`, `Project`, `Schedule`, `JobRun`, `User`, `AuditLog` | App configuration and governance |

**Lineage graph:** `Connection → Dataset → BronzeFile → SilverBatch → FeatureSet → Model → PredictionBatch`, plus `RuleVersion -[APPLIED_TO]-> Run`. It answers questions like "which models were trained on data that later drifted?"

**Storage behaviour:**
- **Idempotent loads:** the same file or query result (same SHA-256) loaded twice is detected and skipped. A re-delivered week replaces its Silver/Gold rows by `_run_id` and never duplicates them.
- **Column names:** source names with spaces or special characters are mapped to safe ArcadeDB property names, with the original names kept in metadata.
- **Large datasets:** `silver.store_rows: full | sample`. For very large data, Silver stores a sample plus all profiles, and full rows stay in Bronze.
- **Retention (defaults):** Bronze and Silver rows 12 weeks; Gold, profiles and the audit log kept indefinitely.
- **Delete by key:** right-to-erasure deletion across all layers for privacy requests.
- **ArcadeDB unreachable:** results are written to a local SQLite **outbox** and synced automatically when the connection returns, so a weekly run never loses results.
- **Backups:** the setup doc includes ArcadeDB backups on the server laptop (`BACKUP DATABASE` on a schedule).
- **Other stores:** MLflow keeps its own SQLite backend and the model files, with key metadata mirrored to Gold. LangGraph sessions use SQLite.

**ArcadeDB connection** (`.env`): `ARCADEDB_URL=http://<host>:2480`, `ARCADEDB_USER`, `ARCADEDB_PASSWORD`, `ARCADEDB_DB_PREFIX=dsc_`, `ARCADEDB_TIMEOUT_S`.
- `docs/arcadedb-setup.md` + `docker-compose.arcadedb.yml` cover:
  - A pinned image version and a persistent volume
  - Ports 2480 and 2424, and the root password via `JAVA_OPTS`
  - **A stable hostname** (macOS `.local` name or a Tailscale name, because a DHCP IP can change)
  - Firewall settings, and LAN or VPN access only
  - A health check (`/api/v1/ready`) and backups
- All access goes through `copilot/storage/arcadedb_store.py` (HTTP/JSON API, parameterised SQL, batch inserts, idempotent schema setup, retries).

## AI agents (LangGraph + Anthropic SDK)
```
user ─► Supervisor ─► Data ─► EDA ─► Advisor ─►[APPROVE target + plan]─► Feature ─►[APPROVE features]
                                                                                    │
     Supervisor ◄─ Report ◄─ Evaluation ◄─ Modeling ◄──────────────────────────────┘
Weekly job: Ingest ─► Validate ─► Observability ─► Score ─► (retrain? ─► Modeling ─► Evaluation ─►[APPROVE promotion]) ─► Report + email
```
- **Agents:**
  - **Supervisor:** talks with the user and routes between steps.
  - **Data:** reads from datasets and connectors.
  - **EDA:** profiles the data and writes a summary.
  - **Advisor:** problem type, metric, threshold and plan, using the business rules.
  - **Feature:** builds and validates features.
  - **Modeling:** split, training and tuning.
  - **Evaluation:** metrics, plots and verdict.
  - **Observability:** quality, drift, trends and performance checks.
  - **Rules Assistant:** drafts rules from documents or chat.
- **Claude integration:**
  - Official `anthropic` SDK, model `claude-opus-5-5` by default (`DSC_MODEL` can switch to `claude-sonnet-5-5`).
  - Adaptive thinking with effort set explicitly, and streaming.
  - `strict: true` tools, with inputs validated before running.
  - Refusal fallback (`fallbacks: "default"`) and typed error handling with retries.
- **Prompt-injection guard:** column names, cell values and uploaded documents are treated as **data**. They're wrapped and labelled as untrusted, and Claude can act only through the allow-listed tools.
- **Context limits:** wide data (hundreds of columns) is summarised and paged instead of pasted whole. Claude never receives full datasets, and sample rows can be turned off per project (`share_sample_rows: false`).
- **Cost and safety caps:**
  - Step limit and per-run token budget, with cost shown in the UI.
  - Each AI decision is recorded with its inputs, for audit and reproducibility.
- **Swappable backend:** `copilot/llm.py` is a backend interface, so Phase 4 can plug in local models.

## Modeling pipeline
- **Problem types:** binary and multiclass classification, and regression. Time-series forecasting is **out of scope** for v1; the assistant says so and suggests a time-aware regression instead.
- **Split strategy:**
  - Stratified split by default.
  - **Time-based** when a time column exists.
  - **Group-aware** (GroupKFold) when one entity, such as a customer, appears in several rows or weeks, so the same entity is never in both train and test.
  - Duplicate rows that span train and test are detected.
- **Preprocessing:** a single sklearn `Pipeline` (impute, encode with `handle_unknown`, scale, plus the business-rule features), saved with the model, so **training and scoring transform data identically**.
- **Models:** Logistic/Linear, Random Forest, HistGradientBoosting, XGBoost, LightGBM. The list is filtered by the constraints (e.g. explainable only), and Optuna tuning has a time budget.
- **Metrics:**
  - Classification: accuracy, precision, recall, F1 (macro and weighted for multiclass), ROC-AUC, PR-AUC, confusion-matrix heatmap, ROC/PR curves, calibration, threshold chosen from the business costs.
  - Regression: RMSE, MAE, R², MAPE (when safe), residual plots.
  - Both: SHAP explanations, and the baseline (original features) vs engineered features comparison.
- **Feature-engineering safeguards:**
  - Features are built only from allow-listed recipes.
  - A feature is kept only on a cross-validated gain above the noise level, using training data only.
  - Leakage checks: a single-feature near-perfect score, use of an unavailable or excluded column, wrong split type.
  - Claude asks about unclear columns instead of guessing, and the user approves the feature list.
- **Fairness check:** when protected columns exist, they're excluded from training, but performance is still reported **per protected group**, and features that act as proxies for them (high correlation) are flagged.
- **Model registry:** MLflow with `champion` and `challenger` aliases. A model card records the data, rules, metrics, limitations and the approver.
- **Batch scoring:** each weekly run scores the new data with the champion model and writes the results to Gold `Prediction`. Missing or new columns and unseen categories are handled or reported, never crashed on.

## Data observability (weekly)
- **Quality:**
  - Contract and schema changes; volume anomalies; freshness.
  - Missing-value and duplicate rates; rule-violation and quarantine counts, as trends over time.
- **Drift:**
  - Per column: PSI (bins taken from reference-data quantiles), plus KS or chi-square tests.
  - **Severity is based on effect size, not p-values alone.** With large weekly volumes, p-values flag even trivial changes.
  - False-discovery-rate correction across many columns.
  - New and disappearing categories.
  - Whole-dataset drift through a classifier test.
  - Per-segment drift.
- **Target, concept and prediction drift:** target rate, SHAP importance shift, prediction-score distribution.
- **Performance:**
  - Label timing configured per project: `with_data | delayed (delay_weeks) | none`.
  - With delayed labels, past weeks' predictions are scored when their outcomes land.
  - Rolling-origin backtesting.
  - Champion/challenger with **human approval** before a promotion; auto-promote is optional.
- **Trends:** rolling averages, MAD anomaly flags, changepoint detection, and a **seasonality-aware baseline** (same week last year when available, holiday calendar).
  - This avoids alerts for expected seasonal changes.
- **Small-sample handling:** weeks with too few rows are marked "insufficient data", never alerted on.
- **Baselines:** each run is compared with the training data, the previous run and the rolling 4 runs.
- **Alerts:**
  - Email via SMTP (credentials in `.env`/Admin page); report-only when no email is configured.
  - Duplicates are suppressed, there's a cool-down period, and a daily digest is optional, to avoid alert fatigue.
  - Alerts are also sent when a **job fails or misses its schedule**.
- **Reports:**
  - HTML report: health score, a Claude-written summary (what changed, likely causes, actions), trend charts, drill-down by column and segment, and the model decision.
  - Also shown in the Monitoring page.

## Edge cases handled (checklist)
**Data:**
- Empty or tiny datasets (minimum-row checks); more columns than rows.
- Constant or all-null columns; mixed-type columns (`"N/A"` in numeric).
- Duplicate or odd column names; encodings and delimiters (auto-detected); Excel with several sheets or a header row that isn't the first row.
- Dates: several formats or time zones (normalised to UTC with a configurable week boundary).
- Free-text columns (flagged and excluded in v1); very high-cardinality categories (grouped or target-encoded with CV).

**Target:**
- Missing target values (dropped and reported).
- Only one class (blocked, with an explanation).
- Very rare positive class (fewer than about 30 positives gives a warning and switches to repeated CV and PR-AUC focus).
- Multiclass with tiny classes; skewed regression target (log option).
- Target leakage through ID ordering.

**Weekly runs:**
- Late or partial weeks.
- Re-delivered files (idempotency).
- Backfills.
- Schema evolution (data-contract check).
- Missed runs while the laptop sleeps (catch-up).
- Overlapping runs (lock).
- ArcadeDB offline (outbox).
- Claude offline (deterministic pipeline continues).
- SMTP failure (retry and log).

**Rules:**
- Contradictions; references to missing columns; a rule change in the middle of the history (version recorded, backfill is explicit).
- Unsafe expressions (rejected).
- Claude-extracted rules that may be wrong (draft + human approval).

**Security:**
- Secrets encrypted and redacted.
- SQL read-only checks.
- Upload limits.
- Prompt-injection guard.
- Roles: admin, editor, viewer.
- Audit log.
- PII masking.

## Folder layout (`AIML/datasci-copilot/`)
```
backend/
  api/                 # FastAPI routers: auth, connections, datasets, rules, projects, chat, models, monitoring, admin
  worker/              # job runner, APScheduler, locks, outbox sync
copilot/
  llm.py, graph.py, state.py
  agents/              # supervisor, data, eda, advisor, feature, modeling, evaluation, observability, rules_assistant
  connectors/          # base.py, registry.py, files.py, sql.py, arcadedb.py (+ Phase 3: lake, warehouses, rest, sftp, sheets)
  datasets/            # dataset specs, joins, contracts, incremental watermarks
  rules/               # schema (pydantic), expression parser, validator, engine, dry-run, versioning, importers (yaml/xlsx/csv/doc)
  pipeline/            # ingest → bronze → silver → gold steps
  ml/                  # profiling, features, splits, training, evaluation, fairness, scoring, registry
  observability/       # quality, drift, trends, performance, alerts, report
  storage/             # bronze.py, arcadedb_store.py, outbox.py, retention.py, secrets.py
  tracking.py, report.py
ui/                    # Streamlit pages (Connections, Datasets, Rules, Assistant, Models, Monitoring, Admin)
templates/             # rules_template.xlsx, rules_example.yaml, dataset_example.yaml
projects/bank_loan/    # example project: dataset, validation + business rules
docker-compose.yml, docker-compose.arcadedb.yml, docs/ (setup, arcadedb, connectors, rules guide)
tests/  .env.example  requirements.txt
```
- **Environment:** a project venv from Anaconda Python 3.11; the base conda env is untouched.
- **Libraries (pinned):** fastapi, uvicorn, apscheduler, streamlit, langgraph, anthropic, pandas, pyarrow, pydantic, sqlalchemy (+ pyodbc, oracledb, psycopg, pymysql), passlib/argon2 (passwords), duckdb, requests, openpyxl, pypdf, python-docx, scikit-learn, xgboost, lightgbm, optuna, shap, mlflow, plotly, scipy, cryptography, keyring, python-dotenv, pyyaml, pytest.

## Phases
1. **Platform foundation:**
   - Backend and UI skeleton; **team auth from day one**: user accounts with argon2-hashed passwords, roles (**admin**: users/connections/settings; **editor**: rules, datasets, run the assistant, approve; **viewer**: reports and monitoring only), session tokens, audit log of every change and approval. SSO comes later.
   - Docker Compose deployment onto the ArcadeDB laptop
   - ArcadeDB store, Bronze/Silver layers, secrets
   - Connector framework: **files, SQL Server, Oracle** (+ Postgres/MySQL/SQLite via the same SQL connector), ArcadeDB; datasets with joins and contracts
   - **Rule management** for validation and business rules (upload, edit, versioning, dry-run, activate, roll back) and the rule engine
   - Basic EDA and quality profile
2. **AI data scientist:**
   - Agents and approval gates
   - Feature engineering, training and evaluation; Gold layer
   - MLflow registry, model cards, batch scoring
   - HTML reports
3. **Observability and automation:**
   - Scheduler, incremental loads, the full drift, trend and performance suite
   - Champion/challenger, delayed labels
   - Email alerts, Monitoring page, outbox, retention
   - Lake, warehouse, REST, SFTP and Sheets connectors
4. **Open LLMs:**
   - Local model backend (Ollama / mlx-lm) compared against Claude
   - QLoRA fine-tuning with Apple MLX on 4-bit 7–8B models, tracked in MLflow; the use case is decided at the start of the phase

## Verification
- **Unit tests:**
  - Each connector: temporary files; **SQL Server** (`mcr.microsoft.com/mssql/server` container) and **Oracle** (`gvenzl/oracle-free` container) integration tests for type mapping, chunked reads, incremental watermarks and read-only enforcement; SQLite; join safety checks
  - Auth and roles: a viewer can't edit rules; an editor can't manage users; every approval is in the audit log
  - Rule parser (valid, invalid and unsafe expressions), contradiction detection, severity actions, dry-run counts, versioning and roll-back
  - Profiling edge cases (the data checklist above); split strategies (time-based and group-aware)
  - Leakage detection; drift on synthetic data with known drift, including a no-drift control, large-N robustness and FDR
  - Graph routing and interrupts with Claude mocked
- **Integration tests:** against an ArcadeDB Docker container, skipped when `ARCADEDB_URL` is unset:
  - Bronze → Silver → Gold round trip; idempotent reload
  - Lineage edges; outbox sync after a simulated outage; retention
- **End-to-end runs:**
  - **Bank loan:** a two-source join (customers + a synthetic accounts table), rules uploaded from the Excel template, ID dropped, ZIP excluded, negative Experience quarantined, recall-focused threshold, baseline vs engineered comparison.
  - **Diabetes:** disguised zeros caught through the rules.
  - **California Housing:** the regression path.
- **Weekly simulation:** the bank data split into 8 weeks, with drift injected from week 5, a re-delivered week 3, a schema change in week 6, and ArcadeDB offline in week 7. Expected results:
  - Changepoint at week 5 and alert email (local test SMTP server)
  - No duplicates from the re-delivery; a contract alert in week 6; the outbox synced after week 7
  - Champion/challenger awaiting approval
- **Deployment check:** `docker compose up` on the ArcadeDB laptop; the app reaches the existing ArcadeDB container over the Docker network; a second team member logs in from another machine on the LAN.
- **Manual walk-through:** every UI page, using the setup docs on the ArcadeDB laptop.
