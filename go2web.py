"""
go2web — a minimal HTTP/HTTPS browser for the terminal.

Usage:
  go2web -u <URL>           Fetch and display a URL
  go2web -s <search term>   Search DuckDuckGo (top 10 results)
  go2web -h                 Show this help
"""

import sys
import socket
import ssl
import zlib
from html.parser import HTMLParser
import json
import os
import time
import argparse
from urllib.parse import quote_plus, unquote_plus

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.json")


class TextExtractor(HTMLParser):
    BLOCK_TAGS   = {'p', 'div', 'section', 'article', 'main', 'header', 'footer',
                    'li', 'tr', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                    'blockquote', 'pre', 'code', 'td', 'th'}
    HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
    SKIP_TAGS    = {'script', 'style', 'head', 'noscript', 'nav', 'aside',
                    'form', 'button', 'svg', 'iframe', 'meta', 'link'}

    def __init__(self):
        super().__init__()
        self.parts        = []
        self.skip_depth   = 0
        self.in_pre       = False
        self.heading_level = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == 'pre':
            self.in_pre = True
        if tag in self.BLOCK_TAGS:
            if self.parts and self.parts[-1] != '\n':
                self.parts.append('\n')
        if tag in self.HEADING_TAGS:
            self.heading_level = int(tag[1])

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == 'pre':
            self.in_pre = False
        if tag in self.HEADING_TAGS:
            self.heading_level = None
            self.parts.append('\n')
        elif tag in self.BLOCK_TAGS:
            self.parts.append('\n')

    def handle_data(self, data):
        if self.skip_depth > 0:
            return
        if self.in_pre:
            self.parts.append(data)
            return
        stripped = data.strip()
        if not stripped:
            return
        if self.heading_level is not None:
            self.parts.append('#' * self.heading_level + ' ' + stripped + ' ')
        else:
            self.parts.append(stripped + ' ')

    def get_text(self):
        raw   = ''.join(self.parts)
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        result, prev_blank = [], False
        for line in lines:
            if not line:
                if not prev_blank:
                    result.append('')
                prev_blank = True
            else:
                result.append(line)
                prev_blank = False
        return '\n'.join(result)


class SearchResultExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results         = []
        self.current_link    = None
        self.current_title   = ""
        self.in_title        = False
        self.current_snippet = ""
        self.in_snippet      = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        if tag == "a" and "result__a" in cls:
            self.current_link  = attrs_dict.get("href", "")
            self.in_title      = True
            self.current_title = ""
        if "result__snippet" in cls:
            self.in_snippet      = True
            self.current_snippet = ""

    def handle_endtag(self, tag):
        if tag == "a" and self.in_title:
            self.in_title = False
            if self.current_link and self.current_title:
                self.results.append({
                    "title":   self.current_title.strip(),
                    "url":     _extract_real_url(self.current_link),
                    "snippet": "",
                })
            self.current_link = None
        if self.in_snippet and tag in ("a", "div", "span"):
            if self.results:
                self.results[-1]["snippet"] = self.current_snippet.strip()
            self.in_snippet = False

    def handle_data(self, data):
        if self.in_title:
            self.current_title += data
        if self.in_snippet:
            self.current_snippet += data


def _load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            data = json.loads(content) if content else {}
            return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}


def _save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[cache] Warning: could not save cache: {e}", file=sys.stderr)


def _is_fresh(cached):
    if not cached or not isinstance(cached, dict):
        return False
    if cached.get("max_age", 0) <= 0:
        return False
    try:
        return (time.time() - cached["cached_at"]) < cached["max_age"]
    except Exception:
        return False


def _validation_headers(cached):
    headers = {}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]
    return headers


def _parse_url(url):
    use_ssl = url.startswith("https://")
    stripped = url.replace("https://", "").replace("http://", "")
    if "/" in stripped:
        host, rest = stripped.split("/", 1)
        path = "/" + rest
    else:
        host = stripped
        path = "/"
    if ":" in host:
        host, port_str = host.rsplit(":", 1)
        port = int(port_str)
    else:
        port = 443 if use_ssl else 80
    return host, path, port, use_ssl


def _decode_chunked(data: bytes) -> bytes:
    result = b""
    while data:
        crlf = data.find(b"\r\n")
        if crlf == -1:
            break
        try:
            size = int(data[:crlf].split(b";")[0].strip(), 16)
        except ValueError:
            break
        if size == 0:
            break
        result += data[crlf + 2: crlf + 2 + size]
        data = data[crlf + 2 + size + 2:]
    return result


def _decode_gzip(data: bytes) -> bytes:
    return zlib.decompress(data, wbits=16 + zlib.MAX_WBITS)


