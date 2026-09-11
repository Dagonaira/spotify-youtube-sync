"""Runs the Crossplay backend as a plain web server, with no desktop window -
for reaching it from another device (e.g. a phone, as an installed PWA) over
the local network or through a tunnel. Same FastAPI app and job manager as
desktop.py; this just skips pywebview and binds to a fixed, predictable port
instead of a random one, since a tunnel needs a stable target.

Usage (from the project root): python serve_pwa.py
Then, in another terminal, expose it with a free Cloudflare tunnel:
    cloudflared tunnel --url http://localhost:8765
which prints a https://<random>.trycloudflare.com URL - open that on the
phone and use "Add to Home Screen" to install it.
"""

import uvicorn

from app.server import app

PORT = 8765

if __name__ == "__main__":
    print(f"Crossplay running at http://localhost:{PORT}  (Ctrl+C to stop)")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
