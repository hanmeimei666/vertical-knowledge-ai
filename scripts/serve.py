"""Authenticated read-only loopback API; CLI alone can write, review or release."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from urllib.parse import parse_qs, urlsplit

from pipeline import Pipeline, WorkflowError, ROOT
from platform_store import Platform


def handler(root, config, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not put queries, bearer tokens, or evidence in access logs.

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send(405, {"error": "Read-only API; use local CLI for controlled writes"})

        def do_GET(self):
            parts = urlsplit(self.path)
            if parts.path == "/health":
                return self.send(200, {"status": "alive", "access": "loopback_read_only"})
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                return self.send(401, {"error": "Unauthorized"})
            if len(self.path) > 6000:
                return self.send(400, {"error": "Request too long"})
            params = parse_qs(parts.query)
            try:
                candidate=Pipeline(root=root,config=config)
                generic=bool(candidate.records('KnowledgeUnit'))
                if generic:
                    candidate.db.close()
                    candidate=Platform(root=root,config=config)
                with candidate as app:
                    if parts.path == "/search":
                        query = params.get("q", [""])[0]
                        if not 1 <= len(query.strip()) <= 2000:
                            return self.send(400, {"error": "Query must contain 1..2000 characters"})
                        filters=json.loads(params.get('filters',['{}'])[0])
                        result=app.retrieve(query,filters,params.get('mode',['AND'])[0]) if generic else app.evidence(query)
                        return self.send(200, result)
                    if parts.path.startswith("/runs/"):
                        run_id = parts.path.removeprefix("/runs/")
                        run = app.get_run(run_id)
                        if run["state"] == "invalidated":
                            # Historical evidence is available to the local owner for audit,
                            # not to service callers after a source/permission change.
                            run = {k: run[k] for k in ("id", "state", "error", "created", "updated")}
                        return self.send(200, run)
                    if parts.path == "/artifact":
                        path = app.artifact(params.get("id", [""])[0], params.get("name", [""])[0], params.get("release", ["false"])[0] == "true")
                        types = {".html": "text/html; charset=utf-8", ".md": "text/plain; charset=utf-8", ".json": "application/json; charset=utf-8"}
                        return self.send(200, path.read_bytes(), types[path.suffix])
                self.send(404, {"error": "Unknown route"})
            except WorkflowError:
                self.send(409, {"error": "Record unavailable, invalidated or not permitted"})
            except (ValueError, OSError, KeyError):
                self.send(500, {"error": "Local configuration/data error"})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/runtime.json")
    args = parser.parse_args()
    with Pipeline(config=args.config) as app:
        service = app.config["service"]
    token = os.environ.get(service["token_env"], "")
    if len(token) < 24:
        raise SystemExit("Set the service token environment variable to at least 24 random characters")
    server = HTTPServer((service["host"], service["port"]), handler(ROOT, args.config, token))
    print(f"Read-only API listening on http://{service['host']}:{service['port']}; Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
