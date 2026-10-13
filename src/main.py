"""SEO Audit actor: bulk technical SEO checks over plain HTTP (no headless browser).

Each public URL is fetched with a browser-like User-Agent, redirects are followed
and recorded, and the HTML is parsed for on-page SEO signals. Pay-per-event
charges are emitted only for successful audits, right after the fetch/parse and
before the dataset item is pushed.
"""

import asyncio
import ipaddress
import os
import socket
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from apify import Actor

EVENT_URL_AUDITED = 'url-audited'

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/131.0.0.0 Safari/537.36'
)

MAX_BODY_BYTES = 5 * 1024 * 1024  # don't parse gigantic downloads


def sanitize_proxy_env() -> None:
    """Strip brackets from IPv6 entries in no_proxy.

    Some environments export no_proxy entries like ``[::1]``; httpx parses
    no_proxy as URL patterns and crashes on the brackets (InvalidURL).
    Unbracketed ``::1`` parses fine and matches the same hosts.
    """
    for key in ('no_proxy', 'NO_PROXY'):
        val = os.environ.get(key)
        if not val:
            continue
        parts = []
        for part in val.split(','):
            part = part.strip()
            if part.startswith('[') and part.endswith(']'):
                part = part[1:-1]
            parts.append(part)
        os.environ[key] = ','.join(parts)


def check_public_url(url: str) -> tuple[bool, str]:
    """SSRF guard: only allow http(s) URLs that resolve to public IPs."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, 'malformed URL'
    if parsed.scheme not in ('http', 'https'):
        return False, f"unsupported scheme '{parsed.scheme}' (only http/https allowed)"
    host = parsed.hostname
    if not host:
        return False, 'missing hostname'
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, 'DNS resolution failed'
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False, 'unparseable resolved address'
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast \
                or ip.is_reserved or ip.is_unspecified:
            return False, f'blocked: {host} resolves to non-public IP {ip}'
    return True, ''


def normalize_url(raw: str) -> str:
    raw = raw.strip()
    if raw and '://' not in raw:
        raw = 'https://' + raw
    return raw


def _meta_content(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find('meta', attrs={'name': lambda v: v and v.lower() == name})
    return (tag.get('content', '') or '').strip() if tag else ''


def parse_seo(html: str) -> dict:
    """Extract on-page SEO signals from HTML."""
    soup = BeautifulSoup(html, 'lxml')

    title = soup.title.string.strip() if soup.title and soup.title.string else ''
    meta_desc = _meta_content(soup, 'description')
    robots_meta = _meta_content(soup, 'robots')

    canonical = ''
    for link in soup.find_all('link'):
        rels = [str(r).lower() for r in (link.get('rel') or [])]
        if 'canonical' in rels:
            canonical = (link.get('href') or '').strip()
            break

    h1_tags = soup.find_all('h1')
    h1_text = h1_tags[0].get_text(' ', strip=True) if h1_tags else ''

    hreflang = 0
    for link in soup.find_all('link'):
        rels = [str(r).lower() for r in (link.get('rel') or [])]
        if 'hreflang' in rels:
            hreflang += 1

    images = soup.find_all('img')
    missing_alt = sum(1 for img in images if not (img.get('alt') or '').strip())

    html_tag = soup.find('html')
    lang = (html_tag.get('lang') or '').strip() if html_tag else ''
    has_viewport = soup.find('meta', attrs={'name': lambda v: v and v.lower() == 'viewport'}) is not None

    text = soup.get_text(' ', strip=True)
    word_count = len(text.split())

    return {
        'title': title,
        'titleLength': len(title),
        'metaDescription': meta_desc,
        'metaDescriptionLength': len(meta_desc),
        'canonical': canonical,
        'robotsMeta': robots_meta,
        'h1': h1_text,
        'h1Count': len(h1_tags),
        'wordCount': word_count,
        'imagesTotal': len(images),
        'imagesMissingAlt': missing_alt,
        'hreflangCount': hreflang,
        'hasViewport': has_viewport,
        'lang': lang,
    }


async def audit_url(client: httpx.AsyncClient, raw_url: str) -> dict:
    """Fetch one URL and compute its SEO audit row. Never raises."""
    url = normalize_url(raw_url)
    item = {
        'url': raw_url.strip(),
        'finalUrl': '',
        'statusCode': None,
        'redirectCount': 0,
        'redirectChain': '',
        'loadTimeMs': None,
        'title': '', 'titleLength': 0,
        'metaDescription': '', 'metaDescriptionLength': 0,
        'canonical': '', 'robotsMeta': '',
        'h1': '', 'h1Count': 0,
        'wordCount': 0, 'imagesTotal': 0, 'imagesMissingAlt': 0,
        'hreflangCount': 0, 'hasViewport': False, 'lang': '',
        'ok': False, 'error': '',
    }

    ok, reason = check_public_url(url)
    if not ok:
        item['error'] = reason
        return item

    try:
        start = time.perf_counter()
        resp = await client.get(url, follow_redirects=True)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
    except httpx.TimeoutException:
        item['error'] = 'request timed out'
        return item
    except httpx.TooManyRedirects:
        item['error'] = 'too many redirects'
        return item
    except httpx.HTTPError as exc:
        item['error'] = f'request failed: {type(exc).__name__}'
        return item

    chain = [f'{r.status_code} {r.url}' for r in resp.history] + [f'{resp.status_code} {resp.url}']
    item['statusCode'] = resp.status_code
    item['finalUrl'] = str(resp.url)
    item['redirectCount'] = len(resp.history)
    item['redirectChain'] = ' -> '.join(chain)
    item['loadTimeMs'] = elapsed_ms

    content_type = resp.headers.get('content-type', '')
    if 'html' in content_type.lower() and len(resp.content) <= MAX_BODY_BYTES:
        try:
            item.update(parse_seo(resp.text))
        except Exception as exc:  # parse problems shouldn't fail the audit
            item['error'] = f'parse warning: {type(exc).__name__}'

    item['ok'] = True
    return item


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        raw_urls = inp.get('urls') or ''
        max_urls = inp.get('maxUrls') or 50
        try:
            max_urls = max(1, int(max_urls))
        except (TypeError, ValueError):
            max_urls = 50

        urls = [u for u in (line.strip() for line in str(raw_urls).splitlines()) if u][:max_urls]
        if not urls:
            await Actor.set_value('OUTPUT', {'total': 0, 'audited': 0, 'failed': 0})
            return

        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
        timeout = httpx.Timeout(30.0)
        sanitize_proxy_env()
        async with httpx.AsyncClient(
            headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xhtml+xml'},
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
        ) as client:
            sem = asyncio.Semaphore(5)

            async def one(u: str) -> dict:
                async with sem:
                    return await audit_url(client, u)

            results = await asyncio.gather(*(one(u) for u in urls))

        audited = 0
        failed = 0
        for item in results:
            if item['ok']:
                # Charge only after a successful audit, never on failure.
                await Actor.charge(EVENT_URL_AUDITED)
                audited += 1
            else:
                failed += 1
            await Actor.push_data(item)

        await Actor.set_value('OUTPUT', {
            'total': len(results),
            'audited': audited,
            'failed': failed,
        })
        Actor.log.info('SEO audit finished: %d audited, %d failed', audited, failed)
