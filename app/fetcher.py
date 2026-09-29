"""Size-limited fetching with per-connection public-IP validation and robots checks.

No cookies, proxy credentials, arbitrary request headers, headless browser, or login
bypass is accepted. DNS is checked in the connector itself to resist rebinding.
"""
import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser

import aiohttp
from aiohttp.abc import AbstractResolver

USER_AGENT = 'MicListBot/1.0'
MAX_BYTES = 5 * 1024 * 1024

class FetchError(ValueError):
    pass

def public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split('%')[0])
        if getattr(ip, 'ipv4_mapped', None):
            ip = ip.ipv4_mapped
        # Block transition mechanisms as well as private/reserved/link-local ranges.
        if isinstance(ip, ipaddress.IPv6Address) and (ip.sixtofour or ip.teredo):
            return False
        return ip.is_global and not ip.is_multicast
    except ValueError:
        return False

def validate_url(url: str) -> str:
    try:
        p = urlsplit(url.strip())
        if p.scheme not in ('http', 'https') or not p.hostname:
            raise FetchError('Use a public http:// or https:// URL.')
        if p.username or p.password or p.port not in (None, 80, 443):
            raise FetchError('Credentials and nonstandard ports are not allowed in source URLs.')
        host = p.hostname.rstrip('.').lower()
        if (host == 'localhost' or '.' not in host or host.endswith(('.localhost', '.local', '.internal', '.home', '.test', '.invalid'))):
            raise FetchError('Private and local network addresses are not allowed.')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not public_ip(host):
                raise FetchError('Private and reserved IP addresses are not allowed.')
        if any(ord(c) < 33 for c in url):
            raise FetchError('The URL contains invalid whitespace or control characters.')
        return p._replace(fragment='').geturl()
    except ValueError as exc:
        if isinstance(exc, FetchError):
            raise
        raise FetchError('The source URL is invalid.') from exc

class PublicResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM, family=family)
        if not infos or any(not public_ip(info[4][0]) for info in infos):
            raise FetchError('This hostname resolves to a private or reserved IP address.')
        return [dict(hostname=host, host=i[4][0], port=i[4][1], family=i[0], proto=i[2], flags=socket.AI_NUMERICHOST) for i in infos]

    async def close(self):
        pass

class SafeFetcher:
    def __init__(self):
        self.robots_cache = {}
        self.host_last = {}
        self.host_locks = {}

    async def _request(self, url, headers=None, check_redirect_robots=False):
        connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False, limit=4)
        timeout = aiohttp.ClientTimeout(total=25, connect=10)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False,
                                         cookie_jar=aiohttp.DummyCookieJar(),
                                         headers={'User-Agent': USER_AGENT, 'Accept': '*/*'}) as session:
            for _ in range(5):
                url = validate_url(url)
                async with session.get(url, allow_redirects=False, headers=headers or {}) as res:
                    if res.status in (301, 302, 303, 307, 308):
                        location = res.headers.get('Location')
                        if not location:
                            raise FetchError('Redirect did not include a destination.')
                        next_url = validate_url(urljoin(url, location))
                        if check_redirect_robots:
                            await self.check_robots(next_url)
                        url = next_url
                        headers = {}  # Do not forward validators between redirected resources.
                        continue
                    size = 0
                    pieces = []
                    async for piece in res.content.iter_chunked(65536):
                        size += len(piece)
                        if size > MAX_BYTES:
                            raise FetchError('Source exceeds the 5 MB import limit.')
                        pieces.append(piece)
                    return res.status, dict(res.headers), b''.join(pieces), url
        raise FetchError('Too many redirects. Use the final public URL directly.')

    async def check_robots(self, url):
        p = urlsplit(validate_url(url))
        origin = f'{p.scheme}://{p.netloc}'
        cached = self.robots_cache.get(origin)
        if not cached or time.time() - cached[0] > 3600:
            code, _, data, _ = await self._request(origin + '/robots.txt')
            if code in (404, 410):
                data = b'User-agent: *\nAllow: /'
            elif code >= 400:
                raise FetchError(f'Cannot verify crawling rules (robots.txt HTTP {code}). Import paused.')
            parser = RobotFileParser(origin + '/robots.txt')
            parser.parse(data.decode('utf-8', errors='replace').splitlines())
            cached = (time.time(), parser)
            self.robots_cache[origin] = cached
        parser = cached[1]
        if not parser.can_fetch(USER_AGENT, url):
            raise FetchError('This source disallows MicListBot in robots.txt. Use an authorized export or feed instead.')
        delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay('*') or 1
        if delay > 60:
            raise FetchError('This source requests a long crawl delay; arrange an authorized feed instead.')
        return origin, max(1, delay)

    async def fetch(self, url, etag=None, modified=None):
        # Bound the entire redirect / robots chain below the source lease duration.
        async with asyncio.timeout(75):
            return await self._fetch(url, etag, modified)

    async def _fetch(self, url, etag=None, modified=None):
        url = validate_url(url)
        try:
            origin, delay = await self.check_robots(url)
            lock = self.host_locks.setdefault(origin, asyncio.Lock())
            async with lock:
                wait = delay - (time.monotonic() - self.host_last.get(origin, 0))
                if wait > 0:
                    await asyncio.sleep(wait)
                headers = {}
                if etag:
                    headers['If-None-Match'] = etag
                if modified:
                    headers['If-Modified-Since'] = modified
                result = await self._request(url, headers, check_redirect_robots=True)
                self.host_last[origin] = time.monotonic()
            code, response_headers, content, final_url = result
            if code not in (200, 304):
                raise FetchError(f'Source returned HTTP {code}. Existing listings have been kept.')
            return {'status': code, 'headers': response_headers, 'content': content, 'url': final_url}
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise FetchError('The source could not be reached safely. Existing listings have been kept.') from exc
