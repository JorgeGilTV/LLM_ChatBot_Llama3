#!/usr/bin/env python3
"""
Verbose Splunk REST probe: DNS → raw TCP :8089 → GET /services/server/info
Uses repo .env (no token printed). Run from anywhere:

  python3 scripts/splunk_rest_verbose_probe.py
"""
from __future__ import annotations

import argparse
import http.client as http_client
import logging
import os
import socket
import sys

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    print("Install python-dotenv: pip install python-dotenv", file=sys.stderr)
    sys.exit(1)


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _import_splunk_ipv4_scope():
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    from tools.splunk_tool import splunk_ipv4_rest_scope, splunk_prefer_ipv4

    return splunk_ipv4_rest_scope, splunk_prefer_ipv4


def main() -> int:
    p = argparse.ArgumentParser(description="Verbose Splunk REST connectivity probe")
    p.add_argument("--tcp-timeout", type=float, default=15.0, help="Per-address TCP connect timeout")
    p.add_argument("--http-connect", type=float, default=15.0, help="requests connect timeout")
    p.add_argument("--http-read", type=float, default=25.0, help="requests read timeout")
    p.add_argument("--skip-tcp", action="store_true", help="Only DNS + HTTPS GET (no raw socket pass)")
    args = p.parse_args()

    load_dotenv(os.path.join(_repo_root(), ".env"))
    splunk_ipv4_rest_scope, splunk_prefer_ipv4 = _import_splunk_ipv4_scope()
    host = (os.getenv("SPLUNK_HOST") or "arlo.splunkcloud.com").strip()
    port = int((os.getenv("SPLUNK_MGMT_PORT") or "8089").strip() or "8089")
    token = (os.getenv("SPLUNK_TOKEN") or "").strip()
    if not token:
        print("ERROR: SPLUNK_TOKEN missing in .env", file=sys.stderr)
        return 1

    mode = (os.getenv("SPLUNK_AUTH_MODE") or os.getenv("SPLUNK_REST_AUTH") or "bearer").strip().lower()
    if mode in ("splunk", "session", "splunk-session"):
        auth = f"Splunk {token}"
        scheme = "Splunk"
    else:
        auth = f"Bearer {token}"
        scheme = "Bearer"

    http_client.HTTPConnection.debuglevel = 1
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s", level=logging.DEBUG)
    for name in ("urllib3.connectionpool", "urllib3.util.connection"):
        logging.getLogger(name).setLevel(logging.DEBUG)

    url = f"https://{host}:{port}/services/server/info"

    print(f"SPLUNK_PREFER_IPV4: {'on' if splunk_prefer_ipv4() else 'off'} (default on; set SPLUNK_PREFER_IPV4=0 for IPv6/dual-stack)\n")

    with splunk_ipv4_rest_scope():
        print("=== 1) DNS (getaddrinfo) ===")
        try:
            infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except socket.gaierror as e:
            print(f"  DNS error: {e}")
            return 1
        for i, info in enumerate(infos):
            fam, _, _, _, sockaddr = info
            fam_name = "IPv4" if fam == socket.AF_INET else ("IPv6" if fam == socket.AF_INET6 else str(fam))
            print(f"  [{i}] {fam_name} {sockaddr}")

        if not args.skip_tcp:
            print("\n=== 2) Raw TCP connect (one try per resolved address) ===")
            last_err: OSError | None = None
            ok = False
            for fam, _, _, _, sockaddr in infos[:12]:
                s = None
                try:
                    s = socket.socket(fam, socket.SOCK_STREAM)
                    s.settimeout(args.tcp_timeout)
                    print(f"  connecting to {sockaddr} (timeout {args.tcp_timeout}s) ...")
                    s.connect(sockaddr)
                    print(f"  OK TCP connected {sockaddr}")
                    ok = True
                    s.close()
                    break
                except OSError as e:
                    last_err = e
                    print(f"  failed {sockaddr}: {e}")
                finally:
                    if s is not None:
                        try:
                            s.close()
                        except OSError:
                            pass
            if not ok:
                print(f"  (no successful TCP) last error: {last_err}")

        print("\n=== 3) GET (requests + urllib3/http.client debug) ===")
        print(f"URL: {url}?output_mode=json")
        print(f"Authorization: {scheme} <{len(token)} chars hidden>\n")

        try:
            r = requests.get(
                url,
                headers={"Authorization": auth},
                params={"output_mode": "json"},
                timeout=(args.http_connect, args.http_read),
                verify=True,
            )
            print(f"\n=== Response ===\nstatus: {r.status_code}")
            for k, v in r.headers.items():
                print(f"  {k}: {v}")
            body = r.text
            print(f"\nbody ({len(body)} chars, first 2000):\n{body[:2000]}")
            return 0 if r.ok else 1
        except requests.exceptions.ConnectTimeout as e:
            print(f"\n=== ConnectTimeout ===\n{e}")
            return 2
        except requests.exceptions.ReadTimeout as e:
            print(f"\n=== ReadTimeout ===\n{e}")
            return 2
        except requests.exceptions.RequestException as e:
            print(f"\n=== Request error ===\n{type(e).__name__}: {e}")
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
