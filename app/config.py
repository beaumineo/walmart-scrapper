"""Runtime config: proxy pool + collector settings.

Proxy format inspired by triposat/walmart-price-monitor:
  PROXIES env = one proxy per line as host:port:user:password
Also supports WALMART_PROXY_SERVER / USER / PASS for a single proxy.
"""
from __future__ import annotations

import os
import random
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
# Bundled read-only assets (zips, store CSV) stay in repo data/
DATA_DIR = ROOT / "data"
ENV_PATH = ROOT / ".env"


def is_vercel() -> bool:
    return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))


def is_serverless() -> bool:
    """Vercel / similar — ephemeral FS, short timeouts; prefer reliable sample mode."""
    return is_vercel() or bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def writable_data_dir() -> Path:
    """Writable dir for caches/pulls/db ( /tmp on Vercel )."""
    if is_serverless():
        path = Path(os.environ.get("WALMART_TMP_DIR") or "/tmp/walmart-data")
        path.mkdir(parents=True, exist_ok=True)
        return path
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


PULLS_DIR = writable_data_dir() / "pulls"


def _load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


_load_dotenv()
PULLS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ProxyConfig:
    host: str
    port: str
    user: str = ""
    password: str = ""

    @property
    def url(self) -> str:
        if self.user:
            return (
                f"http://{quote(self.user, safe='')}:{quote(self.password, safe='')}@"
                f"{self.host}:{self.port}"
            )
        return f"http://{self.host}:{self.port}"

    def playwright_dict(self) -> dict:
        d = {"server": f"http://{self.host}:{self.port}"}
        if self.user:
            d["username"] = self.user
            d["password"] = self.password
        return d

    def with_session(self, session_id: Optional[str] = None) -> "ProxyConfig":
        """Bright Data-style sticky session: append -session-ID to username."""
        if not self.user:
            return self
        sid = session_id or uuid.uuid4().hex[:12]
        # Strip any existing session suffix
        base = re.sub(r"-session-[A-Za-z0-9_-]+$", "", self.user)
        # Prefer US exits for Walmart (Bright Data username flag)
        if "brd-customer" in base and "-country-" not in base:
            base = f"{base}-country-us"
        return ProxyConfig(
            host=self.host,
            port=self.port,
            user=f"{base}-session-{sid}",
            password=self.password,
        )


def _parse_proxy_line(line: str) -> Optional[ProxyConfig]:
    line = line.strip()
    if not line:
        return None
    # host:port:user:pass (password may contain ':')
    parts = line.split(":", 3)
    if len(parts) == 4:
        host, port, user, password = parts
        return ProxyConfig(host=host, port=port, user=user, password=password)
    if len(parts) == 2:
        return ProxyConfig(host=parts[0], port=parts[1])
    # http://user:pass@host:port
    if "://" in line:
        from urllib.parse import urlparse

        u = urlparse(line)
        if not u.hostname or not u.port:
            return None
        return ProxyConfig(
            host=u.hostname,
            port=str(u.port),
            user=u.username or "",
            password=u.password or "",
        )
    return None


def load_proxy_pool() -> List[ProxyConfig]:
    proxies: List[ProxyConfig] = []
    raw = os.environ.get("PROXIES", "").strip()
    if raw:
        for line in raw.splitlines():
            p = _parse_proxy_line(line)
            if p:
                proxies.append(p)

    single = os.environ.get("WALMART_PROXY_SERVER") or ""
    if single:
        # Allow full URL or host:port with separate user/pass
        if "://" in single or single.count(":") >= 1:
            if single.count(":") >= 3 and "://" not in single:
                p = _parse_proxy_line(single)
            else:
                from urllib.parse import urlparse

                if "://" not in single:
                    single = "http://" + single
                u = urlparse(single)
                p = ProxyConfig(
                    host=u.hostname or "",
                    port=str(u.port or 80),
                    user=u.username
                    or os.environ.get("WALMART_PROXY_USERNAME")
                    or "",
                    password=u.password
                    or os.environ.get("WALMART_PROXY_PASSWORD")
                    or "",
                )
            if p and p.host:
                proxies.append(p)

    # de-dupe by url
    seen = set()
    out = []
    for p in proxies:
        if p.url in seen:
            continue
        seen.add(p.url)
        out.append(p)
    return out


