# Putting Relay online for free

Relay runs on your own computer with no setup (`python -m orchestrator.web`). This puts it
online, so routines run while your computer is off and you can use it from your phone. Every
service here is free and none needs a card.

| Piece | Service | Why |
|---|---|---|
| The app | Hugging Face Spaces (Docker, free CPU) | Runs the server and the robots |
| Data | Neon (free Postgres) | A Space's own disk is wiped on restart |
| AI | Groq, from an account with **no card added** | A free account can never be charged |
| Staying awake | GitHub Actions, in this repository | Free Spaces pause after 48 hours without visits |

## Three accounts, three values

Sign up for each (all three accept "Continue with Google" or GitHub), then copy one value
from each. You never need to paste these anywhere except your own `deploy/keys.env`.

1. **Hugging Face** (huggingface.co): Settings > Access Tokens > **Create new token**, type
   **Write**. Copy the token (starts with `hf_`).
2. **Groq** (console.groq.com), a **new account with no payment method**: API Keys >
   **Create API Key**. Copy the key (starts with `gsk_`).
3. **Neon** (neon.tech): create a project (pick the region closest to you), then **Connect**
   and copy the connection string (starts with `postgresql://`).

## One command

In VS Code's terminal, in the project folder:

```powershell
python deploy\deploy.py
```

The first time, it creates `deploy\keys.env`. Open it, paste the three values, put your email
(and any friends' emails) on the `EMAILS=` line, save, and run the same command again. It:

- creates the site's private keys and **an invite code for each email**
- builds the app and creates your Space at `https://<your-username>-relay.hf.space`
- stores every secret in the Space's settings, never in the code
- writes `.github/workflows/keep-awake.yml`, which visits the site every 6 hours

It prints your site's address and the invite codes. Hugging Face takes 3 to 5 minutes to build
it the first time. Then **push to GitHub** (VS Code Sync) so the keep-awake workflow starts.

`deploy\keys.env` and `deploy\generated.env` stay on your computer (they are in `.gitignore`).
Keep `generated.env`: running `deploy.py` again publishes a new version with the same keys,
so nobody is signed out.

## Inviting people

Add their email to `EMAILS=` in `deploy\keys.env` and run `python deploy\deploy.py` again.
Send each person the site's address and their invite code from the output.

## On your phone

Open the site in Chrome (Android) or Safari (iPhone), sign in, then add it to the home screen
(Safari: **Share > Add to Home Screen**). Open it from the home screen and choose
**Settings > Notifications > Turn on**. On iPhone, notifications only work from the home-screen
app (iOS 16.4 or later). Open the `hf.space` address directly, not the huggingface.co/spaces
page, which shows the site inside a frame where notifications are blocked.

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
   URIs** add `https://<your-username>-relay.hf.space/auth/callback`. Copy the client ID and
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
  robots the smaller model too with the Space variables `LLM_MODEL_PLANNER`,
  `LLM_MODEL_TESTER`, and `LLM_MODEL_REVIEWER`.
- One job runs at a time; others wait their turn.
- Model-written code runs in the guarded sandbox (`src/orchestrator/guard`), which blocks
  network access, other programs, and the app's own files, and none of the site's secrets are
  passed to it. It is defence in depth for a small invite-only site, not a container.
