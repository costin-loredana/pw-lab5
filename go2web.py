import sys
import socket
import ssl
from html.parser import HTMLParser

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

def parse_url(url):
    """Returns (hosts, path, port, use_ssl)"""
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
    """Decode HTTP chunked transfer encoding."""
    result = b""
    while data:
        crlf = data.find(b"\r\n")
        if crlf == -1:
            break
        chunk_size = int(data[:crlf].split(b";")[0], 16)
        if chunk_size == 0:
            break 
        result += data[crlf + 2 : crlf + 2 + chunk_size]
        data = data[crlf + 2 + chunk_size + 2 :]
    return result

def fetch_url(url, max_redirects=5):
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
            f"Accept: text/html\r\n"
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
        body = response[header_end + 4 :]

        status_line = headers_raw.split("\r\n")[0]
        status_code = int(status_line.split()[1])

        if status_code in (301, 302, 303, 307, 308):
            for line in headers_raw.split("\r\n"):
                if line.lower().startswith("location:"):
                    url = line.split(":", 1)[1].strip()
                    print(f"Redirecting to {url}...")
                    break
            continue

        headers_lower = headers_raw.lower()
        if "transfer-encoding: chunked" in headers_lower:
            body = decode_chunked(body)
        
        body_text = body.decode(errors="ignore")
        parser = TextExtractor()
        parser.feed(body_text)
        print(parser.get_text())
        return 
    print("Error: too many redirects")

def show_help():
    print("Usage: go2web.py <URL>")
    print("go2web -u <URL>  - Fetch and display text content from the specified URL")
    print("go2web -h        - Show this help message")
    print("go2web -s <search-term> -Search for something (top 10 results)")

def fetch_url(url):
    if url.startswith("https://"):
        url = url[len("https://"):]
    host = url 
    path = "/"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    secure_sock = ssl.wrap_socket(sock)
    secure_sock.connect((host, 443))
    request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
    secure_sock.send(request.encode())
    while True:
        data = secure_sock.recv(4096)
        if not data:
            break 
        print(data.decode(errors = 'ignore'), end = "")

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
        url = sys.argv[2]
        print(f"Fetching {url}...\n")
        fetch_url(url)
elif flag == "-s":
    if len(sys.argv) < 3:
        print("Error: search term is required")
    else:
        term = sys.argv[2]
        print(f"Searching for '{term}'...\n")
else:
    print(f"Unknown flag: {flag}")
    show_help()