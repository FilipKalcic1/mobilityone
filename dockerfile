FROM python:3.11-slim

RUN useradd -m -u 1000 appuser

WORKDIR /app


RUN apt-get update && apt-get install -y curl redis-tools && rm -rf /var/lib/apt/lists/*


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .


RUN chown -R appuser:appuser /app


USER appuser


CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]