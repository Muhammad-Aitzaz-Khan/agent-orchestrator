# Results: one agent vs. a team of four

Measured on 21 October 2026 with `openai/gpt-oss-120b` on Groq, 15 bug-fixing tasks, 3 runs
per task in each mode, both modes on the same code and model. Raw data:

- single agent: `results/20261021-213140-rescored.json`
- team of four: `results/20261021-214435-rescored.json`

(See "Corrections" below for why these are the `-rescored` files.)

## Headline

| | Single agent | Team (Planner, Coder, Tester, Reviewer) |
|---|---|---|
| Easy (4 tasks) | 12 / 12 | 12 / 12 |
| Medium (5 tasks) | 15 / 15 | 15 / 15 |
| Hard (6 tasks) | 14 / 17 (82%) | **17 / 17 (100%)** |
| **All** | **41 / 44 (93%)** | **44 / 44 (100%)** |
| API errors (excluded above) | 1 | 1 |
| Average tokens per task | 11,459 | 47,932 (4.2x) |
| Average time per task | 11.1 s | 28.4 s (2.6x) |
| Total tokens for 45 runs | 0.51 M | 2.14 M |

The team solved every task it attempted. The single agent failed three runs, all on the same
task.

## Where the difference comes from

Per task, the two modes tie everywhere except one task:

| Task | Single | Team |
|---|---|---|
| hard_dynamic_programming | **0 / 3** | **3 / 3** |
| hard_meeting_scheduler | 2 / 2 (+1 error) | 2 / 2 (+1 error) |
| the other 13 tasks | 3 / 3 each | 3 / 3 each |

`hard_dynamic_programming` contains three functions. The visible tests expose two bugs; the
third (`min_coins` must return `-1` when an amount cannot be made) is stated only in a
docstring. The single agent fixed what the tests showed, saw them pass, and stopped, in all
three runs (and in all three runs of an earlier baseline). In the team runs, the Planner read
the docstring and wrote the rule down as a requirement (for example: "R13: If amount cannot be
formed using the given coins, min_coins returns -1"), and the Coder implemented it.

So the measurable benefit comes from **turning the specification into an explicit checklist
before coding**, which the Planner does.

## What the team's internals show

From the 44 completed team runs (`runs/orch-*.state.json`):

| Observation | Count |
|---|---|
| Runs finished in the first round (no revision) | 45 / 45 |
| Rounds where the visible-test gate failed | 0 |
| Test reports that found a failing requirement | 0 |
| Reviewer verdicts | 41 approve, 0 revise |
| Tester runs that ended without submitting a report | **28 / 44 (64%)** |
| Average requirements per plan | 11.2 (3 to 19) |

Tokens by role: Planner 21%, Coder 30%, **Tester 35%**, Reviewer 15%.

Three conclusions:

1. **The revision loop never fired.** Every fix passed the gate and was approved on the first
   attempt. On this benchmark the Tester and Reviewer never changed an outcome, so their value
   is unproven here; on these tasks they only added cost.
2. **The Tester was the most expensive role and usually did not finish.** It checked
   requirements one at a time, one script per step, and ran out of its 10 steps before
   submitting. (Fixed afterwards; see "Changes since this measurement".)
3. **The Reviewer approved everything.** A reviewer that never objects may be a rubber stamp,
   or the fixes were simply right. More difficult tasks are needed to tell the difference.

## Corrections made after the runs

**An unfair hidden test was removed.** `medium_bank_account` had a hidden test requiring a
transfer to appear in the history as exactly `("withdraw", 25)` and `("deposit", 25)`. No
docstring specifies that. Two single-agent runs wrote correct code that also recorded extra
`("transfer_out", ...)` entries, and failed only that test. The test was deleted (a hidden
test may only check behaviour the docstrings state), and every `medium_bank_account` run in
both modes was re-judged from its traces with `evals/rescore.py`, without rerunning any agent.
This changed the single agent from 39/44 to 41/44 and did not change the team's score.

## Limitations

- **Small sample.** 3 runs per task and one decisive task. The difference (3 of 44 runs) is
  real for this task but too small to claim a general improvement rate.
- **The benchmark is near its ceiling.** At 93% for one agent, there is little room to measure
  improvements. Harder tasks (larger codebases, several interacting bugs, vaguer reports) are
  needed.
- **One model.** Other models may benefit more or less from planning.
- **Cost.** The team used 4.2 times the tokens and 2.6 times the time.

## Changes since this measurement (not yet measured)

These were made after analysing the runs above and need a new run before any claim is made:

- The Tester checks several requirements per script, and every role is warned two steps before
  its step limit to submit what it has. Target: the 64% of Tester runs without a report.
- A malformed tool call is retried with a note explaining the error, instead of resending the
  identical request (which failed three times in a row in the one error run of each mode).
- An agent that repeats the same tool call three times is told to change approach.

## Next experiment

`--mode plan-code` runs only the Planner and the Coder (plus the deterministic test gate).
Since the gain came from the Planner and the checkers never changed an outcome, this tests
whether most of the benefit is available at about half the team's cost:

```powershell
python evals/run_evals.py --mode plan-code --repeats 3
python evals/run_evals.py --mode multi --repeats 3
```

The second command re-measures the full team with the Tester fixes above.
