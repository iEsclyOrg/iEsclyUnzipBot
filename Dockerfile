FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# System dependencies. `unrar` is gone from Debian (licensing) and `p7zip-full`
# in trixie no longer ships the unRAR codec, so RAR is handled by bsdtar from
# libarchive-tools — a backend rarfile recognises (see RAR_TOOL_CANDIDATES).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    p7zip-full \
    libarchive-tools \
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
