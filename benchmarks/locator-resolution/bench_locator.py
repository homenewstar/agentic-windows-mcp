#!/usr/bin/env python3
"""Locator-resolution benchmark (issue #24).

Measures the identity-predicate counting that strict ref resolution is built
on: given a stored identity (role + AutomationId + stable name), count how
many elements in a tree match it, and classify the ref as

    exactly 1 match  -> resolved
    0 matches        -> stale
    2 or more        -> ambiguous

Control *values* and *bounds* are never part of the identity: a scenario in
which only the target's value changed must still resolve.

The trees are synthetic and deterministically generated (fixed seed), so two
runs print directly comparable numbers. When the shipped resolver grows its
own counting implementation (issue #12), this harness is the reference it is
checked against; `tests/test_locator_benchmark.py` runs the small scenario
set in CI.

Usage:
    python benchmarks/locator-resolution/bench_locator.py [--sizes 500,2000]
                                                            [--repeats 15]

Exit code is non-zero if any scenario classifies differently than expected.
Pure standard library; runs on any platform (it measures logic, not UIA).
"""
import argparse
import json
import random
import statistics
import sys
import time

ROLES = ["button", "edit", "check box", "list item", "tab item", "hyperlink",
         "combo box", "radio button", "slider", "text"]
NAMES = ["OK", "Cancel", "Save", "Delete", "Apply", "Close", "Submit",
         "Search", "Name", "Address", "Item", "Row", "Open", "Edit"]


def make_node(rng, depth):
    node = {
        "role": rng.choice(ROLES),
        "name": rng.choice(NAMES) + " " + str(rng.randrange(40)),
        "automation_id": "aid_%d" % rng.randrange(3000),
        "value": "",
        "children": [],
    }
    if depth > 0:
        for _ in range(rng.randrange(0, 4)):
            node["children"].append(make_node(rng, depth - 1))
    return node


def build_tree(target_nodes, seed):
    """A tree with approximately target_nodes elements."""
    rng = random.Random(seed)
    root = {"role": "window", "name": "Bench", "automation_id": "root",
            "value": "", "children": []}
    count = 1
    frontier = [root]
    while count < target_nodes and frontier:
        nxt = []
        for parent in frontier:
            for _ in range(rng.randrange(2, 5)):
                child = make_node(rng, 0)
                parent["children"].append(child)
                nxt.append(child)
                count += 1
                if count >= target_nodes:
                    break
            if count >= target_nodes:
                break
        frontier = nxt
    return root, count


def walk(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node["children"])


def identity_of(node):
    """The identity predicate fields of an element (never value/bounds)."""
    return (node["role"], node["automation_id"], node["name"])


def count_matches(root, identity):
    return sum(1 for n in walk(root) if identity_of(n) == identity)


def classify(root, identity):
    n = count_matches(root, identity)
    if n == 1:
        return "resolved"
    if n == 0:
        return "stale"
    return "ambiguous"


def pick_unique_target(root, rng):
    counts = {}
    for node in walk(root):
        counts[identity_of(node)] = counts.get(identity_of(node), 0) + 1
    uniques = [node for node in walk(root) if counts[identity_of(node)] == 1]
    return rng.choice(uniques)


def scenarios(size):
    """Yield (name, tree, identity, expected) for one tree size."""
    root, actual = build_tree(size, seed=1000 + size)
    rng = random.Random(42)

    target = pick_unique_target(root, rng)
    yield ("unique", root, identity_of(target), "resolved")

    # A twin: clone the target's identity onto another node -> ambiguous.
    twin_root, _ = build_tree(size, seed=1000 + size)
    twin_target = pick_unique_target(twin_root, rng)
    others = [n for n in walk(twin_root) if n is not twin_target]
    dup = rng.choice(others)
    dup["role"], dup["automation_id"], dup["name"] = identity_of(twin_target)
    yield ("twin-duplicates", twin_root, identity_of(twin_target), "ambiguous")

    # Absent identity -> stale.
    yield ("absent", root, ("button", "aid_does_not_exist", "Nope"), "stale")

    # Value mutated after capture -> still resolved (value is not identity).
    mut_root, _ = build_tree(size, seed=1000 + size)
    mut_target = pick_unique_target(mut_root, rng)
    ident = identity_of(mut_target)
    mut_target["value"] = "typed text that was not there at capture"
    yield ("value-mutated", mut_root, ident, "resolved")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="500,2000,8000")
    ap.add_argument("--repeats", type=int, default=15)
    args = ap.parse_args()

    failures = 0
    summary = []
    print(f"{'size':>6} {'scenario':<16} {'expect':<10} {'got':<10} {'median ms':>10}")
    for size in (int(s) for s in args.sizes.split(",")):
        for name, tree, ident, expected in scenarios(size):
            times = []
            got = None
            for _ in range(args.repeats):
                start = time.perf_counter()
                got = classify(tree, ident)
                times.append((time.perf_counter() - start) * 1000.0)
            ok = got == expected
            failures += 0 if ok else 1
            median = statistics.median(times)
            summary.append({"size": size, "scenario": name, "expected": expected,
                            "got": got, "ok": ok, "median_ms": round(median, 3)})
            print(f"{size:>6} {name:<16} {expected:<10} {got:<10} {median:>10.3f}")
    print("JSON:", json.dumps(summary))
    if failures:
        print(f"FAIL: {failures} scenario(s) misclassified")
        return 1
    print("OK: all scenarios classified as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
