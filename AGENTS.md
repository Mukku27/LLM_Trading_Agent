# AGENTS.md

## Cursor Cloud specific instructions

This is a single Python product: **LLM_Trading_Agent**, an AI-powered crypto trading
analysis bot. There is no database or backend server. Persistence is local JSON files
under `trading_data/`. See `README.md` and `TECHNICAL_REPORT.md` for product details.

### Python / dependencies
- Requires **Python 3.13+** (enforced at runtime in `main.py`; the base image only ships
  3.12). The update script installs 3.13 via `uv` and creates a `.venv` at the repo root.
- Always run Python through the venv: `.venv/bin/python` (e.g. `.venv/bin/python main.py`).
  `uv` is installed at `$HOME/.local/bin/uv`.
- Dependencies are pinned in `requirements.txt`; the update script installs them into `.venv`.

### Required config files (gitignored — must exist before running `main.py`/`dashboard.py`)
These are ignored by git (`.gitignore`) so they may not persist. Recreate if missing:
- `config/config.ini` — copy from the committed template: `cp config/config.ini.template config/config.ini`.
- `config/model_config.ini` — **no template exists in the repo**; create it manually:
  ```ini
  [model]
  name = deepseek/deepseek-r1-0528:free
  base_url = https://openrouter.ai/api/v1
  api_key =
  ```
  Put a real LLM key in `api_key` (or in `LLM_API_KEY` in `.env`) only when live LLM
  analysis is needed; without it, `analyze_trend` degrades gracefully to `HOLD`.
- Unit/sandbox tests do NOT need these config files (they use fixtures in `tests/conftest.py`).

### Running / testing
- Tests: `.venv/bin/python -m pytest tests/unit tests/sandbox -v` (108 pass, fully mocked,
  no network/creds). Integration tests (`tests/integration/`) are skipped unless
  `EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET` are set (Binance testnet). No linter is configured.
- Bot: `.venv/bin/python main.py` — runs an infinite async loop in `dry_run` mode (default,
  no creds needed). Modes are set via `[execution] mode` in `config/config.ini`
  (`dry_run` | `paper` | `live`); `live` requires exchange credentials.
- Dashboard (optional Streamlit UI): `.venv/bin/python -m streamlit run dashboard.py --server.port 8501 --server.headless true`.
  Reads `trading_data/trade_history.json`.

### Environment gotcha (important)
- **Binance is geo-blocked in Cursor Cloud (HTTP 451).** `main.py` fetches OHLCV from
  `ccxt.binance()` and any Binance call (incl. `fetch_time`) returns 451, so the bot cannot
  complete a full market-analysis loop here — it starts up correctly and falls back to local
  time, but OHLCV fetch fails. This is a network restriction, not a setup bug. The Fear & Greed
  API (`api.alternative.me`) is reachable. To exercise the core execution pipeline without the
  exchange, drive `DryRunEngine` directly (see the factory `execution/factory.py`), or use the
  Streamlit dashboard against the sample `trading_data/trade_history.json`.
