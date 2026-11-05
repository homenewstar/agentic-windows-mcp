# Windows probe corpus

Small, re-runnable scripts that measure real Windows behaviour **before**
product code is written against it. Each probe:

- writes one machine-readable capture (JSON) beside itself, named
  `<probe-name>.capture.json`;
- passes the capture through redaction (user name, machine name, profile
  paths → stable placeholders) before it is written;
- never modifies product code;
- is summarised as a row in [FINDINGS.md](FINDINGS.md) with a stable id and a
  verdict. The ledger row is the citable artefact; the capture is its evidence.

A probe that did not run, or produced nothing, is recorded as a **DEFERRED**
ledger row — recording that nothing was looked at is not a measurement.

## Probes

| Script | Measures | Ledger |
|--------|----------|--------|
| `probe_integrity_level.py [pid]` | The caller's token integrity level and, optionally, a target process's — the comparison issue #14 performs before sending input. | W-002 |

## Running

On the target Windows machine, from this directory:

```
py -3.13 probe_integrity_level.py
py -3.13 probe_integrity_level.py 1234   # also read pid 1234's level
```

The script prints the (redacted) JSON it writes, so a human can sanity-check
what would be committed.
