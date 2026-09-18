FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home appuser
COPY --chown=appuser:appuser . .
USER appuser
EXPOSE 8000
CMD ["sh", "-ec", "if [ \"${APP_BOOTSTRAP_ON_START:-0}\" = \"1\" ]; then alembic upgrade head; flask --app wsgi seed-demo-if-empty; fi; exec python server.py"]
