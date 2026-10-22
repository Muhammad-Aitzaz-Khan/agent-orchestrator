# Architecture

The system fixes bugs in Python projects. It has two modes that share the same agent loop,
tools, and eval harness:

- **single**: one agent reads the code, edits it, and runs the tests (the baseline).
- **multi**: a team of four agents (Planner, Coder, Tester, Reviewer) coordinated by an
  orchestrator.

Both modes are measured on the same benchmark (`evals/`), so the effect of the team can be
compared directly with the single agent on the same model. Results: `evals/RESULTS.md`.

The team handles two kinds of work with the same state machine (`roles.TEAMS`):

- **fix**: repair an existing project. The Planner reads code, docstrings, and failing tests.
- **create**: make something new from a request (writing, a web page, code). The Coder is
  called the Builder, the Tester measures anything countable with `run_python` (paragraphs,
  word counts, forbidden words), and the test gate is skipped when the project has no tests.
  Random choices must be made with Python's `random` module, never claimed.

## Why four agents

The single-agent baseline showed three recurring failures:

| Failure seen in the baseline | Example | Which role addresses it |
|---|---|---|
| Stops as soon as the visible tests pass, ignoring rules stated only in docstrings | `min_coins([2], 3)` returned `inf` instead of `-1` in 3/3 runs | Planner lists docstring rules as requirements; Tester checks each one by running code |
| Needs reasoning beyond the tests | `hard_meeting_scheduler`: all visible tests already pass; only the bug report describes the problem | Planner turns the bug report into checkable requirements |
| Misses edge cases | `medium_bank_account`: history after a transfer | Tester probes edge cases; Reviewer checks correctness for all inputs |

Splitting the work means each agent has one job, a short prompt focused on that job, and only
the tools that job needs.

## The flow

```mermaid
flowchart LR
    T[Task] --> P[Planner]
    P -->|Plan: requirements| C[Coder]
    C --> G{Visible tests\n(no model)}
    G -->|fail| C
    G -->|pass| TE[Tester]
    TE -->|a requirement fails| C
    TE -->|all pass| R[Reviewer]
    R -->|blocker issues| C
    R -->|approve| D[Done]
```

Each return to the Coder starts a new **round** and carries specific feedback: the failing test
output, the Tester's evidence for each failing requirement, or the Reviewer's blocker issues.
After `max_rounds` (default 3: one attempt plus two revisions) the run ends as `unresolved`.

## Roles

| Role | Job | Tools | Submits | Step limit |
|---|---|---|---|---|
| Planner | Turn the task, docstrings, and failing tests into precise requirements | `list_files`, `read_file`, `run_tests` | `Plan`: summary, files to change, requirements | 8 |
| Coder | Change the code so every requirement holds | `list_files`, `read_file`, `write_file`, `run_tests`, `show_diff` | `ChangeSummary` | 15 |
| Tester | Check each requirement by running code | `list_files`, `read_file`, `run_tests`, `run_python` | `TestReport`: pass/fail plus evidence per requirement | 10 |
| Reviewer | Review the diff for correctness and unrelated changes | `read_file`, `show_diff`, `run_tests` | `Review`: approve or revise, with issues | 6 |

Definitions live in `src/orchestrator/roles.py`; data models in `src/orchestrator/state.py`.

## Key design decisions

**One agent loop, four configurations.** Every role runs `agent.run_agent` with a different
system prompt, tool set, and submit tool. Rate-limit handling, context compaction, and tracing
are written once and shared by all roles.

**Structured hand-offs through submit tools.** Each role finishes by calling a tool such as
`submit_plan`, whose JSON schema is generated from a pydantic model. The arguments are
validated; an invalid submission is returned to the model as an error to fix. The next role
therefore receives typed data (a list of requirements with IDs) rather than prose it would
have to interpret. A role that stops without submitting is reminded twice.

**Least privilege, enforced twice.** Each role is shown only the schemas of its own tools, and
`execute_tool` also refuses any tool outside the role's allowed set, because a model can name
a tool it was never offered. Only the Coder can write files.

**The Tester cannot change the project.** `run_python` copies the project (without `.env`,
caches, or virtual environments) to a temporary folder and runs the script there with a
timeout and with API keys removed from the environment. Whatever the script does is
discarded.

**A deterministic gate before any checker runs.** After the Coder, the orchestrator runs the
visible tests itself. If they fail, the round goes straight back to the Coder with the test
output, so no tokens are spent testing or reviewing a fix that does not pass the existing
tests. Likewise the Reviewer only runs once the Tester reports every requirement passing.

**Minor issues never block.** The Reviewer marks each issue `blocker` or `minor`. Only
blockers send the Coder back, which keeps style preferences from consuming rounds.

**Graceful degradation.** If the Planner fails to submit, a fallback plan built from the task
is used. If the Tester fails to submit, the run continues to review, marked as unverified.
If the Reviewer fails to submit after the tests and checks passed, the change is accepted.
Failures that retrying cannot fix (the daily API limit) abort the whole run with status
`error`, which the eval harness excludes from scores and can resume later.