def _raw_request(url, extra_headers=None, max_redirects=8, timeout=12):
    extra_headers = extra_headers or {}
    visited = set()

    for _ in range(max_redirects):
        if url in visited:
            raise RuntimeError("Redirect loop detected")
        visited.add(url)

        host, path, port, use_ssl = _parse_url(url)

        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(timeout)
        try:
            raw_sock.connect((host, port))
        except (socket.gaierror, OSError) as e:
            raise RuntimeError(f"Connection failed to {host}:{port} — {e}")

        if use_ssl:
            context = ssl.create_default_context()
            sock = context.wrap_socket(raw_sock, server_hostname=host)
        else:
            sock = raw_sock

        req_lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}",
            "Accept: text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Encoding: gzip",
            "Accept-Language: en-US,en;q=0.9",
            "User-Agent: go2web/2.0",
            "Connection: close",
        ]
        for k, v in extra_headers.items():
            req_lines.append(f"{k}: {v}")
        req_lines += ["", ""]
        sock.sendall("\r\n".join(req_lines).encode())

        response = b""
        try:
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                response += chunk
        finally:
            sock.close()

        header_end = response.find(b"\r\n\r\n")
        if header_end == -1:
            raise RuntimeError("Malformed HTTP response (no header end)")

        headers_raw = response[:header_end].decode(errors="ignore")
        body        = response[header_end + 4:]

        status_line = headers_raw.split("\r\n")[0]
        try:
            status_code = int(status_line.split()[1])
        except (IndexError, ValueError):
            raise RuntimeError(f"Unreadable status line: {status_line!r}")

        headers_dict = {}
        for line in headers_raw.split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers_dict[k.strip().lower()] = v.strip()

        if status_code in (301, 302, 303, 307, 308):
            location = headers_dict.get("location")
            if not location:
                raise RuntimeError("Redirect with no Location header")
            if location.startswith("/"):
                scheme   = "https" if use_ssl else "http"
                location = f"{scheme}://{host}{location}"
            elif not location.startswith("http"):
                scheme   = "https" if use_ssl else "http"
                location = f"{scheme}://{host}/{location}"
            print(f"  ↳ Redirect → {location}", file=sys.stderr)
            url           = location
            extra_headers = {}
            continue

        if "chunked" in headers_dict.get("transfer-encoding", ""):
            body = _decode_chunked(body)
        if "gzip" in headers_dict.get("content-encoding", ""):
            try:
                body = _decode_gzip(body)
            except zlib.error:
                pass

        return status_code, headers_dict, body.decode(errors="ignore"), url

    raise RuntimeError("Too many redirects")


def _extract_real_url(ddg_url):
    if "uddg=" in ddg_url:
        uddg = ddg_url.split("uddg=")[1].split("&")[0]
        return unquote_plus(uddg)
    return ddg_url


def fetch_url(url, silent=False):
    cache  = _load_cache()
    cached = cache.get(url)

    if cached and _is_fresh(cached):
        if not silent:
            print("[cache] Serving fresh cached copy", file=sys.stderr)
            print(cached["body"])
        return cached["body"]

    extra = _validation_headers(cached) if cached else {}

    try:
        status_code, headers, body_text, final_url = _raw_request(url, extra_headers=extra)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return ""

    if status_code == 304 and cached:
        if not silent:
            print("[cache] 304 Not Modified — using cached copy", file=sys.stderr)
            print(cached["body"])
        return cached["body"]

    if status_code >= 400:
        print(f"Error: server returned HTTP {status_code}", file=sys.stderr)
        return ""

    content_type = headers.get("content-type", "")
    if "application/json" in content_type or "text/json" in content_type:
        try:
            output = json.dumps(json.loads(body_text), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            output = body_text
    else:
        parser = TextExtractor()
        parser.feed(body_text)
        output = parser.get_text()

    max_age = 0
    for part in headers.get("cache-control", "").split(","):
        part = part.strip()
        if part.startswith("max-age="):
            try:
                max_age = int(part.split("=")[1])
            except ValueError:
                pass

    cache[url] = {
        "url":           url,
        "body":          output,
        "status_code":   status_code,
        "content_type":  content_type,
        "etag":          headers.get("etag"),
        "last_modified": headers.get("last-modified"),
        "max_age":       max_age,
        "cached_at":     time.time(),
    }
    _save_cache(cache)

    if not silent:
        print(output)
    return output


def search(term, interactive=True):
    encoded = quote_plus(term)
    url     = f"https://html.duckduckgo.com/html/?q={encoded}"

    try:
        _, _, body_text, _ = _raw_request(url)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return []

    extractor = SearchResultExtractor()
    extractor.feed(body_text)
    results = extractor.results[:10]

    if not results:
        print("No results found.")
        return []

    print()
    print(f'  Search results for: "{term}"')
    print("  " + "─" * 60)
    print()
    for i, r in enumerate(results, 1):
        print(f"  [{i}] {r['title']}")
        print(f"       {r['url']}")
        if r.get("snippet"):
            print(f"       {r['snippet']}")
        print()

    if interactive:
        try:
            choice = input("  Open a result [1-10] or press Enter to skip: ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(results):
                    target = results[idx]["url"]
                    print()
                    print(f"  Fetching: {target}", file=sys.stderr)
                    print("  " + "─" * 60)
                    print()
                    fetch_url(target)
                else:
                    print("  Invalid selection.")
        except (EOFError, KeyboardInterrupt):
            print()

    return results


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-h", "--help",   action="store_true")
    parser.add_argument("-u", "--url",    metavar="URL")
    parser.add_argument("-s", "--search", metavar="TERM", nargs="+")
    args = parser.parse_args()

    if args.help or (not args.url and not args.search):
        print(__doc__)
        sys.exit(0)

    if args.url:
        fetch_url(args.url)
    elif args.search:
        search(" ".join(args.search), interactive=sys.stdin.isatty())


if __name__ == "__main__":
    main()