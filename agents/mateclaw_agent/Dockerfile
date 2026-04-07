FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY agents/mateclaw_agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app/agents/mateclaw_agent
COPY agents/mateclaw_agent/ .

WORKDIR /app/agents

EXPOSE 8080

CMD ["adk", "web", "--host", "0.0.0.0", "--port", "8080", "."]
