"""The personal assistant built on the agent team.

    concierge.py  turns what a person asks into a job, a routine, or a reminder
    skills.py     the catalogue of things the assistant can do
    gather.py     collects a job's material (news, emails, weather, files, web pages)
    runner.py     runs a job: gather, then the four-agent team, then notify
    worker.py     the scheduler and the job queue
    store.py      the database (SQLite locally, Postgres in the cloud)
    google.py     Google sign-in, Gmail and Calendar (read-only)
    push.py       browser notifications (Web Push)
"""
