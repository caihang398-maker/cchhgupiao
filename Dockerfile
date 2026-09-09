FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    APP_ENV=production \
    PORT=80 \
    MINIAPP_API_HOST=0.0.0.0 \
    MINIAPP_TRUST_PROXY=true \
    MINIAPP_TRUST_CLOUDBASE_IDENTITY=true \
    MINIAPP_CLOUDBASE_PERSONAL_MODE=true \
    MINIAPP_CLOUD_SQLITE_SYNC=true \
    MINIAPP_REQUIRE_PERSISTENT_STORAGE=false \
    STOCK_QUANT_DATA_DIR=/tmp/stock-quant-data \
    STOCK_QUANT_REPORTS_DIR=/tmp/stock-quant-data/reports \
    STOCK_QUANT_SQLITE_JOURNAL_MODE=delete \
    STOCK_QUANT_LOG_STDOUT=true

WORKDIR /app

COPY requirements-miniapp.txt ./requirements-miniapp.txt
RUN pip install --no-cache-dir -r requirements-miniapp.txt

COPY stock_quant ./stock_quant
COPY deploy/cloudbase ./deploy/cloudbase

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:%s/health' % os.getenv('PORT', '80'), timeout=3).read()" || exit 1

CMD ["python", "-m", "stock_quant.miniapp_api"]
