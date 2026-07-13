#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local SOCKS5 chain proxy: client → this(10808) → Clash(7897) → overseas → residential SOCKS.

KiroX / Playwright can use socks5://127.0.0.1:10808 for a residential exit.
Never touches Clash global config. Start before kiro pipeline, stop after.

Credentials via env (never hardcode secrets in git):
  NOVP_HOST / NOVP_PORT / NOVP_USER / NOVP_PASS
  optional: CLASH_HOST CLASH_PORT LISTEN_HOST LISTEN_PORT
"""
from __future__ import annotations

import os
import select
import signal
import socket
import struct
import sys
import threading

CLASH_HOST = os.environ.get("CLASH_HOST", "127.0.0.1")
CLASH_PORT = int(os.environ.get("CLASH_PORT", "7897"))
NOVP_HOST = os.environ.get("NOVP_HOST", "").strip()
NOVP_PORT = int(os.environ.get("NOVP_PORT", "0") or "0")
NOVP_USER = os.environ.get("NOVP_USER", "").encode()
NOVP_PASS = os.environ.get("NOVP_PASS", "").encode()
LISTEN_HOST = os.environ.get("LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "10808"))
BUF = 65536

def log(msg):
    print(f'[chain-socks] {msg}', flush=True)

def connect_via_chain(dest_host, dest_port):
    """Establish: local → Clash HTTP CONNECT → NovProxy SOCKS5 → dest."""
    # Step 1: TCP to Clash
    s = socket.create_connection((CLASH_HOST, CLASH_PORT), timeout=20)
    s.settimeout(20)

    # Step 2: HTTP CONNECT to NovProxy through Clash
    req = f'CONNECT {NOVP_HOST}:{NOVP_PORT} HTTP/1.1\r\nHost: {NOVP_HOST}:{NOVP_PORT}\r\n\r\n'.encode()
    s.sendall(req)
    resp = b''
    while b'\r\n\r\n' not in resp:
        chunk = s.recv(4096)
        if not chunk:
            raise ConnectionError(f'Clash closed during CONNECT')
        resp += chunk
    if b'200' not in resp.split(b'\r\n')[0]:
        raise ConnectionError(f'Clash CONNECT failed: {resp[:80]}')

    # Step 3: SOCKS5 handshake with NovProxy
    s.sendall(b'\x05\x01\x02')
    greet = _recv_exact(s, 2)
    if greet != b'\x05\x02':
        raise ConnectionError(f'SOCKS5 greet failed: {greet!r}')

    # Step 4: SOCKS5 auth
    s.sendall(b'\x01' + bytes([len(NOVP_USER)]) + NOVP_USER + bytes([len(NOVP_PASS)]) + NOVP_PASS)
    auth = _recv_exact(s, 2)
    if auth != b'\x01\x00':
        raise ConnectionError(f'SOCKS5 auth failed: {auth!r}')

    # Step 5: SOCKS5 CONNECT to destination
    h = dest_host.encode() if isinstance(dest_host, str) else dest_host
    s.sendall(b'\x05\x01\x00\x03' + bytes([len(h)]) + h + struct.pack('!H', dest_port))
    vr = _recv_exact(s, 4)
    rep = vr[1]
    atyp = vr[3]
    if atyp == 1:
        _recv_exact(s, 6)
    elif atyp == 3:
        ln = _recv_exact(s, 1)[0]
        _recv_exact(s, ln + 2)
    elif atyp == 4:
        _recv_exact(s, 18)
    if rep != 0:
        raise ConnectionError(f'SOCKS5 CONNECT rep={rep}')
    return s

def _recv_exact(s, n):
    buf = b''
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            raise ConnectionError(f'closed, wanted {n} got {len(buf)}')
        buf += c
    return buf

def relay(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 60)
            if not r:
                continue
            for s in r:
                data = s.recv(BUF)
                if not data:
                    return
                other = b if s is a else a
                other.sendall(data)
    except Exception:
        pass
    finally:
        try: a.close()
        except: pass
        try: b.close()
        except: pass

def handle_client(client, addr):
    try:
        client.settimeout(20)
        # SOCKS5 greeting from client
        ver = client.recv(2)
        if ver[0] != 5:
            client.close(); return
        nmethods = ver[1]
        client.recv(nmethods)
        # No auth needed for local
        client.sendall(b'\x05\x00')

        # SOCKS5 request
        head = client.recv(4)
        if head[0] != 5:
            client.close(); return
        atyp = head[3]
        if atyp == 1:
            addr_bytes = client.recv(4)
            dest_host = '.'.join(str(b) for b in addr_bytes)
        elif atyp == 3:
            ln = client.recv(1)[0]
            dest_host = client.recv(ln).decode()
        elif atyp == 4:
            addr_bytes = client.recv(16)
            dest_host = socket.inet_ntop(socket.AF_INET6, addr_bytes)
        else:
            client.close(); return
        dest_port = struct.unpack('!H', client.recv(2))[0]

        # Connect via chain
        remote = connect_via_chain(dest_host, dest_port)

        # Success reply
        client.sendall(b'\x05\x00\x00\x01' + b'\x00\x00\x00\x00' + b'\x00\x00')
        client.settimeout(None)
        relay(client, remote)
    except Exception as e:
        try:
            client.sendall(b'\x05\x01\x00\x01' + b'\x00\x00\x00\x00' + b'\x00\x00')
        except:
            pass
        try:
            client.close()
        except:
            pass

def main():
    if not NOVP_HOST or not NOVP_PORT or not NOVP_USER or not NOVP_PASS:
        log("Set NOVP_HOST NOVP_PORT NOVP_USER NOVP_PASS (residential SOCKS via Clash CONNECT)")
        sys.exit(2)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(32)
    log(f'Listening on socks5://{LISTEN_HOST}:{LISTEN_PORT}')
    log(f'Chain: client → Clash({CLASH_PORT}) → resi({NOVP_HOST}:{NOVP_PORT}) → dest')

    def shutdown(*_):
        log('Shutting down...')
        srv.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        try:
            client, addr = srv.accept()
            t = threading.Thread(target=handle_client, args=(client, addr), daemon=True)
            t.start()
        except KeyboardInterrupt:
            shutdown()

if __name__ == '__main__':
    main()
