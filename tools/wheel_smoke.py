"""Exercise the built wheel outside the source checkout."""

import subprocess
import tempfile
import venv
from pathlib import Path

wheel = sorted(Path("dist").glob("fisheye-*.whl"))[-1].resolve()
with tempfile.TemporaryDirectory(prefix="fisheye-wheel-") as tmp:
    target = Path(tmp)
    venv.create(target / "venv", with_pip=True)
    python = target / "venv" / "bin" / "python"
    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(
        [
            str(python),
            "-c",
            'import importlib.util; assert importlib.util.find_spec("fastapi") is None; '
            'from fisheye.cli.main import main; raise SystemExit(main(["evaluate","--split","all"]))',
        ],
        cwd=target,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", str(wheel) + "[server]"], check=True, stdout=subprocess.DEVNULL
    )
    subprocess.run(
        [
            str(python),
            "-c",
            "from importlib.resources import files; from fisheye import FisheyeConfig; "
            'assert files("fisheye").joinpath("templates/dashboard.html").is_file(); '
            "from fisheye.api.app import create_app; from fisheye.cli.main import main; "
            'raise SystemExit(main(["evaluate","--split","all"]))',
        ],
        cwd=target,
        check=True,
        stdout=subprocess.DEVNULL,
    )
print("Installed wheel imports, templates, CLI, and offline evaluation passed.")
