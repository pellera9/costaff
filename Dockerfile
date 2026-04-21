FROM python:3.10-slim

WORKDIR /app

# Install system dependencies and fonts
RUN apt-get update && apt-get install -y \
    build-essential \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

CMD ["python", "mcp_servers/server.py"]