# Single-container deploy. Works unmodified on Azure Container Apps or
# App Service for Containers — mount a persistent volume at /app/data
# (Azure Files) so the SQLite cache/audit-log survives restarts, and put
# Easy Auth / Container Apps Authentication in front for Entra ID login.

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DB_PATH=/app/data/app.db \
    TOKENS_MODE=azure \
    TOKENS_PATH=/app/tokens.azure.json \
    ENVIRONMENT=production

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
