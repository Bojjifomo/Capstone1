"""Guards on the local server: token, Host header, and folder escapes."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["LP_SCRIPTS_DIR"] = cls.tmp
        with open(os.path.join(cls.tmp, "RH Pool.js"), "w") as f:
            f.write("// pool\n")
        with open(os.path.join(cls.tmp, "RH Secrets.js"), "w") as f:
            f.write("module.exports = {};\n")
        server.LOCAL = os.path.join(cls.tmp, "local")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        server.Handler.port = cls.srv.server_address[1]
        cls.base = "http://127.0.0.1:%d" % server.Handler.port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path, token=True, host=None):
        r = urllib.request.Request(self.base + path)
        if token:
            r.add_header("X-LP-Token", server.TOKEN)
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_lists_scripts_but_not_secrets(self):
        code, j = self.get("/api/scripts")
        self.assertEqual(code, 200)
        self.assertEqual(j["scripts"], ["RH Pool"])
        self.assertTrue(j["hasSecrets"])

    def test_api_needs_token(self):
        self.assertEqual(self.get("/api/script?name=RH%20Secrets", token=False)[0], 403)
        self.assertEqual(self.get("/api/script?name=RH%20Secrets")[0], 200)

    def test_rejects_foreign_host(self):
        # A DNS-rebinding page would arrive with its own host name.
        self.assertEqual(self.get("/api/scripts", host="evil.example:%d" % server.Handler.port)[0], 403)

    def test_fs_cannot_escape(self):
        code, j = self.get("/api/fs?op=read&path=/icloud/../../etc/passwd")
        self.assertEqual(code, 500)
        code, j = self.get("/api/fs?op=read&path=/nowhere/x")
        self.assertEqual(code, 500)

    def test_script_name_cannot_be_a_path(self):
        self.assertEqual(self.get("/api/script?name=../server")[0], 404)

    def test_page_has_token(self):
        with urllib.request.urlopen(self.base + "/") as resp:
            self.assertIn(server.TOKEN, resp.read().decode())


if __name__ == "__main__":
    unittest.main()
