# Project Bot — version 1.1 (bias LONG/SHORT)

Updated: 2025-11-12T15:46:13.774058

## What's new
- Introduced pair-level bias key in each `<SYMBOL>.json`: `"bias": "LONG"|"SHORT"`.
- Command `/coin <symbol> long|short` sets this bias (default for new pairs is LONG).
- `/market force` now auto-selects frame by bias: LONG → `12+6`, SHORT → `6+4`.
- `/now` collects metrics only for the frames required by the bias and updates raw + `market_mode` accordingly.
- Scheduler reads bias per symbol and runs the appropriate pipeline (no hard-coded frame arguments).
- Backward compatible: both `12+6` and `6+4` logic remain in `market_calculation.py`.

## Files touched
- `main.py` — command handlers updated/added.
- `metric_scheduler.py` — per-symbol bias routing.
- `data.py` — helpers to read/write `bias` in `<SYMBOL>.json`.
- `market_calculation.py` — added helper `run_market_pipeline_by_bias(...)` and optional `frame` param for raw calc.
- `metric_scheduler_config.json` — updated example (no 4h/2h hard-coding).
- `migrations/add_bias_default_long.py` — new one-off migration.



**Compat note (v1.2):** switched to `collector.collect_all_metrics(symbol)` to match existing API. The scheduler and commands now trigger a full metrics collection; market calculation still uses the bias to pick 12+6 or 6+4.


## HTTP API (since 1.3)

ASGI app: `main:app`

- `GET /healthz` → `{ ok: true, version: "1.3" }`
- `POST /coin` body: `{ "symbol": "BTCUSDT", "mode": "long|short" }`
- `POST /market/force` body: `{ "symbol": "BTCUSDT" }`
- `POST /now` body: `{ "symbol": "BTCUSDT" }`

`DEFAULT_SYMBOL` env var can be used if `symbol` omitted.
