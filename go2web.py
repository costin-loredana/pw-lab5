import sys
import socket
import ssl

def show_help():
    print("Usage: go2web <url>")
    print("go2web -u <url> - Update something")
    print("go2web -h - Show this help message")
    print("go2web -s <search-term> - Search for something and print top 10 results")

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

if flag == '-h':
    show_help()
    
elif flag == '-u':
    if len(sys.argv) < 3:
        print("Error: URL is required for update")
    else:
        url = sys.argv[2]
        print(f"URL requested {url}...")
elif flag == '-s':
    if len(sys.argv) < 3:
        print("Error: missing search term")
    else:
        url = sys.argv[2]
        print(f"Search term: {url}...")
else:
    print("Unknown option", flag)
    show_help()