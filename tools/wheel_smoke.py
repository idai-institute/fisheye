"""Exercise the built wheel outside the checkout, optionally using cached wheels."""

import argparse
import os
import subprocess
import tempfile
import venv
from pathlib import Path

from packaging.utils import parse_wheel_filename


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="Exact artifact to test; defaults to the highest version in dist/")
    parser.add_argument("--wheelhouse", type=Path, help="Install offline from this directory of dependency wheels")
    args = parser.parse_args()
    wheels = list(Path("dist").glob("fisheye-*.whl"))
    if not args.wheel and not wheels:
        parser.error("Build a wheel first or supply --wheel")
    wheel = (args.wheel or max(wheels, key=lambda p: parse_wheel_filename(p.name)[1])).resolve()
    if not wheel.is_file():
        parser.error("Wheel does not exist")
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1")
    if args.wheelhouse:
        env.update(PIP_NO_INDEX="1", PIP_FIND_LINKS=str(args.wheelhouse.resolve()))

    with tempfile.TemporaryDirectory(prefix="fisheye-wheel-") as tmp:
        target = Path(tmp)
        venv.create(target / "venv", with_pip=True)
        python = target / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

        def run(*command):
            subprocess.run([str(python), *command], cwd=target, env=env, check=True, stdout=subprocess.DEVNULL)

        run("-m", "pip", "install", str(wheel))
        run(
            "-c",
            "import importlib.util, sys; from pathlib import Path; import fisheye; "
            "assert Path(fisheye.__file__).is_relative_to(Path(sys.prefix)); "
            'assert importlib.util.find_spec("fastapi") is None; '
            'from fisheye.cli.main import main; raise SystemExit(main(["evaluate","--split","all"]))',
        )
        run("-m", "pip", "install", str(wheel) + "[server]")
        run(
            "-c",
            """
import asyncio
from importlib.metadata import version
from importlib.resources import files
from fisheye.api.app import create_app
from fisheye.cli.main import main

async def check():
    app = create_app()
    assert app.version == version("fisheye")
    assert any(route.path == "/v2/audit" for route in app.routes)
    for template in ("dashboard.html", "investigation.html"):
        assert files("fisheye").joinpath("templates", template).is_file()
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        await runtime.publish(dict(event_type="agent.start", agent_id="wheel", run_id="smoke"))
        await runtime.drain(5)
        assert (await runtime.store.journal_metrics())["pending"] == 0
    assert runtime._closed

asyncio.run(check())
raise SystemExit(main(["evaluate", "--split", "all"]))
""",
        )
    print("Installed wheel imports, templates, server lifecycle, CLI, and offline evaluation passed.")


if __name__ == "__main__":
    main()
