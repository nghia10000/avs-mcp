"""fake_plc.py — stand-in for a PLC on a TCP socket, to test an AVS program's communication without the machine.

Listens on host:port, accepts one connection at a time (the vision program is the client, as with a PLC's open
socket / MC protocol port), prints every line it receives with a timestamp and appends it to --log. With --reply,
answers each line (e.g. "ACK"). Exits after --lines lines or --timeout seconds of silence. Standard library only.

    python fake_plc.py --port 5000 --lines 8 --log plc_log.txt
"""

import argparse
import socket
import sys
import time


def serve(host, port, lines, timeout, log, reply):
    got = 0
    with socket.create_server((host, port)) as srv:
        srv.settimeout(timeout)
        print(f"fake PLC listening on {host}:{port}", flush=True)
        while not lines or got < lines:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                print("no connection, timeout", flush=True)
                break
            print(f"connected: {addr[0]}:{addr[1]}", flush=True)
            conn.settimeout(timeout)
            buf = b""
            with conn:
                while not lines or got < lines:
                    try:
                        data = conn.recv(4096)
                    except socket.timeout:
                        print("silent, timeout", flush=True)
                        return got
                    if not data:
                        print("disconnected", flush=True)
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        text = line.rstrip(b"\r").decode("utf-8", "replace")
                        got += 1
                        stamp = time.strftime("%H:%M:%S")
                        print(f"{stamp} <- {text}", flush=True)
                        if log:
                            with open(log, "a", encoding="utf-8") as f:
                                f.write(f"{stamp}\t{text}\n")
                        if reply:
                            conn.sendall(reply.encode() + b"\r\n")
    return got


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--lines", type=int, default=0, help="stop after this many lines (0 = never)")
    p.add_argument("--timeout", type=float, default=60, help="seconds without a connection or data before exiting")
    p.add_argument("--log", default="", help="append received lines to this file")
    p.add_argument("--reply", default="", help="send this text back after each line")
    a = p.parse_args()
    got = serve(a.host, a.port, a.lines, a.timeout, a.log, a.reply)
    print(f"{got} line(s) received", flush=True)
    sys.exit(0 if not a.lines or got >= a.lines else 1)


if __name__ == "__main__":
    main()
