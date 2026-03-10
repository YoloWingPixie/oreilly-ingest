import json
import time
from pathlib import Path

import requests

import config

# Hit /profile/ this often so server keeps sending full content (truncation starts after ~15 min)
KEEPALIVE_INTERVAL = 5 * 60  # 5 minutes


class HttpClient:
    def __init__(self, cookies_file: Path | None = None):
        self.session = requests.Session()
        self.session.headers.update(config.HEADERS)
        self.last_request_time = 0
        self.last_keepalive_time = 0

        self._cookies_path = cookies_file or config.COOKIES_FILE
        if self._cookies_path.exists():
            self._load_cookies(self._cookies_path)

    def _load_cookies(self, path: Path):
        try:
            with open(path) as f:
                cookies = json.load(f)
            if isinstance(cookies, dict):
                for name, value in cookies.items():
                    self.session.cookies.set(name, value, domain=".oreilly.com")
        except (json.JSONDecodeError, ValueError):
            pass  # Empty or invalid file, skip loading

    def _save_cookies(self):
        """Persist session cookies to file so server-refreshed cookies survive."""
        try:
            jar = self.session.cookies
            data = {c.name: c.value for c in jar if "oreilly" in getattr(c, "domain", "")}
            if data:
                self._cookies_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._cookies_path, "w") as f:
                    json.dump(data, f, indent=2)
        except Exception:
            pass

    def _rate_limit(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < config.REQUEST_DELAY:
            time.sleep(config.REQUEST_DELAY - elapsed)
        self.last_request_time = time.time()

    def _keepalive_if_due(self):
        """Hit /profile/ every 5 min so the server keeps sending full content (no truncation)."""
        now = time.time()
        if now - self.last_keepalive_time >= KEEPALIVE_INTERVAL:
            self.last_keepalive_time = now
            try:
                r = self.session.get(
                    config.BASE_URL + "/profile/",
                    timeout=config.REQUEST_TIMEOUT,
                    allow_redirects=False,
                )
                if r.status_code == 200:
                    self._save_cookies()  # persist any refreshed cookies from server
            except Exception:
                pass  # Don't fail the app if keepalive fails

    def get(self, url: str, **kwargs) -> requests.Response:
        self._rate_limit()
        self._keepalive_if_due()
        if not url.startswith("http"):
            url = config.BASE_URL + url
        kwargs.setdefault("timeout", config.REQUEST_TIMEOUT)
        response = self.session.get(url, **kwargs)
        # If session expired, reload cookies (e.g. user refreshed in browser) and retry once
        if response.status_code in (401, 403):
            self.reload_cookies()
            self._rate_limit()
            response = self.session.get(url, **kwargs)
        return response

    def get_json(self, url: str, **kwargs) -> dict:
        response = self.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str, **kwargs) -> str:
        response = self.get(url, **kwargs)
        response.raise_for_status()
        return response.text

    def get_bytes(self, url: str, **kwargs) -> bytes:
        response = self.get(url, **kwargs)
        response.raise_for_status()
        return response.content

    def reload_cookies(self):
        """Clear and reload cookies from file. Used after browser login."""
        self.session.cookies.clear()
        if self._cookies_path.exists():
            self._load_cookies(self._cookies_path)
