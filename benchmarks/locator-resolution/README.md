# Locator-resolution benchmark

Repeatable measurement of the identity-predicate counting that strict ref
resolution (issue #12) is built on: count the elements in a tree matching a
stored identity (role + AutomationId + stable name — never value or bounds)
and classify the ref as resolved (exactly 1), stale (0) or ambiguous (2+).

```
python benchmarks/locator-resolution/bench_locator.py
python benchmarks/locator-resolution/bench_locator.py --sizes 500,2000 --repeats 25
```

Trees are synthetic and generated from fixed seeds, so two runs print
comparable numbers; the harness exits non-zero if any scenario classifies
differently than expected. It uses only the standard library and runs on any
platform — it measures the counting logic, not live UIA.

Run it before and after resolver changes and record the numbers (machine +
commit) in `probes/windows/FINDINGS.md` or the change's PR. CI runs the small
scenario set via `tests/test_locator_benchmark.py` but does not gate on the
timings.

The harness also prints a **wrapper-descent measurement** (issue #28): a
Chromium-like tree where every semantic node sits under a chain of anonymous
wrappers, reporting the wrapper share of all walk visits and the
full-descent identity-count time per wrapper depth. The keep/skip decision
it backs lives in `probes/windows/FINDINGS.md` row W-007.