**Budgets.** Each role has a step limit, the run has a round limit, and a per-task token
budget (`ORCH_TOKEN_BUDGET`, default 100,000) ends the run as `budget_exhausted`, protecting
a free-tier daily quota from one runaway task.

**A model per role.** `LLM_MODEL_PLANNER`, `LLM_MODEL_CODER`, `LLM_MODEL_TESTER`, and
`LLM_MODEL_REVIEWER` override `LLM_MODEL`. A smaller model can do the cheaper roles, and on
Groq, where each model has its own daily limit, this also spreads usage across quotas.

**Observable by default.** Every agent run writes its own JSONL trace. The orchestrator writes
a trace of phase transitions and a `runs/<id>.state.json` snapshot of the whole state (plan,
rounds, decisions, tokens per role) after every phase.

## The web app (Relay)

`python -m orchestrator.web` serves one page (`web/static/relay.html`): a box to ask the team
for anything (create mode) or to fix a sample project, and an office scene where the four
robots sit at desks. The robot holding the job's folder has its lamp on, its mini screen shows
its latest tool call (the file it opened, the code it saved, the test output), and the wall
board shows the checklist turning green as the Checker reports. Below the office: the active
robot's screen enlarged, the team chat, the checklist with evidence, and the result.

The orchestrator emits structured events (`run_started`, `agent_started`, `agent_step`,
`agent_submitted`, `agent_finished`, `gate`, `decision`, `run_finished`) through `on_update`,
and the server streams them with Server-Sent Events. The page turns them into a model and
plays it back, so a live job and a recording use the same code.

**Notes between robots.** Every submission has a `message` field: a short note, in plain
English, to the robot that works next. It is shown in the team chat and is also added to that
robot's instructions ("Note from the Planner: ..."), so the hand-off carries the sender's
emphasis as well as the structured data.

`web/site.py` builds the page with the recordings embedded: served by the app with live jobs
switched on, or written out as a single static file that only plays recordings.
`web/replays/` holds recordings rebuilt from real eval traces by `web/record.py`, with paths
that reveal a user's name removed.

**Local mode** listens on 127.0.0.1, allows one live job at a time, limits request length, and
saves each job's events so it can become a recording.

**Public mode** (`RELAY_PUBLIC=1`, used by `deploy/huggingface`) is for a site anyone can use
without a key:

- The key is read once into the provider client and removed from the environment; the
  process is made non-dumpable so other processes of the same user cannot read
  `/proc/<pid>/environ`.
- `SANDBOX=guarded`: every Python process the agents start loads `guard/sitecustomize.py`,
  which installs an audit hook (it cannot be removed once installed) that blocks network
  access, starting programs, ctypes, reading `/proc`, `/sys`, and the app's folder, and
  writing outside the job's folder and the temp folder. The child also gets a zero process
  limit (so even a direct `fork_exec` fails), and memory, file-size, and CPU limits.
- `RELAY_DAILY_TOKENS` (default 180,000) for the whole site and `RELAY_JOBS_PER_VISITOR`
  (default 3) per day, and a 60,000-token budget per job. When the provider reports its daily
  limit, the site closes for the day.
- Jobs run in a temporary folder that is deleted when the job ends; events are kept only in
  memory.

## Measuring what each part adds

`checkers=False` (`--mode plan-code` in the eval harness) runs only the Planner and the Coder,
plus the deterministic test gate. Comparing it with the full team isolates what the Tester and
Reviewer contribute and what they cost.

## Shared infrastructure

| Module | Responsibility |
|---|---|
| `agent.py` | The agent loop; retries for rate limits, overloaded providers, and malformed tool calls |
| `context.py` | Keeps each request under a size budget by shortening old outputs, then dropping the oldest steps |
| `tools.py` | Tool implementations and safety rules: path checks, protected test files, timeouts, secret-free environments, bytecode caching off |
| `roles.py` | The four roles and the messages built for each from the shared state |
| `orchestrator.py` | The state machine described above |
| `doctor.py` | Checks the provider, key, and whether a model can make tool calls |
| `web/` | The Relay web app, its live event stream, and replays |
| `evals/rescore.py` | Re-judges finished runs from their traces after a task's tests are corrected |
| `evals/run_evals.py` | Runs either mode on the benchmark and judges results with its own test run |

## Known limitations

- **Sandboxing is opt-in.** By default (`SANDBOX=local`) `run_tests` and `run_python` execute
  model-written code as normal processes, protected only by copies, timeouts, and secret-free
  environments. With `SANDBOX=docker` the same code runs in a throwaway container with no
  network, a read-only filesystem apart from the project folder and `/tmp`, an unprivileged
  user, and memory, CPU, and process limits (`sandbox/Dockerfile`). The eval judge uses the
  same setting.
- **Cost.** A team run makes more model calls than a single agent. The gate, the
  Tester-before-Reviewer ordering, and the budgets keep this down; the eval reports tokens per
  role so the trade-off can be measured.
- **The checkers are models too.** The Tester or Reviewer can be wrong. The eval harness never
  trusts them: success is decided only by the harness's own run of the visible and hidden
  tests.
