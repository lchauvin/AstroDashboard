"""AstroDashboard engine.

Everything under the configured data root (G:\\Astro) is opened read-only.
Phase 1 has no code path that writes there at all; the only writes go to the
dashboard's own `var/` directory (the header cache).
"""

__version__ = "0.1.0"
