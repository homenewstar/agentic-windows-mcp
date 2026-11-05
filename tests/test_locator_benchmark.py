"""Issue #24: the locator-resolution benchmark harness classifies synthetic
trees correctly (unique -> resolved, twins -> ambiguous, absent -> stale,
value-mutated -> resolved). Pure logic; runs on any platform."""
import importlib.util
from pathlib import Path

BENCH = (Path(__file__).resolve().parent.parent
         / "benchmarks" / "locator-resolution" / "bench_locator.py")


def _load():
    spec = importlib.util.spec_from_file_location("bench_locator", BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_scenarios_classify_as_expected():
    bench = _load()
    seen = set()
    for name, tree, identity, expected in bench.scenarios(300):
        assert bench.classify(tree, identity) == expected, name
        seen.add(name)
    assert seen == {"unique", "twin-duplicates", "absent", "value-mutated"}


def test_value_is_not_identity():
    bench = _load()
    root, _ = bench.build_tree(200, seed=7)
    target = next(n for n in bench.walk(root) if n["role"] != "window")
    identity = bench.identity_of(target)
    target["value"] = "something typed later"
    assert bench.count_matches(root, identity) >= 1
