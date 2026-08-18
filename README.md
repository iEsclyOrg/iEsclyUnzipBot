# UnzipBot

Telegram bot to extract **zip, rar, 7z, tar, gz, bz2, xz** archives and send the files back.

## Features

- Accepts any archive document in private chat.
- Asks what you want to do.
- Asks if the archive is password protected.
- Lists all files/folders with **pagination**.
- Lets you extract **All**, **Selected**, or the **Whole archive**.
- Validates extracted files (CRC, magic bytes, ffmpeg for media).
- Splits files >2 GB with **ffmpeg** (media) or binary chunks (documents).
- Uploads via **kurigram** MTProto for 2 GB support.
- Stores sessions in **MongoDB**.

## Setup

1. Copy `.env.example` to `.env` and fill in your credentials.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Make sure **MongoDB** is running.
4. Make sure **ffmpeg** and **ffprobe** are installed and in PATH.
5. For RAR support, install `unrar` / WinRAR tools.
6. Run the bot:
   ```bash
   python main.py
   ```

## .env

```env
BOT_TOKEN=your_bot_token
API_ID=your_api_id
API_HASH=your_api_hash
MONGO_URI=mongodb://localhost:27017
DB_NAME=unzip_bot
```

## Deploy on Railway

1. Push this repo to GitHub.
2. Create a new Railway project from the GitHub repo.
3. Add these environment variables in Railway:
   - `BOT_TOKEN`
   - `API_ID`
   - `API_HASH`
   - `MONGO_URI` (use Railway MongoDB add-on or MongoDB Atlas)
   - `DB_NAME`
   - `PORT` (Railway sets this automatically)
4. Railway will build using the `Dockerfile` and start the worker.

## Deploy on Heroku

### One-Click Deploy

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/yourusername/unzip-bot)

> Replace `https://github.com/yourusername/unzip-bot` with your actual repo URL after pushing.

### Manual Deploy via Heroku CLI

1. Push this repo to GitHub.
2. Create a new Heroku app:
   ```bash
   heroku create your-app-name
   ```
3. Set the stack to container:
   ```bash
   heroku stack:set container -a your-app-name
   ```
4. Add environment variables:
   ```bash
   heroku config:set BOT_TOKEN=your_bot_token
   heroku config:set API_ID=your_api_id
   heroku config:set API_HASH=your_api_hash
   heroku config:set MONGO_URI=your_mongo_uri
   heroku config:set DB_NAME=unzip_bot
   ```
5. Deploy:
   ```bash
   git push heroku main
   ```
6. Scale the worker dyno:
   ```bash
   heroku ps:scale worker=1 -a your-app-name
   ```

### Important Heroku Notes

- Heroku uses **container stack** because we need `ffmpeg`, `unrar`, and `p7zip`. Pure Python buildpack won't work.
- The bot runs as a **worker** dyno, but the `health_server.py` keeps a lightweight `/health` endpoint alive on `PORT` so Heroku doesn't kill the container.
- Free dynos sleep after inactivity, but Telegram bots should run 24/7. Use at least a **Basic** ($7/month) worker dyno for production.

## Notes

- `kurigram` (pyrogram fork) is used for bot operations and file uploading via MTProto.
- Bot token is used for all operations — no separate user session needed.
- MTProto mode supports up to 2 GB file uploads natively.