@dataclass
class CollectorConfig:
    proxies: List[ProxyConfig] = field(default_factory=list)
    max_retries: int = 1
    timeout_sec: float = 20.0
    max_per_query: int = 40
    queries: tuple = ("clearance", "rollback", "special buy")
    # auto | oxylabs | scraperapi | unlocker | curl | playwright | uc
    collect_engine: str = "auto"
    rotate_session: bool = False
    min_delay_sec: float = 3.0
    max_delay_sec: float = 8.0
    stop_on_challenge: bool = True
    # Bright Data Web Unlocker (not ISP proxy)
    unlocker_api_key: str = ""
    unlocker_zone: str = ""
    unlocker_country: str = "us"
    unlocker_proxy: Optional[ProxyConfig] = None
    # Commercial Walmart scrapers (reliable vs Akamai)
    scraperapi_key: str = ""
    scraperapi_ultra: bool = True
    oxylabs_username: str = ""
    oxylabs_password: str = ""
    uc_enabled: bool = False
    uc_headless: bool = True
    allow_browser: bool = False

    @property
    def proxy_enabled(self) -> bool:
        return bool(self.proxies)

    @property
    def scraperapi_enabled(self) -> bool:
        return bool(self.scraperapi_key)

    @property
    def oxylabs_enabled(self) -> bool:
        return bool(self.oxylabs_username and self.oxylabs_password)

    @property
    def client_live_configured(self) -> bool:
        """Non-browser backends the client module can use."""
        return bool(
            self.oxylabs_enabled
            or self.scraperapi_enabled
            or (self.unlocker_api_key and self.unlocker_zone)
            or self.unlocker_proxy
            or self.proxies
        )

    def next_proxy(self) -> Optional[ProxyConfig]:
        if not self.proxies:
            return None
        base = random.choice(self.proxies)
        if self.rotate_session:
            return base.with_session()
        return base

    def proxy_cycle(self) -> Optional[Iterator[ProxyConfig]]:
        if not self.proxies:
            return None

        def _gen() -> Iterator[ProxyConfig]:
            while True:
                p = self.next_proxy()
                if p is None:
                    return
                yield p

        return _gen()

    def first_playwright_proxy(self) -> Optional[dict]:
        p = self.next_proxy()
        return p.playwright_dict() if p else None


def get_collector_config() -> CollectorConfig:
    queries_raw = os.environ.get("WALMART_QUERIES", "").strip()
    queries = (
        tuple(q.strip() for q in queries_raw.split(",") if q.strip())
        if queries_raw
        else ("clearance",)
    )
    rotate_raw = os.environ.get("WALMART_PROXY_ROTATE_SESSION", "1").strip().lower()
    rotate = rotate_raw in ("1", "true", "yes", "on")
    proxies = load_proxy_pool()
    # Bright Data single gateway: always rotate session (triposat next-proxy equivalent)
    if proxies and any("brd-customer" in (p.user or "") for p in proxies):
        rotate = True
    unlocker_proxy_raw = (
        os.environ.get("BRIGHTDATA_UNLOCKER_PROXY")
        or os.environ.get("WALMART_UNLOCKER_PROXY")
        or ""
    ).strip()
    unlocker_proxy = _parse_proxy_line(unlocker_proxy_raw) if unlocker_proxy_raw else None

    return CollectorConfig(
        proxies=proxies,
        max_retries=int(os.environ.get("WALMART_MAX_RETRIES", "3")),
        timeout_sec=float(os.environ.get("WALMART_TIMEOUT_SEC", "35")),
        max_per_query=int(os.environ.get("WALMART_MAX_PER_QUERY", "40")),
        queries=queries,
        collect_engine=os.environ.get("WALMART_COLLECT_ENGINE", "auto").lower(),
        rotate_session=rotate,
        min_delay_sec=float(os.environ.get("WALMART_MIN_DELAY_SEC", "3")),
        max_delay_sec=float(os.environ.get("WALMART_MAX_DELAY_SEC", "7")),
        stop_on_challenge=os.environ.get("WALMART_STOP_ON_CHALLENGE", "0")
        .strip()
        .lower()
        in ("1", "true", "yes", "on"),
        unlocker_api_key=(
            os.environ.get("BRIGHTDATA_API_KEY")
            or os.environ.get("WALMART_UNLOCKER_API_KEY")
            or ""
        ).strip(),
        unlocker_zone=(
            os.environ.get("BRIGHTDATA_UNLOCKER_ZONE")
            or os.environ.get("WALMART_UNLOCKER_ZONE")
            or ""
        ).strip(),
        unlocker_country=(
            os.environ.get("BRIGHTDATA_UNLOCKER_COUNTRY")
            or os.environ.get("WALMART_UNLOCKER_COUNTRY")
            or "us"
        )
        .strip()
        .lower()
        or "us",
        unlocker_proxy=unlocker_proxy,
        scraperapi_key=(
            os.environ.get("SCRAPERAPI_KEY") or os.environ.get("SCRAPER_API_KEY") or ""
        ).strip(),
        scraperapi_ultra=os.environ.get("SCRAPERAPI_ULTRA", "1")
        .strip()
        .lower()
        in ("1", "true", "yes", "on"),
        oxylabs_username=(
            os.environ.get("OXYLABS_USERNAME") or os.environ.get("OXYLABS_USER") or ""
        ).strip(),
        oxylabs_password=(
            os.environ.get("OXYLABS_PASSWORD") or os.environ.get("OXYLABS_PASS") or ""
        ).strip(),
        uc_enabled=os.environ.get("WALMART_UC_ENABLED", "0")
        .strip()
        .lower()
        in ("1", "true", "yes", "on"),
        uc_headless=os.environ.get("WALMART_UC_HEADLESS", "1")
        .strip()
        .lower()
        in ("1", "true", "yes", "on"),
        allow_browser=os.environ.get("WALMART_ALLOW_BROWSER", "0")
        .strip()
        .lower()
        in ("1", "true", "yes", "on"),
    )
