"""Local SQLite query-plan experimentation API."""
import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from engine import CATALOG, compare

ROOT = Path(__file__).resolve().parent
ASSETS = {'/': ('index.html', 'text/html'), '/style.css': ('style.css', 'text/css'), '/app.js': ('app.js', 'text/javascript')}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass

    def reply(self, status, data, mime='application/json'):
        body = data if isinstance(data, bytes) else json.dumps(data, allow_nan=False).encode('utf-8')
        self.send_response(status)
        for key, value in {'Content-Type': mime+'; charset=utf-8', 'Content-Length': str(len(body)),
                           'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                           'Referrer-Policy': 'no-referrer',
                           'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def trusted(self):
        hosts = self.headers.get_all('Host', [])
        if len(hosts) != 1 or hosts[0].lower() not in {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}:
            self.reply(400, {'error': 'Use this server’s loopback address.'})
            return False
        origin = self.headers.get('Origin')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' or origin is not None and origin != 'http://' + hosts[0].lower():
            self.reply(403, {'error': 'Cross-site requests are not allowed.'})
            return False
        return True

    def do_GET(self):
        if not self.trusted():
            return
        path = urlsplit(self.path).path
        if path in ASSETS:
            name, mime = ASSETS[path]
            return self.reply(200, (ROOT/name).read_bytes(), mime)
        if path == '/api/catalog':
            return self.reply(200, CATALOG)
        self.reply(404, {'error': 'Route not found.'})

    do_HEAD = do_GET

    def do_POST(self):
        if not self.trusted():
            return
        try:
            sizes = self.headers.get_all('Content-Length', [])
            if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding') or len(sizes) != 1 or not re.fullmatch(r'[0-9]{1,6}', sizes[0]):
                raise ValueError('Send JSON with one bounded Content-Length and no transfer encoding.')
            size = int(sizes[0])
            if size > 128000:
                raise ValueError('Request exceeds 128 KB.')
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError('Incomplete request body.')
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError('Duplicate JSON field.')
                    result[key] = value
                return result
            def nonfinite(_):
                raise ValueError('JSON numbers must be finite.')
            data = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=nonfinite)
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object.')
            path = urlsplit(self.path).path
            if path == '/api/compare':
                return self.reply(200, compare(data))
            self.reply(404, {'error': 'Route not found.'})
        except (ValueError, UnicodeError, RecursionError) as error:
            self.reply(400, {'error': 'JSON nesting is too deep.' if isinstance(error, RecursionError) else str(error)})
        except TimeoutError:
            self.reply(408, {'error': 'Request body timed out.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Query Quarry: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
