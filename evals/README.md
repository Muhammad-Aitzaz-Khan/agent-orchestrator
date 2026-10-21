# Evals

The eval harness measures how often the agent fixes real bugs. Unit tests in `tests/` check that
the code works; evals check how well the AI performs.

## Running

```powershell
python evals/run_evals.py --check-tasks          # validate every task, no API calls
python evals/run_evals.py --repeats 3            # full run, 3 attempts per task
python evals/run_evals.py --difficulty hard
python evals/run_evals.py --task parser --repeats 5
python evals/run_evals.py --retry-errors evals/results/<file>.json   # rerun only API errors
python evals/run_evals.py --mode multi            # the four-agent team (see docs/ARCHITECTURE.md)
```

`--mode single` (the default) runs one agent; `--mode multi` runs the Planner, Coder, Tester,
and Reviewer team. Results record the mode, and multi-mode rows also record the number of
rounds and the tokens used by each role. Compare the two modes on the same model.

Results are printed per difficulty and saved to `evals/results/<timestamp>.json` after every
run, so an interrupted eval loses nothing. Failing runs list the names of the tests that failed.

## Free-tier limits

Groq's free tier limits tokens per minute and per day (for `openai/gpt-oss-120b`, about 8,000
per minute and 200,000 per day). One task run uses roughly 7,000-12,000 tokens, so a full
`--repeats 3` run (45 runs) needs about two days of quota.

- Per-minute limits: the agent waits for the time the API asks for and retries automatically.
- Daily limit: the eval stops, saves what it has, and prints the `--retry-errors` command to
  finish the remaining runs once the limit resets.
- Request size: Groq rejects any single request over ~8,000 tokens. Long runs are kept under
  `LLM_MAX_CONTEXT_CHARS` (default 20,000) by shortening old tool outputs, then dropping the
  oldest steps (see `src/orchestrator/context.py`).
- Malformed tool calls that the provider rejects are retried automatically.

To use a different provider, such as Gemini's free tier, see `.env.example` and check the setup
with `python -m orchestrator.doctor`. Results from different models are not directly comparable,
so record the model (it is saved in every results file).

## How a run is judged

1. The task's `repo/` is copied to a temporary folder. The original is never modified.
2. The agent works in that copy.
3. Test and config files are restored from the original, so editing tests cannot pass.
4. Hidden tests (if any) are added. The agent never sees these.
5. The harness runs pytest itself. The agent's own claim of success is ignored.

API errors (rate limits, network) are reported separately and excluded from the pass rate.

## Tasks

| Task | Level | What makes it hard |
|---|---|---|
| easy_calculator | easy | Three one-line bugs, failing tests point straight at them |
| easy_grades | easy | Boundary conditions (`>` vs `>=`), empty input |
| easy_strings | easy | Case handling, empty words, wrong operation |
| easy_temperature | easy | Formula and operator-precedence bugs |
| medium_bank_account | medium | Mutable default argument; hidden tests check rollback on failed transfer |
| medium_cart | medium | Percent math, ignored quantity, empty input |
| medium_inventory | medium | Two files: tests fail in `inventory.py` but most bugs are in `pricing.py` |
| medium_monthly_report | medium | Vague bug report instead of "tests are failing"; hidden refund case |
| medium_word_frequency | medium | Normalisation plus tie-breaking rules from the docstring |
| hard_algorithms | hard | Binary search and interval merging; hidden edge cases |
| hard_dynamic_programming | hard | Off-by-one DP tables and base cases; hidden impossible/empty cases |
| hard_expression_parser | hard | Tokenizer, associativity, and unary minus in a recursive-descent parser |
| hard_graph | hard | Topological sort ordering and cycle detection; reversed BFS path |
| hard_lru_cache | hard | Recency tracking, update vs insert, capacity 0 |
| hard_meeting_scheduler | hard | Two files, all visible tests pass; only a user bug report describes the problem |

## Task layout

```
evals/tasks/<name>/
    task.toml      difficulty ("easy" | "medium" | "hard") and the prompt given to the agent
    repo/          the buggy project, including visible tests and pytest.ini
    hidden/        optional: extra tests the agent never sees
    solution/      fixed source files; used only by --check-tasks
```

## Adding a task

1. Write the correct code and tests first, and confirm they pass.
2. Copy the correct files into `solution/`, then insert bugs into the copies in `repo/`.
3. Put the specification in docstrings, so hidden tests are fair: the agent can only be expected
   to handle edge cases it can read about.
4. Keep tests deterministic (no randomness, network, or timing) and fast.
5. Keep each source file under 5,000 characters, the agent's `read_file` limit.
6. Run `python evals/run_evals.py --check-tasks`. Every task must show `OK`.

`tests/test_evals.py` runs the same check, so `pytest` fails if any task breaks.
