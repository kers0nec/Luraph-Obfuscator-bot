FROM python:3.11-slim

# Install Lua
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    lua5.3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
