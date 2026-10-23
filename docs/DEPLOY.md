# Putting Relay online

Relay works on your own computer with no setup (`python -m orchestrator.web`). Putting it
online means routines run while your computer is off, you can use it from your phone, and you
can invite friends or let people try it for a day.

This is exactly how the live site was set up. It costs nothing to run.

| Piece | Service | Why this one |
|---|---|---|
| The app | Render, free web service | Builds straight from GitHub; 750 free hours a month is enough to run all month |
| The data | Neon, free Postgres | Render's own disk is wiped on every deploy |
| The AI | Groq, free account | Fast, generous free tier, and an account with no card can never be charged |

> **About cards.** Groq and Neon never asked for one. Render may ask new accounts to add a card
> as an identity check (it places a temporary $1 hold and releases it). The free plan stays
> at $0, and the deploy script only ever creates a free service. If you'd rather not, see
> "Other hosts" at the end.

## 1. Make three accounts and copy three keys

1. **Render** (render.com): sign up **with GitHub**. Then open
   **github.com/settings/installations**, click **Configure** next to Render, and under
   **Repository access** add `agent-orchestrator`. (If Render can't see the repository, the
   deploy stops with a message telling you to do this.) Back in Render: **Account Settings >
   API Keys > Create API Key**, and copy it. It starts with `rnd_`.
2. **Groq** (console.groq.com), on an account with **no payment method**: **API Keys > Create
   API Key**. It starts with `gsk_`.
3. **Neon** (neon.tech): create a project in the region nearest you, open it, click
   **Connect** at the top, and copy the connection string. It starts with `postgresql://`
   (click "Show password" first if it shows `****`).

## 2. Run one command

Push your latest code first (VS Code: **Source Control > Sync Changes**), because Render builds
from GitHub. Then, in the project folder:

```powershell
python deploy\deploy.py
```

The first time, it creates `deploy\keys.env`. Paste the three keys in, put your email (and any
friends' emails) on the `EMAILS=` line, save, and run the same command again. It:

- makes the site's own secrets and **an invite code for each email**
- creates your free Render service from the repository (it builds `deploy/render/Dockerfile`)
- stores every secret in the service's settings, never in the code
- runs the Concierge and Planner on Groq's smaller model and the other robots on the bigger
  one, which roughly doubles how much the free tier can do (each model has its own daily quota)
- turns on the keep-awake ping: Render's free plan sleeps after 15 minutes without visitors,
  so the server visits itself every 10 minutes, and a GitHub Action
  (`.github/workflows/keep-awake.yml`) wakes it every 2 hours as a backup

It prints your site's address and the invite codes. The first build takes 5 to 8 minutes;
the script also prints a link where you can watch it. **Push once more afterwards**, so the
keep-awake Action is on GitHub too.

`deploy\keys.env` and `deploy\generated.env` stay on your computer (both are in
`.gitignore`). Keep `generated.env`: running the script again reuses the same secrets and
codes, so nobody gets signed out. After that, every push rebuilds the site by itself.

## 3. Use it, and invite people

- **You:** open the address, sign in with your email and invite code.
- **Friends:** add their email to `EMAILS=` in `deploy\keys.env`, run `python deploy\deploy.py`
  again, and send them the address and the new code it prints.
- **Anyone else:** they can press **Try it free for a day** on the front page. A guest gets 24
  hours, a small AI allowance of their own, no Gmail or Calendar, and everything they make is
  deleted when the day ends. One free day per browser, a few per network. To switch it off,
  set `TRIALS=off` in the service's settings (Render: your service > Environment). The other
  limits are in [SECURITY.md](SECURITY.md#the-free-day).

## 4. Put it on your phone

Open the site in Chrome (Android) or Safari (iPhone), sign in, and add it to your home screen
(Safari: **Share > Add to Home Screen**). Open it from there and choose **Settings >
Notifications > Turn on**. On iPhone, notifications only work from the home-screen app
(iOS 16.4 or later).

## Gmail and Calendar (optional)

Email summaries, deadlines, reply drafts, and the calendar in the morning brief need read-only
access to Google. It's the one fiddly part, and Relay works fine without it.

In console.cloud.google.com:

1. Create a project called Relay.
2. **APIs & Services > Library**: turn on the **Gmail API** and the **Google Calendar API**.
3. **Google Auth Platform** (called *OAuth consent screen* in older consoles):
   - Branding: app name Relay, your email as the support and developer contact.
   - Audience: **External**, publishing status **Testing**. Under **Test users**, add your
     Gmail address and everyone you invited (up to 100).
   - Data access: add `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`,
     `.../auth/gmail.readonly`, and `.../auth/calendar.readonly`.
4. **Clients**: create an OAuth client of type **Web application**. Under **Authorised
   redirect URIs**, add your site's address followed by `/auth/callback`, for example
   `https://relay-u0de.onrender.com/auth/callback`. Copy the client ID and secret into
   `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `deploy\keys.env`, and run
   `python deploy\deploy.py` again.

While the Google app is in Testing, two things are normal: people see a warning that Google
hasn't verified the app (they click **Continue**), and the Gmail and Calendar connections end
after seven days, when Relay shows a one-tap **Reconnect**. Public apps that read Gmail need a
paid security review, which a personal assistant for you and your friends doesn't need.

## Limits worth knowing

- **How much the AI can do.** A free Groq key allows about 200,000 tokens a day per model. A
  news briefing uses about 60,000 tokens end to end, a set of flashcards about 25,000. Relay
  stops at `ASSISTANT_DAILY_TOKENS` (350,000 by default) for the whole site and
  `ASSISTANT_USER_DAILY_TOKENS` (150,000) per person, and a skipped routine says so instead of
  failing silently. Guests have their own, smaller allowance on top.
- **Speed.** Render's free plan has 512 MB of memory and a small share of a CPU: plenty for
  you and a few friends. Jobs run one at a time and others wait their turn.
- **Agent code.** Scripts the agents write run in the guarded sandbox
  (`src/orchestrator/guard`): no network, no other programs, none of the site's secrets. It's
  defence in depth for a small site, not a container. More in [SECURITY.md](SECURITY.md).

## Other hosts

Relay is one Docker image plus a Postgres URL, so it runs on most hosts. Things that changed
recently, as of October 2026: Hugging Face Docker Spaces now need a paid plan; Koyeb's free
instance needs a card and starts you on a paid plan; Fly.io no longer has a free tier for new
accounts. With the GitHub Student Developer Pack, **Azure for Students** gives $100 a year with
no card at all, which is enough for a small always-on virtual machine.
