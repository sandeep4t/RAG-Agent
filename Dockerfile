FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY samples ./samples
COPY scripts ./scripts
RUN pip install --no-cache-dir .
RUN useradd --create-home appuser && mkdir -p /app/data/posts /app/data/audit && chown -R appuser:appuser /app/data
USER appuser
EXPOSE 8000
CMD ["uvicorn", "secure_rag.api:app", "--host", "0.0.0.0", "--port", "8000"]
