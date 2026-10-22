# Putting Relay online for free

Relay runs on your own computer with no setup (`python -m orchestrator.web`). This guide puts
it online so it can run your routines while your computer is off, for you and up to 100
people you invite. Everything here uses free plans; none of them needs a card.

| Piece | Service | Why |
|---|---|---|
| The app | Hugging Face Spaces (Docker, free CPU) | Runs the server and the robots |
| Data | Neon (free Postgres) | A Space's disk is wiped on restart |
| AI | Groq, a free account with **no card added** | A free account cannot be charged |
| Sign-in, Gmail, Calendar | Google Cloud (free) | Read-only access for the people you invite |
| Staying awake | cron-job.org (free) | Free Spaces pause after 48 hours without visits |

Set aside about 30 minutes. Keep a note open: you will collect about ten values on the way.

## 1. The AI key

Make a **new** Groq account and do not add a payment method. Create an API key. This is
`GROQ_API_KEY`.

## 2. The database

Sign up at neon.tech, create a project (any region near you), and copy the connection
string. It starts with `postgresql://`. This is `DATABASE_URL`.

## 3. Your own secrets

On your computer, in the project folder:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -m orchestrator.assistant.push
```

The first line is `RELAY_SECRET_KEY` (it signs sign-in cookies and encrypts Google tokens;
never change it later, or everyone has to reconnect Google). The second command prints
`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_SUBJECT` (change the email to yours).

## 4. The Space

1. Sign up at huggingface.co. Click **New Space**, name it `relay`, choose **Docker** and
   **Blank**, and make it **Public** (the sign-in keeps it private to the people you invite).
2. Its address will be `https://<your-username>-relay.hf.space`. This is `PUBLIC_URL`.
3. On your computer: `python deploy/build_space.py`. On the Space's **Files** page click
   **Add file**, then **Upload files**, and drag in everything inside `dist\space`.

## 5. Google sign-in, Gmail, and Calendar

In console.cloud.google.com:

1. Create a project called Relay.
2. **APIs & Services > Library**: enable the **Gmail API** and the **Google Calendar API**.
3. **Google Auth Platform** (called *OAuth consent screen* in older consoles):
   - Branding: app name Relay, your email as the support and developer contact.
   - Audience: **External**, publishing status **Testing**. Under **Test users**, add your
     Gmail address and everyone you want to invite (up to 100).
   - Data access: add the scopes `openid`, `.../auth/userinfo.email`,
     `.../auth/userinfo.profile`, `.../auth/gmail.readonly`, and `.../auth/calendar.readonly`.
4. **Clients**: create an OAuth client of type **Web application**. Under **Authorised
   redirect URIs** add `https://<your-username>-relay.hf.space/auth/callback`. Copy the
   client ID and secret: `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

Two things Google does while the app is in Testing, which are normal:

- People see a warning that Google hasn't verified the app. They click **Continue**. (Public
  apps that read Gmail need a paid security review, which a personal assistant doesn't need.)
- Gmail and Calendar connections end after seven days. Relay notices, and shows a
  **Reconnect** button; it takes one tap.

## 6. Secrets on the Space

On the Space: **Settings > Variables and secrets > New secret**, one for each:

```
GROQ_API_KEY          from step 1
DATABASE_URL          from step 2
RELAY_SECRET_KEY      from step 3
VAPID_PUBLIC_KEY      from step 3
VAPID_PRIVATE_KEY     from step 3
VAPID_SUBJECT         from step 3
PUBLIC_URL            from step 4, e.g. https://aitzaz-relay.hf.space
GOOGLE_CLIENT_ID      from step 5
GOOGLE_CLIENT_SECRET  from step 5
ALLOWED_EMAILS        your Gmail and your invitees', separated by commas
```

The Space restarts. Open `PUBLIC_URL` directly (not the huggingface.co/spaces page, which
shows it inside a frame where notifications are blocked) and sign in.

## 7. Keep it awake

At cron-job.org, create a free account and a job that visits
`https://<your-username>-relay.hf.space/healthz` every 6 hours. Without it, a free Space
pauses after 48 hours with no visits, and routines stop until someone opens the site.

## 8. On your phone

Open the site in Chrome (Android) or Safari (iPhone), sign in, then add it to the home screen
(Safari: **Share > Add to Home Screen**). Open it from the home screen and choose
**Settings > Notifications > Turn on**. On iPhone, notifications only work from the home-screen
app (iOS 16.4 or later).

## Limits to know

- A free Groq key allows about 200,000 tokens a day per model, which is roughly 5 to 10 team
  jobs. Relay stops at `ASSISTANT_DAILY_TOKENS` (default 180,000) for the site and
  `ASSISTANT_USER_DAILY_TOKENS` (default 90,000) per person, and a skipped routine says so.
  To fit more in, give some robots a smaller model, which has its own daily quota, e.g.
  `LLM_MODEL_PLANNER`, `LLM_MODEL_TESTER`, `LLM_MODEL_REVIEWER`, and `LLM_MODEL_CONCIERGE`
  set to `openai/gpt-oss-20b`.
- One job runs at a time; others wait their turn.
- Model-written code runs in the guarded sandbox (see `src/orchestrator/guard`), which blocks
  network access, other programs, and the app's own files. It is defence in depth for a
  small invite-only site, not a container.
