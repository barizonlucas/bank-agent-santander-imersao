"""HTML pages with cache-busted asset URLs.

Provenance: Cloudflare caches `/static/*.js|css` at the edge for 4 hours (`max-age=14400`). After a
deploy, a stale `app.js` kept being served next to the new `index.html` and the chat showed
"Erro de comunicação". Referencing every asset as `/static/<file>?v=<content hash>` gives each
deploy new cache keys, so neither the edge nor the browser can serve an outdated file.
"""

import hashlib
import re
from pathlib import Path

_ASSET_URL = re.compile(r'(/static/[\w.-]+\.(?:css|js))(?=["\'])')


def compute_static_version(static_dir: Path) -> str:
    """Short hash of the content of every file under `static_dir` (changes when any asset changes)."""
    digest = hashlib.sha256()
    for file in sorted(static_dir.rglob("*")):
        if file.is_file():
            digest.update(file.relative_to(static_dir).as_posix().encode())
            digest.update(file.read_bytes())
    return digest.hexdigest()[:8]


def render_page(html_file: Path, version: str) -> str:
    """Return the page with `?v=<version>` appended to its /static CSS and JS URLs."""
    return _ASSET_URL.sub(rf"\1?v={version}", html_file.read_text(encoding="utf-8"))
