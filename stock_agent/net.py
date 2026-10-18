"""HTTP helpers with per-host throttling and retries.

證交所網站對短時間大量請求會暫時封鎖 IP，所以每個主機都有最小請求間隔。
"""
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "Mozilla/5.0 (compatible; tw-stock-dashboard/1.0)"
MIN_INTERVAL = {
    "www.twse.com.tw": 3.5,
    "openapi.twse.com.tw": 0.5,
    "www.tpex.org.tw": 1.5,
    "opendata.tdcc.com.tw": 1.0,
    "mopsov.twse.com.tw": 2.0,
}
_last_request = {}


class FetchError(Exception):
    """The request failed or the response could not be decoded (as opposed to 'no data that day')."""


def _ssl_context():
    ctx = ssl.create_default_context()
    try:  # Some Python builds ship without a CA bundle; certifi fills the gap when installed.
        import certifi

        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return ctx


_CTX = _ssl_context()


def _throttle(host):
    wait = MIN_INTERVAL.get(host, 1.0) - (time.monotonic() - _last_request.get(host, -1e9))
    if wait > 0:
        time.sleep(wait)
    _last_request[host] = time.monotonic()


def fetch(url, retries=3, timeout=60):
    host = urllib.parse.urlsplit(url).hostname
    last_error = None
    for attempt in range(retries):
        _throttle(host)
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/csv, */*"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_CTX) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_error = e
            if 400 <= e.code < 500 and e.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError) as e:
            last_error = e
        time.sleep(5 * (attempt + 1))
    raise FetchError(f"{url} -> {last_error}")


def fetch_json(url, **kwargs):
    text = fetch(url, **kwargs).decode("utf-8-sig", errors="replace")
    try:
        return json.loads(text)
    except ValueError as e:
        raise FetchError(f"{url} -> not JSON: {text[:80]!r}") from e


def fetch_text(url, **kwargs):
    return fetch(url, **kwargs).decode("utf-8-sig", errors="replace")
