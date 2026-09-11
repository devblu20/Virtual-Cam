"""Start the real server on a temporary localhost port, with fake credentials."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class StartupTests(unittest.TestCase):
    def test_port_and_real_http_auth(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = os.environ.copy()
        env.update({
            "PORT": str(port),
            "DECART_API_KEY": "fake-key-never-use",
            "VCAM_ACCESS_KEYS": json.dumps({
                "smoke": hashlib.sha256(b"test-only-startup-key-never-issued-123456").hexdigest()
            }),
        })
        process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=Path(__file__).resolve().parents[1], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 20
            while True:
                try:
                    with urlopen(url + "/api/health", timeout=1) as response:
                        self.assertTrue(json.load(response)["configured"])
                    break
                except URLError:
                    if time.monotonic() >= deadline or process.poll() is not None:
                        self.fail("Server did not become healthy on the PORT environment variable.")
                    time.sleep(0.1)
            with urlopen(url, timeout=2) as response:
                    self.assertIn(b"Virtual CAM | Bluqq", response.read())
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(url + "/api/realtime-token", method="POST"), timeout=2)
            self.assertEqual(caught.exception.code, 401)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
