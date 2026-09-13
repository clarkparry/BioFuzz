"""Command-line entry points.

`biofuzz.cli.fuzz` runs a campaign; `biofuzz.cli.triage` analyses its findings.
Both are invoked through the `biofuzz-fuzz` and `biofuzz-triage` wrappers at
the repository root, which put the checkout on sys.path and call main().
"""
