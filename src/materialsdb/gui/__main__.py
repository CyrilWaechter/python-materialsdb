"""Entry point for the materialsdb picker web UI."""

import argparse
import webbrowser

from materialsdb.gui.server import make_server


def main():
    parser = argparse.ArgumentParser(prog="materialsdb-gui", description="Explore and export materialsdb materials.")
    parser.add_argument("--port", type=int, default=8619, help="local port (default 8619)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()

    server = make_server(port=args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"materialsdb picker on {url} (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
