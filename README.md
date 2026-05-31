---
title: Telegram Reel Bot
sdk: docker
app_port: 7860
---

# Telegram to Instagram Reel Bot

Cloud-deployable Telegram bot that turns a Telegram prompt, YouTube URL, direct video URL, or uploaded video into a vertical Instagram Reel and publishes it through the official Instagram Graph API.

The production path is webhook-only. You do not need to run a bot process on your local machine.

## What It Does

Telegram message received
-> detect input type
-> download or generate source video
-> extract/transcribe audio with faster-whisper
-> pick a 15-45 second segment
-> render a 1080x1920 MP4 with FFmpeg, subtitles, and normalized audio
-> generate caption and hashtags
-> upload MP4 to Cloudflare R2 or Supabase Storage
-> publish the public MP4 URL as an Instagram Reel
-> send Telegram success/failure message

If `OPENAI_API_KEY` is not set, the bot still works. It uses transcript text, template captions, and rule-based hashtags.

## Project Structure

```text
telegram_reel_bot/
  app.py
  config.py
  requirements.txt
  Dockerfile
  docker-compose.yml
  render.yaml
  fly.toml
  .env.example
  README.md
  services/
    telegram_service.py
    downloader.py
    video_editor.py
    transcriber.py
    scene_selector.py
    caption_generator.py
    instagram_uploader.py
    storage.py
    database.py
  data/
  downloads/
  outputs/
```

## Important Limits

- Instagram publishing uses the official Instagram Graph API only.
- Your Instagram account must be Professional: Creator or Business.
- The Instagram account must be linked to a Facebook Page.
- The app needs permissions that include `instagram_basic`, `instagram_content_publish`, and Page access needed to discover the linked IG account.
- Instagram must be able to fetch the final MP4 from a public HTTPS URL. That is why the bot uploads to Cloudflare R2 or Supabase Storage before publishing.
- Free hosts can sleep, restart, or have limited CPU/RAM. Use `WHISPER_MODEL=tiny` first.

## Environment Variables

Copy `.env.example` into your cloud provider environment settings and fill the values there.

Your Telegram token has already been placed in the local `.env` file for reference, but `.env` is ignored by Git and should not be uploaded. In Render, Railway, Fly.io, or Cloud Run, paste the same value into the provider's environment variable UI as `TELEGRAM_BOT_TOKEN`.

Required:

```env
TELEGRAM_BOT_TOKEN=
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_USER_ID=
STORAGE_PROVIDER=cloudflare_r2
PUBLIC_BASE_URL=https://your-deployed-service.example.com
```

Cloudflare R2:

```env
CLOUDFLARE_R2_ACCESS_KEY_ID=
CLOUDFLARE_R2_SECRET_ACCESS_KEY=
CLOUDFLARE_R2_BUCKET=
CLOUDFLARE_R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
CLOUDFLARE_R2_PUBLIC_URL=https://<public-bucket-domain-or-r2-dev-url>
```

Supabase Storage:

```env
STORAGE_PROVIDER=supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_BUCKET=
```

