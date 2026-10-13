"""`uv run astrodash-engine [--port 3007] [--data-root G:/Astro]`"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from .api import create_app
from .config import load_config, load_env_file


def main() -> None:
    parser = argparse.ArgumentParser(description="AstroDashboard engine")
    parser.add_argument("--host", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="port (default 3007)")
    parser.add_argument("--data-root", type=Path, help="astro data root (default G:/Astro)")
    parser.add_argument("--config", type=Path, help="astrodashboard.toml path")
    parser.add_argument("--no-scan", action="store_true", help="do not scan on startup")
    args = parser.parse_args()

    env_keys = load_env_file()
    cfg = load_config(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.data_root:
        cfg.data_root = args.data_root

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if env_keys:
        logging.getLogger(__name__).info("loaded from .env: %s", ", ".join(env_keys))
    app = create_app(cfg, scan_on_start=not args.no_scan)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
