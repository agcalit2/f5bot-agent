from __future__ import annotations

import asyncio
import re
from collections import deque

import httpx


_PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://www.proxy-list.download/api/v1/get?type=https",
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
]

_VALIDATION_URL = "https://www.reddit.com/robots.txt"
_VALIDATION_TIMEOUT = 6.0
_VALIDATION_BATCH = 50
_REFRESH_THRESHOLD = 5
_IP_PORT_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}$")


class ProxyPool:
    def __init__(self) -> None:
        self._live: deque[str] = deque()
        self._dead: set[str] = set()
        self._lock = asyncio.Lock()
        self._refreshing = False

    async def get_proxy(self) -> str | None:
        async with self._lock:
            if len(self._live) < _REFRESH_THRESHOLD and not self._refreshing:
                self._refreshing = True
                asyncio.create_task(self._refresh())
            if not self._live:
                # Pool is empty — wait for the in-flight refresh
                pass
        if not self._live:
            await self._wait_for_refresh()
        async with self._lock:
            if not self._live:
                return None
            proxy = self._live.popleft()
            self._live.append(proxy)
            return proxy

    async def mark_dead(self, proxy: str) -> None:
        async with self._lock:
            self._dead.add(proxy)
            try:
                self._live.remove(proxy)
            except ValueError:
                pass

    async def _wait_for_refresh(self, timeout: float = 90.0) -> None:
        waited = 0.0
        step = 0.5
        while waited < timeout:
            if self._live:
                return
            await asyncio.sleep(step)
            waited += step

    async def _refresh(self) -> None:
        try:
            candidates = await self._fetch_all_sources()
            candidates = [c for c in candidates if c not in self._dead]
            print(f"[proxy_pool] validating {len(candidates)} candidates...")
            live = await self._validate_many(candidates)
            async with self._lock:
                for p in live:
                    if p not in self._live:
                        self._live.append(p)
            print(f"[proxy_pool] refresh done: {len(live)} live / {len(candidates)} checked")
        finally:
            self._refreshing = False

    async def _fetch_all_sources(self) -> list[str]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            results = await asyncio.gather(
                *(self._fetch_one(client, url) for url in _PROXY_SOURCES),
                return_exceptions=True,
            )
        seen: set[str] = set()
        out: list[str] = []
        for r in results:
            if isinstance(r, Exception):
                continue
            for line in r:
                line = line.strip()
                if _IP_PORT_RE.match(line):
                    proxy = f"http://{line}"
                    if proxy not in seen:
                        seen.add(proxy)
                        out.append(proxy)
        return out

    async def _fetch_one(self, client: httpx.AsyncClient, url: str) -> list[str]:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            return resp.text.splitlines()
        except Exception:
            return []

    async def _validate_many(self, proxies: list[str]) -> list[str]:
        live: list[str] = []
        for i in range(0, len(proxies), _VALIDATION_BATCH):
            batch = proxies[i : i + _VALIDATION_BATCH]
            results = await asyncio.gather(
                *(self._validate_one(p) for p in batch),
                return_exceptions=True,
            )
            for proxy, ok in zip(batch, results):
                if ok is True:
                    live.append(proxy)
        return live

    async def _validate_one(self, proxy: str) -> bool:
        try:
            async with httpx.AsyncClient(
                proxy=proxy,
                timeout=_VALIDATION_TIMEOUT,
                follow_redirects=False,
            ) as client:
                resp = await client.get(_VALIDATION_URL)
                return resp.status_code == 200
        except Exception:
            return False


_shared_pool: ProxyPool | None = None


def get_pool() -> ProxyPool:
    global _shared_pool
    if _shared_pool is None:
        _shared_pool = ProxyPool()
    return _shared_pool
