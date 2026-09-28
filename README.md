# Claude Reset Watch

Checks official Claude / Anthropic X accounts every 15 minutes and posts any
usage-limit reset announcement to a Discord channel. Runs free on GitHub Actions,
so it works even when your PC is off.

## How it works

Every 15 minutes GitHub starts `reset_watcher.py`. It reads each account's RSS feed,
skips posts it has already seen (tracked in `state.json`), and sends any new post
matching the `keywords` in `config.json` to your Discord webhook. The first run only
records existing posts, so old ones aren't spammed.

## Setup

1. **Discord webhook:** channel → Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL.
2. **Feeds:** at rss.app, create an X/Twitter feed for each account
   (e.g. `https://x.com/claudeai`) and paste each feed URL into `sources` in `config.json`.
3. **GitHub repo:** github.com → New repository → **Public** (public = unlimited free runs;
   nothing secret is in these files) → "uploading an existing file" → drag in everything in
   this folder, including the `.github` folder → Commit.
4. **Secret:** repo → Settings → Secrets and variables → Actions → New repository secret →
   name `DISCORD_WEBHOOK_URL`, value = the webhook URL.
5. **Start it:** repo → Actions tab → enable workflows if asked → "Claude Reset Watch" →
   Run workflow. After that it runs by itself every 15 minutes.

To change accounts or keywords later, edit `config.json` on GitHub (pencil icon).

## Running on your PC instead (optional)

Put the webhook URL in `config.json`, then `python reset_watcher.py --test` to check it,
and `powershell -ExecutionPolicy Bypass -File install_task.ps1` to run every 10 minutes
while you're logged in. Don't run both this and GitHub, or you'll get duplicates.
