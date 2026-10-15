"""Live observatory: direct connections to N.I.N.A. (Advanced API) and PHD2 (event server).

The engine owns these connections itself (PLAN §5.3) — no AstroController in between.
`runtime.Observatory` runs the background tasks inside the engine's event loop;
`fake` is an in-process simulator for daylight testing (`--fake-observatory`).
"""
