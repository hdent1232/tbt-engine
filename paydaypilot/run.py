#!/usr/bin/env python3
"""PayDay Pilot launcher.

Double-click (or `python run.py`): starts the local server and opens the
dashboard in your default browser. Close it from the dashboard's Quit button
or by pressing Ctrl+C in this window. Uses only the Python standard library.
"""

import sys
import threading
import webbrowser

from app.server import serve, shutdown_event


def main():
    port = 0
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    httpd = serve(port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"PayDay Pilot running at {url}")
    print("Close this window or press Ctrl+C to quit.")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        shutdown_event.wait()
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
    print("Goodbye.")


if __name__ == "__main__":
    main()
