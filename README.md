# go2web

A minimal command-line HTTP client built entirely on raw TCP/TLS sockets — no `requests`, no `urllib3`, no HTTP libraries.

> Built for the **FAF Networks Lab 5** — Websockets assignment.

---

## Demo

![how cli is used](cli_work.gif)

---

## Features

| Feature | Details |
|---|---|
| Raw TCP/TLS sockets | No HTTP libraries — everything built on `socket` + `ssl` |
| HTTP & HTTPS | Port 80 for HTTP, port 443 for HTTPS |
| Redirect following | Handles 301, 302, 303, 307, 308 — up to 5 hops |
| Chunked transfer decoding | Manual implementation of the chunked framing protocol |
| Gzip decompression | Decompresses `Content-Encoding: gzip` responses |
| HTML → plain text | Strips tags, scripts, styles using `html.parser` |
| Content negotiation | Sends `Accept: text/html, application/json` — pretty-prints JSON when received |
| DuckDuckGo search | Scrapes `html.duckduckgo.com` — no API key needed |
| File-based cache | Responses cached in `cache.json` — skips network on repeat requests |

---

---

## Usage

```bash
./go2web -h                        # show help

./go2web -u <URL>                  # fetch a URL and print human-readable output
./go2web -s <search term>          # search DuckDuckGo and print top 10 results
```

### Examples

```bash
# Fetch a webpage
./go2web -u https://example.com

# Fetch a JSON API — automatically pretty-printed
./go2web -u https://jsonplaceholder.typicode.com/posts/1

# Follows redirects automatically (HTTP → HTTPS)
./go2web -u http://github.com

# Run the same URL twice — second request served from cache
./go2web -u https://example.com
./go2web -u https://example.com     # prints [cache hit]

# Single-word search
./go2web -s python

# Multi-word search — everything after -s is the query
./go2web -s http sockets tutorial
```

---

## How it works

```
./go2web -u https://example.com
         │
         ├─ parse_url()          → host, path, port, use_ssl
         ├─ socket.connect()     → TCP 3-way handshake
         ├─ ssl.wrap_socket()    → TLS handshake (HTTPS only)
         ├─ sock.send()          → raw HTTP/1.1 GET request
         ├─ sock.recv() loop     → read full response
         ├─ parse headers        → status code, Content-Type, encodings
         ├─ 3xx? → follow Location header (up to 5 redirects)
         ├─ decode_chunked()     → strip chunked framing if needed
         ├─ decode_gzip()        → decompress if needed
         ├─ JSON? → json.dumps() → pretty-print
         │  HTML? → TextExtractor → strip tags, keep text
         └─ save to cache.json → print output
```

---

## Project structure

```
go2web/
├── go2web          # shell launcher: exec python3 go2web.py "$@"
├── go2web.py       # all logic — sockets, parsing, cache, search
├── cache.json      # auto-created on first request (gitignored)
└── README.md
```

---




