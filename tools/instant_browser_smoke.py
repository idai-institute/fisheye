"""Exercise Instant in Chromium against a real local server.

Install playwright and its Chromium browser before running. Artifacts go to /tmp.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def main():
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="instant-browser-") as directory:
        data = Path(directory)
        cfg = {
            "storage": {
                "sqlite_path": str(data / "events.db"),
                "events_jsonl_path": str(data / "events.jsonl"),
                "alerts_jsonl_path": str(data / "alerts.jsonl"),
            }
        }
        code = (
            "import uvicorn; from fisheye import FisheyeConfig; from fisheye_instant.app import create_app; "
            f"uvicorn.run(create_app(config=FisheyeConfig.model_validate_json({json.dumps(cfg)!r}),demo=True),host='127.0.0.1',port={port})"
        )
        env = {key: value for key, value in os.environ.items() if not key.startswith("FISHEYE__")}
        server = subprocess.Popen(
            [sys.executable, "-c", code], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    with urlopen(base + "/v1/health", timeout=1):
                        break
                except (URLError, TimeoutError):
                    if server.poll() is not None:
                        raise RuntimeError("Instant server exited during startup")
                    time.sleep(0.1)
            else:
                raise RuntimeError("Instant server did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1080})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                expect(page.locator("#empty-state")).to_be_visible()
                page.locator("#demo-button").click()
                expect(page.locator("#dashboard-content")).to_be_visible()
                page.wait_for_function("Number(document.querySelector('#score-value').textContent) >= 60")
                expect(page.locator(".signal").first).to_be_visible()
                page.locator('[data-tab="rules"]').click()
                page.locator("#add-rule").click()
                page.locator('#rule-form [name="name"]').fill("Stop demo at high risk")
                page.locator('#rule-form [name="action"]').select_option("shutdown")
                page.locator('#rule-form [name="target"]').select_option(
                    json.dumps(["research-demo", "demo"], separators=(",", ":"))
                )
                page.locator('#rule-form [name="threshold"]').fill("85")
                page.locator('#rule-form [type="submit"]').click()
                expect(page.locator("#rule-dialog")).not_to_be_visible()
                expect(page.get_by_role("heading", name="Stop demo at high risk")).to_be_visible()
                page.locator('[data-tab="overview"]').click()
                page.locator("#demo-button").click()
                expect(page.locator("#demo-button")).to_have_text("Demo agent stopped", timeout=10000)
                expect(page.get_by_text("Stop demo at high risk").first).to_be_visible()
                page.screenshot(path="/tmp/instant-desktop.png", full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path="/tmp/instant-mobile.png", full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile page overflows"
                page.reload()
                page.locator('[data-tab="rules"]').click()
                expect(page.get_by_role("heading", name="Stop demo at high risk")).to_be_visible()
                assert not errors, errors
                browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    print("Instant browser navigation, demo, rule creation, shutdown, persistence and mobile overflow checks passed.")


if __name__ == "__main__":
    main()
