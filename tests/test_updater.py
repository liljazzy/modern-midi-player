import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from midiplayer import updater  # noqa: E402

PAYLOAD = os.urandom(300_000)
NAME = "ModernMidiPlayer-Setup-9.1.0.exe"


class Handler(http.server.BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):
        body = self.routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        if callable(body):
            body = body()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class UpdaterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def release(self, tag="v9.1.0", sha=None):
        sha = sha or hashlib.sha256(PAYLOAD).hexdigest()
        Handler.routes = {
            "/repos/me/app/releases/latest": json.dumps({
                "tag_name": tag, "body": "## New\n- stuff", "html_url": "https://example/r",
                "published_at": "2026-10-01T00:00:00Z",
                "assets": [
                    {"name": NAME, "size": len(PAYLOAD), "browser_download_url": self.base + "/dl/" + NAME},
                    {"name": NAME + ".sha256", "size": 90, "browser_download_url": self.base + "/dl/sha"},
                ]}).encode(),
            "/dl/" + NAME: PAYLOAD,
            "/dl/sha": f"{sha}  {NAME}\n".encode(),
        }

    def test_versions(self):
        self.assertTrue(updater.is_newer("v1.10.0", "1.9.9"))
        self.assertTrue(updater.is_newer("1.1", "1.0.9"))
        self.assertFalse(updater.is_newer("1.0.0", "1.0.0"))
        self.assertTrue(updater.is_newer("1.2.0", "1.2.0-beta.2"))
        self.assertFalse(updater.is_newer("1.2.0-rc1", "1.2.0"))
        self.assertFalse(updater.is_newer("garbage", "1.0.0"))

    def test_check_and_download(self):
        self.release()
        info = updater.check("me/app", api_base=self.base, current="1.1.0")
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "9.1.0")
        self.assertEqual(info.installer_name, NAME)
        self.assertTrue(info.sha256_url)
        seen = []
        with tempfile.TemporaryDirectory() as d:
            p = updater.download(info, d, progress=lambda a, b: seen.append((a, b)))
            with open(p, "rb") as f:
                self.assertEqual(f.read(), PAYLOAD)
        self.assertEqual(seen[-1], (len(PAYLOAD), len(PAYLOAD)))

    def test_up_to_date(self):
        self.release()
        self.assertIsNone(updater.check("me/app", api_base=self.base, current="9.1.0"))

    def test_checksum_mismatch(self):
        self.release(sha="0" * 64)
        info = updater.check("me/app", api_base=self.base, current="1.0.0")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(updater.UpdateError):
                updater.download(info, d)
            self.assertEqual(os.listdir(d), [])

    def test_cancel(self):
        self.release()
        info = updater.check("me/app", api_base=self.base, current="1.0.0")
        ev = threading.Event()
        ev.set()
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(updater.UpdateError):
                updater.download(info, d, cancel=ev)

    def test_errors(self):
        Handler.routes = {}
        with self.assertRaises(updater.UpdateError) as cm:
            updater.fetch_latest("me/app", api_base=self.base)
        self.assertIn("No releases", str(cm.exception))
        with self.assertRaises(updater.UpdateError):
            updater.fetch_latest("me/app", api_base="http://127.0.0.1:9", timeout=2)
        with self.assertRaises(updater.UpdateError):
            updater.fetch_latest("")

    def test_not_installed_copy(self):
        self.assertIsNone(updater.install_dir())
        self.assertFalse(updater.can_self_update())


if __name__ == "__main__":
    unittest.main()
