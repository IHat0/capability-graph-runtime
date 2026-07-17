import socket

from standalone_candidate import main

try:
    socket.create_connection(("192.0.2.1", 443), timeout=0.1)
except OSError:
    pass

if __name__ == "__main__":
    main("valid")