Optional:

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
TELEGRAM_WEBHOOK_SECRET=
GRAPH_API_VERSION=v24.0
WHISPER_MODEL=tiny
ENABLE_TRANSCRIPTION=true
LOW_MEMORY_MODE=false
MIN_REEL_SECONDS=15
MAX_REEL_SECONDS=45
PROMPT_REEL_SECONDS=25
MAX_DOWNLOAD_MB=500
MAX_CONCURRENT_JOBS=1
YOUTUBE_COOKIES_FILE=
YOUTUBE_COOKIES_CONTENT=
```

Webhook URL:

```text
PUBLIC_BASE_URL + /telegram-webhook
```

The app sets the Telegram webhook automatically on startup when `TELEGRAM_BOT_TOKEN` and `PUBLIC_BASE_URL` are present.

## 1. Create The Telegram Bot

1. Open Telegram and message `@BotFather`.
2. Run `/newbot`.
3. Copy the bot token into `TELEGRAM_BOT_TOKEN`.
4. No polling setup is needed. This service uses webhooks.

Official reference: [Telegram Bot API setWebhook](https://core.telegram.org/bots/api#setwebhook).

## 2. Set Up Instagram Graph API

1. Convert your Instagram account to a Creator or Business account.
2. Link it to a Facebook Page.
3. Create a Meta developer app.
4. Add Instagram Graph API / Instagram product access as required by Meta's current dashboard.
5. Generate a User access token with publishing permissions.
6. Find your Page:

```http
GET https://graph.facebook.com/v24.0/me/accounts?access_token=YOUR_TOKEN
```

7. Find the linked Instagram professional account:

```http
GET https://graph.facebook.com/v24.0/PAGE_ID?fields=instagram_business_account&access_token=YOUR_TOKEN
```

8. Put the returned Instagram account id into `INSTAGRAM_USER_ID`.
9. Put the access token into `INSTAGRAM_ACCESS_TOKEN`.

Long-lived token notes:

- Short-lived tokens expire quickly. Exchange them for long-lived tokens from Meta's token tools or OAuth flow.
- Check expiration in Meta's Access Token Debugger.
- Refresh or replace the token before it expires. This starter does not refresh tokens automatically because that would require app id/app secret storage and an OAuth flow.
- For production with other users, you will need Meta App Review for the publishing permission.

Official references:

- [Instagram Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
- [IG User Media endpoint](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media)

### Instagram Token Checklist

You need these two values:

```env
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_USER_ID=
```

Get them like this:

1. Go to [Meta for Developers](https://developers.facebook.com/).
2. Create an app or open your existing app.
3. Add Instagram Graph API / Instagram access in the app dashboard.
4. Make sure your Instagram account is Creator or Business.
5. Make sure it is linked to a Facebook Page.
6. Generate a User access token with publishing permissions.
7. Use the token to list your Facebook Pages:

```http
GET https://graph.facebook.com/v24.0/me/accounts?access_token=YOUR_TOKEN
```

8. Copy the Page id.
9. Use that Page id to get the linked Instagram account:

```http
GET https://graph.facebook.com/v24.0/PAGE_ID?fields=instagram_business_account&access_token=YOUR_TOKEN
```

10. Use the returned Instagram account id as `INSTAGRAM_USER_ID`.
11. Use the token as `INSTAGRAM_ACCESS_TOKEN`.

For first deployment, test with your own account while the Meta app is in development mode. For publishing to accounts outside your app roles, Meta App Review is required.

## 3. Set Up Public Video Storage

### Option A: Cloudflare R2

1. Create an R2 bucket.
2. Create an R2 API token with object read/write access.
3. Copy the S3 endpoint into `CLOUDFLARE_R2_ENDPOINT`.
4. Enable public access using either:
   - a custom domain, recommended for production
   - the `r2.dev` public development URL for testing
5. Put that public base URL into `CLOUDFLARE_R2_PUBLIC_URL`.

Official reference: [Cloudflare R2 public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/).

### Cloudflare R2 Token Checklist

Use this if `STORAGE_PROVIDER=cloudflare_r2`.

You need:

```env
CLOUDFLARE_R2_ACCESS_KEY_ID=
CLOUDFLARE_R2_SECRET_ACCESS_KEY=
CLOUDFLARE_R2_BUCKET=
CLOUDFLARE_R2_ENDPOINT=
CLOUDFLARE_R2_PUBLIC_URL=
```

Steps:

1. Open Cloudflare Dashboard.
2. Go to **R2 Object Storage**.
3. Create a bucket, for example `telegram-reels`.
4. Go to **Manage R2 API Tokens**.
5. Create an API token with Object Read & Write access for that bucket.
6. Copy the Access Key ID into `CLOUDFLARE_R2_ACCESS_KEY_ID`.
7. Copy the Secret Access Key into `CLOUDFLARE_R2_SECRET_ACCESS_KEY`.
8. Set `CLOUDFLARE_R2_BUCKET` to the bucket name.
9. Set `CLOUDFLARE_R2_ENDPOINT` to:

```text
https://<account-id>.r2.cloudflarestorage.com
```

10. Enable public access for the bucket using a custom domain or `r2.dev`.
11. Set `CLOUDFLARE_R2_PUBLIC_URL` to that public bucket URL.
12. Confirm the URL is public by opening an uploaded file in an incognito browser.

### Option B: Supabase Storage

1. Create a Supabase project.
2. Create a Storage bucket.
3. Make the bucket public.
4. Copy your project URL, service role key, and bucket name into the Supabase environment variables.

Official reference: [Supabase Python storage upload](https://supabase.com/docs/reference/python/storage-from-upload).

### Supabase Token Checklist

Use this if `STORAGE_PROVIDER=supabase`.

You need:

```env
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_BUCKET=
```

Steps:

1. Open your Supabase project.
2. Go to **Project Settings -> API**.
3. Copy the Project URL into `SUPABASE_URL`.
4. Copy the service role key into `SUPABASE_SERVICE_ROLE_KEY`.
5. Go to **Storage**.
6. Create a bucket, for example `reels`.
7. Make the bucket public.
8. Set `SUPABASE_BUCKET` to the bucket name.

## 4. Push To GitHub Without Running Locally

Recommended beginner path:

1. Create a new GitHub repository.
2. Upload the contents of the `telegram_reel_bot` folder as the repository root.
3. Do not upload a real `.env` file.
4. Add secrets/environment variables in your cloud provider dashboard.

If you upload the whole parent repository instead, set your cloud provider's root directory to `telegram_reel_bot`.

## 5. Deploy On Render

Render is the simplest path for this project.

1. In Render, choose **New -> Blueprint** if your repo root contains `render.yaml`, or **New -> Web Service** if you prefer manual setup.
2. Connect the GitHub repository.
3. If the project is in a subfolder, set the root directory to `telegram_reel_bot`.
4. Use Docker runtime.
5. Add all required environment variables.
6. Deploy once.
7. Copy the Render URL, for example `https://telegram-reel-bot.onrender.com`.
8. Set `PUBLIC_BASE_URL` to that URL.
9. Redeploy so the app startup can set the Telegram webhook.

