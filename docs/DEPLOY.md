# Putting Relay online for free

Relay runs on your own computer with no setup (`python -m orchestrator.web`). This puts it
online, so routines run while your computer is off and you can use it from your phone. Every
service here is free and none needs a card.

| Piece | Service | Why |
|---|---|---|
| The app | Render (free web service) | Runs the server and the robots |
| Data | Neon (free Postgres) | Render's own disk is wiped on every deploy |
| AI | Groq, from an account with **no card added** | A free account can never be charged |

(Hugging Face Spaces was the first choice, but since 2026 Docker Spaces need a paid PRO plan.)

## Three accounts, three values

1. **Render** (render.com): sign up **with GitHub**, and when GitHub asks which repositories
   Render may see, allow `agent-orchestrator`. Then **Account Settings > API Keys > Create API
   Key**. Copy it (starts with `rnd_`).
2. **Groq** (console.groq.com), a **new account with no payment method**: API Keys >
   **Create API Key**. Copy the key (starts with `gsk_`).
3. **Neon** (neon.tech): create a project (pick the region closest to you), open it, click
   **Connect** at the top of the project page, and copy the connection string (starts with
   `postgresql://`; click "Show password" first if it shows `****`).

## One command

Push your latest commits first (VS Code: **Source Control > Sync Changes**), because Render builds
from GitHub. Then, in VS Code's terminal, in the project folder:

```powershell
python deploy\deploy.py
```

The first time, it creates `deploy\keys.env`. Paste the three values, put your email (and any
friends' emails) on the `EMAILS=` line, save, and run the same command again. It:

- creates the site's private keys and **an invite code for each email**
- creates your free Render service from your GitHub repository (`deploy/render/Dockerfile`)
- stores every secret in the service's settings, never in the code
- turns on a keep-awake ping: Render's free plan sleeps after 15 minutes without visits, so
  the server visits itself every 10 minutes, and `.github/workflows/keep-awake.yml` wakes it
  every 2 hours as a backup

It prints your site's address (`https://relay.onrender.com` or similar) and the invite codes.
The first build takes 5 to 8 minutes; the script also prints a link where you can watch it.
Push once more afterwards so the backup workflow is on GitHub.

`deploy\keys.env` and `deploy\generated.env` stay on your computer (they are in `.gitignore`).
Keep `generated.env`: running `deploy.py` again reuses the same keys, so nobody is signed out.
After you push new code, Render rebuilds automatically.

## Inviting people

Add their email to `EMAILS=` in `deploy\keys.env` and run `python deploy\deploy.py` again.
Send each person the site's address and their invite code from the output.

## On your phone

Open the site in Chrome (Android) or Safari (iPhone), sign in, then add it to the home screen
(Safari: **Share > Add to Home Screen**). Open it from the home screen and choose
**Settings > Notifications > Turn on**. On iPhone, notifications only work from the home-screen
app (iOS 16.4 or later). 

## Gmail and Calendar (optional)

Email summaries, deadlines, reply drafts, and the calendar in the morning brief need read-only
access to Google. This is the only fiddly part, and Relay works without it.

In console.cloud.google.com:

1. Create a project called Relay.
2. **APIs & Services > Library**: enable the **Gmail API** and the **Google Calendar API**.
3. **Google Auth Platform** (called *OAuth consent screen* in older consoles):
   - Branding: app name Relay, your email as the support and developer contact.
   - Audience: **External**, publishing status **Testing**. Under **Test users**, add your Gmail
     address and everyone you invited (up to 100).
   - Data access: add `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`,
     `.../auth/gmail.readonly`, and `.../auth/calendar.readonly`.
4. **Clients**: create an OAuth client of type **Web application**. Under **Authorised redirect
   URIs** add your site's address followed by `/auth/callback`, for example
   `https://relay.onrender.com/auth/callback`. Copy the client ID and
   secret into `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `deploy\keys.env`, and run
   `python deploy\deploy.py` again.

Two things Google does while the app is in Testing, which are normal: people see a warning that
Google hasn't verified the app (they click **Continue**), and Gmail and Calendar connections end
after seven days, when Relay shows a one-tap **Reconnect**. Public apps that read Gmail need a
paid security review, which a personal assistant doesn't need.

## Limits to know

- A free Groq key allows about 200,000 tokens a day per model, which is roughly 5 to 10 team
  jobs. Relay stops at `ASSISTANT_DAILY_TOKENS` (default 180,000) for the site and
  `ASSISTANT_USER_DAILY_TOKENS` (default 90,000) per person, and a skipped routine says so. The
  Concierge uses the smaller `gpt-oss-20b`, which has its own quota. To fit more in, give other
  robots the smaller model too with the environment variables `LLM_MODEL_PLANNER`,
  `LLM_MODEL_TESTER`, and `LLM_MODEL_REVIEWER` (Render: your service > Environment).
- Render's free plan has 512 MB of memory and a small share of a CPU: plenty for one person and
  a few friends, and jobs take a little longer to start than on your own computer.
- One job runs at a time; others wait their turn.
- Model-written code runs in the guarded sandbox (`src/orchestrator/guard`), which blocks
  network access, other programs, and the app's own files, and none of the site's secrets are
  passed to it. It is defence in depth for a small invite-only site, not a container.
