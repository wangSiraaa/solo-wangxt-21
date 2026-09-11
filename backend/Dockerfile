FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

# 容器首次启动时对 PostgreSQL 建表；播种可手动执行：
#   docker compose exec backend python scripts/seed_demo.py
CMD ["sh", "-c", "python -c \"from app.db import Base, engine; Base.metadata.create_all(engine)\" && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
