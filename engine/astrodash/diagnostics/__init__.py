"""Session diagnostics (PLAN.md §4): astro-eval measurements, run as background jobs.

Results are stored in the engine's own database (var/). Nothing here writes
into the data root: astro-eval's report writers are never imported.
"""
