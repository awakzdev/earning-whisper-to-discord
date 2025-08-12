## Earning Whisper → Discord (CI-triggered poster)

A tiny pipeline that watches @eWhispers on X/Twitter, grabs the weekly earnings collage, and posts it to your Discord channel.

You can trigger it on a schedule or manually via Discord slash commands **(`/check` and `/trigger`)** that call a Cloudflare Worker, which in turn kicks off a GitHub Actions workflow that runs the Python poster.

## How it works (high level)
```bash
Discord Slash Command (/check or /trigger)
          │
          ▼
Cloudflare Worker (/interactions)  ← verifies Discord signature (Ed25519)
          │
          ├──> Calls GitHub REST API: workflow_dispatch (with inputs force/photo)
          ▼
GitHub Actions job (Python 3.11)
          │
          ├── Runs ew_to_discord.py:
          │     • Scrapes mirrors of https://x.com/eWhispers via r.jina.ai
          │     • Finds newest collage tweet (or "force latest")
          │     • Downloads the collage image (optional)
          │     • Posts to your Discord Webhook
          │     • Writes/updates last_id.json (and last_post.ts)
          │
          └── Commits state back to the repo (so cron won’t repost the same)
```

* `/check`: Normal run (default: attach photo, do not force).

* `/trigger`: Manual force-post the latest (attach photo and ignore last_id.json).

* **Cron**: Runs on a schedule without forcing; if last_id.json already matches the latest, it skips.

Repo layout (typical)
```bash
.
├─ ew_to_discord.py        # Python script that finds & posts the collage
├─ .github/workflows/ew-to-discord.yml
├─ last_id.json            # State: last posted tweet id (committed by CI)
├─ last_post.ts            # State: Unix timestamp of last successful post
└─ README.md               # (this file)
```

** Requirements
* **Discord** server where you can create a webhook and add an application/bot.

* **Cloudflare** Worker (for slash-command → workflow dispatch).

* **GitHub Actions** enabled in your repository.

* **Python 3.11** on the GitHub runner (handled by the workflow).

* Your repo secret: `DISCORD_WEBHOOK_URL`.

Tokens/secrets must not be committed to git. Keep them in GitHub Secrets / Cloudflare Worker Secrets.

