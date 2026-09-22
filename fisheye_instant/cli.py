from __future__ import annotations

import argparse
from pathlib import Path

from fisheye.config import FisheyeConfig


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fisheye-instant", description="One anomaly score. Your response rules.")
    parser.add_argument("--config", help="Existing Fisheye TOML or JSON configuration")
    parser.add_argument("--data-dir", type=Path, default=Path("instant-data"))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--demo", action="store_true", help="Enable a sample agent workflow with local countermeasures")
    args = parser.parse_args(argv)
    try:
        import uvicorn

        from fisheye_instant.app import create_app

        overrides = {}
        if not args.config:
            overrides["storage"] = dict(
                sqlite_path=args.data_dir / "instant.db",
                events_jsonl_path=args.data_dir / "events.jsonl",
                alerts_jsonl_path=args.data_dir / "alerts.jsonl",
            )
        if args.host is not None or args.port is not None:
            overrides["api"] = {
                key: value for key, value in dict(host=args.host, port=args.port).items() if value is not None
            }
        cfg = FisheyeConfig.load(args.config, overrides)
        if cfg.api.host not in {"127.0.0.1", "localhost", "::1"} and not (cfg.api.api_key and cfg.api.review_api_key):
            parser.error("Remote binding requires both API and reviewer credentials")
        print(f"Fisheye Instant → http://{cfg.api.host}:{cfg.api.port}")
        uvicorn.run(create_app(config=cfg, demo=args.demo), host=cfg.api.host, port=cfg.api.port)
    except ImportError:
        parser.error("Install the web dependencies: pip install 'fisheye[instant]'")
    except (ValueError, OSError) as exc:
        parser.error(type(exc).__name__ + ": could not load configuration or start the server")


if __name__ == "__main__":
    main()
