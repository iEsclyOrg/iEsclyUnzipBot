FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies: ffmpeg, unrar, p7zip, and libmagic.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    unrar \
    p7zip-full \
    gcc \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create temp directories used by the bot.
RUN mkdir -p /app/temp/archives /app/temp/extracted

# Heroku sets PORT automatically; Railway sets it too.
# The health server binds to it, and the bot worker runs forever.
CMD ["python", "main.py"]
