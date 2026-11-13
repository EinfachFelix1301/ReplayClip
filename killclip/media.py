"""Winziger lokaler HTTP-Server, damit die Oberfläche Clips/Musik abspielen und spulen kann.

Nur 127.0.0.1, zufälliges Token, und nur Dateien, die vorher freigegeben wurden.
"""
import hashlib
import mimetypes
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
_TYPES = {".mp4": "video/mp4", ".mkv": "video/x-matroska", ".mov": "video/quicktime", ".mp3": "audio/mpeg",
          ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav", ".ogg": "audio/ogg", ".flac": "audio/flac"}


class MediaServer:
    def __init__(self):
        self.token = secrets.token_urlsafe(16)
        self.files: dict[str, Path] = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                parts = self.path.split("?")[0].strip("/").split("/")
                path = server.files.get(parts[1].split(".")[0]) if len(parts) == 2 and parts[0] == server.token else None
                if not path or not path.is_file():
                    self.send_error(404)
                    return
                size = path.stat().st_size
                start, end, status = 0, size - 1, 200
                m = _RANGE.match(self.headers.get("Range", ""))
                if m and (m[1] or m[2]):
                    if m[1]:
                        start = int(m[1])
                        end = min(int(m[2]), size - 1) if m[2] else size - 1
                    else:
                        start = max(0, size - int(m[2]))
                    if start > end:
                        self.send_error(416)
                        return
                    status = 206
                self.send_response(status)
                self.send_header("Content-Type", _TYPES.get(path.suffix.lower())
                                 or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                try:
                    with path.open("rb") as fh:
                        fh.seek(start)
                        left = end - start + 1
                        while left > 0:
                            chunk = fh.read(min(262144, left))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                except (ConnectionError, OSError):
                    pass  # Player hat beim Spulen abgebrochen

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path) -> str:
        path = Path(path).resolve()
        key = hashlib.sha1(str(path).encode()).hexdigest()[:20]
        self.files[key] = path
        return f"http://127.0.0.1:{self.httpd.server_port}/{self.token}/{key}{path.suffix.lower()}"

    def close(self):
        self.httpd.shutdown()
