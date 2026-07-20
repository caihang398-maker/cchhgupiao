# Contributing

## Local setup

On Windows with Python 3.12:

```powershell
git clone https://github.com/caihang398-maker/cchhgupiao.git
cd cchhgupiao
powershell -ExecutionPolicy Bypass -File scripts\setup_local.ps1 -Start
```

The local application opens at `http://127.0.0.1:8501`.

## Before submitting a change

```powershell
.\.venv\Scripts\python.exe -m compileall -q app.py dashboard.py stock_quant pages scripts
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Keep credentials, local databases, exports, logs and market-data caches out of
commits. Add focused tests for changes to recommendation, position, backtest,
authentication, payment or deployment behavior.

## Financial and data-source boundaries

Do not describe an output as guaranteed profit, guaranteed recovery or a certain
price increase. New data sources must document their license, update frequency
and failure behavior. Broker execution integrations require explicit risk limits
and must remain disabled by default.
