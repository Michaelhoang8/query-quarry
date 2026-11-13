import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from run import Handler
from engine import EXAMPLES


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, data=None, headers=None, raw=None, method=None):
        conn = HTTPConnection(*self.server.server_address, timeout=3)
        conn.request(method or ('POST' if data is not None or raw is not None else 'GET'), path,
                     body=raw if raw is not None else json.dumps(data) if data is not None else None,
                     headers={'Content-Type': 'application/json', **(headers or {})})
        response = conn.getresponse()
        body = response.read()
        result = response.status, json.loads(body) if body else None, response.headers
        conn.close()
        return result

    def test_query_comparison_contract(self):
        status, result, _ = self.request('/api/compare', EXAMPLES['customer'])
        self.assertEqual(status, 200)
        self.assertTrue(result['comparison']['ordered_preview_equal'])
        status, result, _ = self.request('/api/compare', {**EXAMPLES['customer'], 'sql': 'DELETE FROM orders'})
        self.assertEqual(status, 200)
        self.assertIsNotNone(result['baseline']['error'])

    def test_rejects_unknown_fields_invalid_types_and_malformed_json(self):
        for payload in [{'sql': []}, {'parameters': True}, {**EXAMPLES['customer'], 'extra': 1}]:
            self.assertEqual(self.request('/api/compare', payload)[0], 400)
        for raw in ['[]', '{"x":1,"x":2}', '{"x":NaN}', '['*1500, '{']:
            self.assertEqual(self.request('/api/compare', raw=raw)[0], 400)

    def test_browser_boundaries_and_no_filesystem_routes(self):
        self.assertEqual(self.request('/api/catalog', headers={'Host': 'evil.invalid'})[0], 400)
        self.assertEqual(self.request('/api/catalog', headers={'Origin': 'https://evil.invalid'})[0], 403)
        self.assertEqual(self.request('/api/catalog', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        for path in ['/.git/config', '/engine.py', '/../run.py']:
            self.assertEqual(self.request(path)[0], 404)
        status, body, headers = self.request('/api/catalog', method='HEAD')
        self.assertEqual(status, 200)
        self.assertIsNone(body)
        self.assertIn("object-src 'none'", headers['Content-Security-Policy'])


if __name__ == '__main__':
    unittest.main()
