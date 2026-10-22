# Research notes: personal-assistant agents and interface design

Notes gathered on 22 October 2026 before turning the bug-fixing team into a personal
assistant. Each section ends with what was taken into this project.

## How personal-assistant agent systems are built

**Ambient agents (LangChain).** Assistants that react to events and schedules instead of
waiting in a chat. The key design question is *when* to involve the person. Three patterns:
*notify* (flag something without acting), *question* (ask for missing information instead of
guessing), and *review* (show a proposed action for approval before it runs). Open items are
collected in an *agent inbox* modelled on email, because chat notifications pile up and get
lost. [Introducing ambient agents](https://www.langchain.com/blog/introducing-ambient-agents)

**Supervisor and sub-agents (LangChain personal-assistant tutorial).** Three layers: low-level
API tools, domain sub-agents (calendar, email) with narrow tools and prompts, and a supervisor
that routes requests. Narrow sub-agents make fewer tool-selection mistakes and can be tested
separately. Sensitive actions are interrupted for human review.
[Subagents: personal assistant](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents-personal-assistant)

**Daily briefing bots (Hermes Agent).** A cron scheduler starts a fresh agent session at the
set time; because the session has no memory of earlier chats, the stored prompt must be
self-contained, including the person's preferences. Results go to a messaging channel or are
saved. [Build a daily briefing bot](https://hermes-agent.nousresearch.com/docs/guides/daily-briefing-bot)

**GAIA.** A proactive assistant with cron-style and event triggers (new email, calendar
events), results collected in a notifications page where each item can be approved, edited,
or dismissed, and connections to Gmail and Calendar through OAuth. Email content is fetched on
demand rather than stored, and tokens are encrypted.
[theexperiencecompany/gaia](https://github.com/theexperiencecompany/gaia)

**OpenClaw.** The most popular self-hosted assistant of 2026, driven through messaging apps,
with skills defined as folders of instructions. Its record is a warning: broad access to
email and messages, prompt injection through the data it reads, a malicious third-party skill
that exfiltrated data, unencrypted secrets, and an agent that acted without being asked.
[OpenClaw (Wikipedia)](https://en.wikipedia.org/wiki/OpenClaw)

**Survey of open-source assistants (Vellum).** The distinguishing features are proactivity,
messaging channels, memory, and isolating credentials from the model.
[Best open-source personal AI assistants](https://www.vellum.ai/blog/best-open-source-personal-ai-assistants)

**No-code builds (n8n tutorials and videos).** The common recipe for a morning briefing or
email digest is: a schedule trigger, fetch (RSS, Gmail), an AI step that summarises, and a
delivery step (Telegram, email).
[How to build AI agents to automate everything (n8n)](https://www.chaindesk.ai/es/tools/youtube-summarizer/how-to-build-ai-agents-to-automate-everything-n8n-tutorial-L09kacrneOc),
[Building an AI news assistant with n8n](https://www.labellerr.com/blog/building-ai-news-assistant-with-n8n/amp/)

### Taken into Relay

- A **Concierge** agent turns a request into a job or a routine, and shows it as a card to
  confirm (the *review* pattern) before anything is scheduled.
- Scheduled runs store a **self-contained instruction** with the person's preferences.
- **Gather first, then reason.** Each run collects its inputs (news headlines, emails,
  weather, the uploaded file) with ordinary code and hands them to the team as files. The
  agents never get network or mailbox tools, so text inside an email or a web page cannot make
  them fetch or send anything: the main prompt-injection risk seen with OpenClaw is designed out.
- Results go to an **Inbox** and a **browser notification** (notify), so the person does not
  have to keep the site open.
- Mail is **read-only**, never stored in full, and tokens are **encrypted**; nothing is sent
  on the person's behalf.
- The four-robot team stays: it plans, makes, checks against the gathered sources, and reviews.

## Platform facts that shaped the design

- **Gmail access.** `gmail.readonly` is a restricted scope. An unverified app can only use it in
  *Testing* mode with up to 100 test users, and test users' authorisations expire after seven
  days. Public use requires Google's verification.
  [Manage app audience (Google)](https://support.google.com/cloud/answer/15549945)
  So: the people allowed to use Relay are added as test users, and Relay asks for a one-tap
  reconnect when the weekly authorisation runs out.
- **Hosting.** Free Hugging Face Spaces pause after 48 hours without visits, and the container
  disk does not persist across restarts.
  [Manage your Space](https://huggingface.co/docs/huggingface_hub/main/guides/manage-spaces)
  So: data lives in a free Postgres database (Neon: 1 GB, no card, scales to zero,
  [Neon free tier](https://neon.com/faqs/managed-postgres-databases-free-tier)), and a free
  uptime pinger visits the Space every few hours.
- **Web push.** Works in desktop and Android browsers; on iPhone it needs iOS 16.4 or later
  and the site added to the home screen.
  [PWA iOS limitations](https://www.magicbell.com/blog/pwa-ios-limitations-safari-support-complete-guide)
  So: Relay is installable (manifest, icons, service worker) and explains this on iPhone.

## Interface design: books and what was applied

Sources: the books most often recommended in design reading lists
([uxfol.io list](https://blog.uxfol.io/ux-design-books/)) and Jon Yablonski's
[Laws of UX](https://oreilly.com/library/view/laws-of-ux/9781098146955); visual references from
illustrated sites featured on [Awwwards](https://www.awwwards.com/websites/illustration/).

| Source | Principle | Where it shows up |
|---|---|---|
| *Don't Make Me Think* (Krug) | One obvious thing to do; plain labels; follow conventions | One ask box on Today; nav labels Today, Inbox, Routines, Files |
| *The Design of Everyday Things* (Norman) | Visibility of system status, feedback, recoverable mistakes | Every job shows its state; routines can be paused or deleted; nothing runs before you confirm |
| *Refactoring UI* (Wathan, Schoger) | Hierarchy through weight and colour before size; a small palette with shades; illustrated empty states | Warm ink on deep navy, one amber accent; every empty list has a robot illustration |
| *Laws of UX*: Hick's law | Fewer choices are faster | Six suggestions, not a menu of features |
| *Laws of UX*: Fitts's law | Big, close targets | 44 px minimum touch targets; bottom tab bar on phones |
| *Laws of UX*: Doherty threshold | Respond within 400 ms | Instant optimistic cards; the Concierge "is thinking" state |
| *Laws of UX*: Jakob's law | Use patterns people know | The Inbox works like email: unread dots, newest first |
| *Laws of UX*: Peak-end rule | Make the best and last moments good | A small celebration when a job finishes; a friendly notification |
| *Laws of UX*: Goal-gradient, Zeigarnik | Show progress on unfinished setup | A "Get set up" checklist with progress |
| *Laws of UX*: Tesler's law | Complexity moves somewhere; let the system absorb it | Schedules are written in plain words, not cron |
| *Microinteractions* (Saffer) | Trigger, rules, feedback, loops | Toggles, the "Show agents working" reveal, copy and download confirmations |
| Illustrated sites (Awwwards) | A distinct illustration style and a characterful display face | Original tin-toy robots; Fraunces for headings, Archivo for text |