Render notes:

- The service must bind to `$PORT`; the Docker command already does this.
- Free instances may sleep, so the first Telegram request after sleep can be slow.
- Render Free has 512 MB RAM. Keep these values on the free plan:

```env
LOW_MEMORY_MODE=true
ENABLE_TRANSCRIPTION=false
MAX_DOWNLOAD_MB=150
WHISPER_MODEL=tiny
MAX_CONCURRENT_JOBS=1
```

With these settings, the bot avoids local Whisper transcription and uses a lighter FFmpeg vertical render. If you upgrade to 1-2 GB RAM, you can set `ENABLE_TRANSCRIPTION=true` and `LOW_MEMORY_MODE=false` for transcript-based segment selection and blurred backgrounds.

Official references:

- [Render Web Services](https://render.com/docs/web-services)
- [Render Docker](https://render.com/docs/docker)
- [Render Blueprint spec](https://render.com/docs/blueprint-spec)

## YouTube Cloud Blocking

Some YouTube URLs fail on Render/Railway/Fly because YouTube challenges datacenter IP addresses with "Sign in to confirm you're not a bot." That is outside Telegram and Instagram.

Best options:

1. Upload the source video directly to Telegram.
2. Send a direct `.mp4` URL instead of a YouTube watch URL.
3. Upload the source MP4 to R2/Supabase yourself and send that public MP4 URL.
4. Use a paid host/proxy setup with stable YouTube access.
5. Advanced: provide a Netscape-format YouTube cookies file to yt-dlp.

Cookie support is optional and fragile. Only use cookies from an account you control, and understand they are sensitive credentials.

Supported cookie env vars:

```env
YOUTUBE_COOKIES_FILE=/etc/secrets/youtube_cookies.txt
```

or:

```env
YOUTUBE_COOKIES_CONTENT=# Netscape HTTP Cookie File
...
```

`YOUTUBE_COOKIES_FILE` is preferred if your cloud host supports secret files. The cookies file must be in Netscape format. See the official [yt-dlp FAQ on cookies](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp).

## 6. Deploy On Railway

1. Create a new Railway project from GitHub.
2. Select this repository.
3. If needed, set the service root directory to `telegram_reel_bot`.
4. Railway should detect the Dockerfile.
5. Add environment variables in Railway Variables.
6. Generate a public domain in Railway Networking.
7. Set `PUBLIC_BASE_URL` to the Railway domain.
8. Redeploy.

Official references:

- [Railway CLI deploying](https://docs.railway.com/cli/deploying)
- [Railway variables](https://docs.railway.com/reference/variables)

## 7. Deploy On Fly.io

The repo includes `fly.toml`. To avoid local setup, deploy from GitHub Actions.

1. Create a Fly.io account.
2. Create a Fly API token in the Fly dashboard.
3. In GitHub, add repository secret `FLY_API_TOKEN`.
4. Add app secrets in Fly dashboard or with Fly's web console/CLI.
5. Create `.github/workflows/fly-deploy.yml` in GitHub:

```yaml
name: Deploy to Fly

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: telegram_reel_bot
    steps:
      - uses: actions/checkout@v4
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

6. After the first deploy, set `PUBLIC_BASE_URL` to:

```text
https://telegram-reel-bot.fly.dev
```

7. Redeploy.

Official references:

- [Fly deploy with Dockerfile](https://fly.io/docs/languages-and-frameworks/dockerfile/)
- [Fly deploy app](https://fly.io/docs/apps/deploy/)

## 8. Deploy On Google Cloud Run

You can do this from Google Cloud Shell, which runs in the browser, not on your local machine.

1. Open Google Cloud Console.
2. Activate Cloud Shell.
3. Clone your GitHub repo in Cloud Shell.
4. Change into the project folder:

```bash
cd telegram_reel_bot
```

5. Deploy from source. Cloud Run will use the Dockerfile:

```bash
gcloud run deploy telegram-reel-bot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 900
```

6. Add environment variables in Cloud Run -> Service -> Edit & deploy new revision -> Variables & Secrets.
7. Copy the Cloud Run service URL into `PUBLIC_BASE_URL`.
8. Redeploy.

Official references:

- [Cloud Run deploy from source](https://cloud.google.com/run/docs/deploying-source-code)
- [Cloud Run container port and PORT variable](https://cloud.google.com/run/docs/configuring/services/containers)

## 9. How Publishing Works

The bot does this with Instagram Graph API:

1. Uploads final MP4 to public storage.
2. Calls:

```http
POST /{ig-user-id}/media
media_type=REELS
video_url=https://public-storage-url/reel.mp4
caption=...
```

3. Polls the returned container id until `status_code=FINISHED`.
4. Calls:

```http
POST /{ig-user-id}/media_publish
creation_id={container-id}
```

## 10. Optional Local Testing

Local testing is not required for deployment. If you later want it:

```bash
cp .env.example .env
docker compose up --build
```

The local webhook will not work unless you expose it through a public HTTPS URL. Production should use the deployed cloud URL.

## Troubleshooting

- Telegram says nothing happens: check `PUBLIC_BASE_URL`, redeploy, and verify `/health` opens in a browser.
- Instagram says it cannot fetch the video: verify the public storage URL opens in an incognito browser and returns the MP4 directly.
- Instagram token errors: check token scopes, expiry, app mode, Page link, and `INSTAGRAM_USER_ID`.
- Render/Railway free tier runs out of memory: set `LOW_MEMORY_MODE=true`, `ENABLE_TRANSCRIPTION=false`, `MAX_DOWNLOAD_MB=150`, then redeploy. For Whisper transcription, use a host with at least 1-2 GB RAM.
- YouTube downloads fail: the video may block cloud datacenter downloads or require cookies. Upload the video directly, send a direct MP4 URL, or configure `YOUTUBE_COOKIES_FILE`.
- Prompt-only Reel looks simple: this starter creates a clean text Reel with silent audio and generated captions; it does not generate AI video footage.
