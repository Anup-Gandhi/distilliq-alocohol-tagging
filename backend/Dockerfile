FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tagger ./tagger
COPY api ./api
COPY prompts ./prompts
COPY alembic.ini ./
COPY migrations ./migrations
# secrets/ (LLM key files), prompts/ and data/ (uploads, logs) are volumes, see docker-compose.yml
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
