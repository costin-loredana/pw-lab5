import sys
import socket
import ssl
import zlib
from html.parser import HTMLParser
import json
import os


CACHE_FILE = "cache.json"


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_tags = {'script', 'style', 'head', 'noscript'}
        self.current_skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.current_skip += 1

    def handle_endtag(self, tag):
        if tag in self.skip_tags:
            self.current_skip = max(0, self.current_skip - 1)

    def handle_data(self, data):
        if self.current_skip == 0:
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped)

    def get_text(self):
        return '\n'.join(self.text_parts)


class SearchResultExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.current_link = None
        self.current_title = ""
        self.in_result_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "result__a" in attrs.get("class", ""):
            self.current_link = attrs.get("href", "")
            self.in_result_title = True
            self.current_title = ""

    def handle_endtag(self, tag):
        if tag == "a" and self.in_result_title:
            self.in_result_title = False
            if self.current_link and self.current_title:
                self.results.append((self.current_title.strip(), self.current_link))
            self.current_link = None
            self.current_title = ""

    def handle_data(self, data):
        if self.in_result_title:
            self.current_title += data


def parse_url(url):
    use_ssl = url.startswith("https://")
    url = url.replace("https://", "").replace("http://", "")
    if "/" in url:
        host, path = url.split("/", 1)
        path = "/" + path
    else:
        host = url
        path = "/"
    port = 443 if use_ssl else 80
    return host, path, port, use_ssl


def decode_chunked(data: bytes) -> bytes:
    result = b""
    while data:
        crlf = data.find(b"\r\n")
        if crlf == -1:
            break
        chunk_size = int(data[:crlf].split(b";")[0], 16)
        if chunk_size == 0:
            break
        result += data[crlf + 2: crlf + 2 + chunk_size]
        data = data[crlf + 2 + chunk_size + 2:]
    return result


def decode_gzip(data: bytes) -> bytes:
    return zlib.decompress(data, wbits=16 + zlib.MAX_WBITS)


def extract_real_url(ddg_url):
    if "uddg=" in ddg_url:
        uddg = ddg_url.split("uddg=")[1].split("&")[0]
        result = ""
        i = 0
        while i < len(uddg):
            if uddg[i] == "%" and i + 2 < len(uddg):
                result += chr(int(uddg[i+1:i+3], 16))
                i += 3
            elif uddg[i] == "+":
                result += " "
                i += 1
            else:
                result += uddg[i]
                i += 1
        return result
    return ddg_url


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


def print_json(body_text):
    try:
        data = json.loads(body_text)
        print(json.dumps(data, indent=2))
    except json.JSONDecodeError:
        print(body_text)


def fetch_url(url, max_redirects=5):
    cache = load_cache()
    if url in cache:
        print("[cache hit]")
        print(cache[url])
        return

    for _ in range(max_redirects):
        host, path, port, use_ssl = parse_url(url)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        if use_ssl:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        sock.connect((host, port))
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Accept: text/html, application/json\r\n"
            f"Accept-Encoding: gzip\r\n"
            f"User-Agent: Mozilla/5.0\r\n"
            f"Connection: close\r\n\r\n"
        )
        sock.send(request.encode())
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        sock.close()

        header_end = response.find(b"\r\n\r\n")
        if header_end == -1:
            print("Error: malformed response")
            return
        headers_raw = response[:header_end].decode(errors="ignore")
        body = response[header_end + 4:]

        status_line = headers_raw.split("\r\n")[0]
        status_code = int(status_line.split()[1])

        if status_code in (301, 302, 303, 307, 308):
            for line in headers_raw.split("\r\n"):
                if line.lower().startswith("location:"):
                    url = line.split(":", 1)[1].strip()
                    print(f"Redirecting to {url}...")
                    break
            continue

        if "transfer-encoding: chunked" in headers_raw.lower():
            body = decode_chunked(body)

        if "content-encoding: gzip" in headers_raw.lower():
            body = decode_gzip(body)

        body_text = body.decode(errors="ignore")

        content_type = ""
        for line in headers_raw.split("\r\n"):
            if line.lower().startswith("content-type:"):
                content_type = line.lower()
                break

        if "application/json" in content_type:
            output = json.dumps(json.loads(body_text), indent=2)    
        else:
            parser = TextExtractor()
            parser.feed(body_text)
            output = parser.get_text()
        
        cache[url] = output
        save_cache(cache)
        print(output)
        return

    print("Error: too many redirects")


def search(term):
    encoded = ""
    for ch in term:
        if ch.isalnum() or ch in "-_.~":
            encoded += ch
        elif ch == " ":
            encoded += "+"
        else:
            encoded += f"%{ord(ch):02X}"

    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    host, path, port, use_ssl = parse_url(url)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    context = ssl.create_default_context()
    sock = context.wrap_socket(sock, server_hostname=host)
    sock.connect((host, port))

    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Accept: text/html\r\n"
        f"Accept-Encoding: gzip\r\n"
        f"User-Agent: Mozilla/5.0\r\n"
        f"Connection: close\r\n\r\n"
    )
    sock.send(request.encode())

    response = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    sock.close()

    header_end = response.find(b"\r\n\r\n")
    headers_raw = response[:header_end].decode(errors="ignore")
    body = response[header_end + 4:]

    if "transfer-encoding: chunked" in headers_raw.lower():
        body = decode_chunked(body)

    if "content-encoding: gzip" in headers_raw.lower():
        body = decode_gzip(body)

    body_text = body.decode(errors="ignore")
    extractor = SearchResultExtractor()
    extractor.feed(body_text)

    results = extractor.results[:10]
    if not results:
        print("No results found.")
        return

    for i, (title, link) in enumerate(results, 1):
        real_url = extract_real_url(link)
        print(f"{i}. {title}")
        print(f"   {real_url}\n")


def show_help():
    print("Usage:")
    print("  go2web -u <URL>          # Fetch and display content from URL")
    print("  go2web -s <search-term>  # Search and print top 10 results")
    print("  go2web -h                # Show this help message")


if len(sys.argv) < 2:
    show_help()
    sys.exit(1)

flag = sys.argv[1]

if flag == "-h":
    show_help()
elif flag == "-u":
    if len(sys.argv) < 3:
        print("Error: URL is required")
    else:
        fetch_url(sys.argv[2])
elif flag == "-s":
    if len(sys.argv) < 3:
        print("Error: search term is required")
    else:
        term = " ".join(sys.argv[2:])
        search(term)
else:
    print(f"Unknown flag: {flag}")
    show_help()