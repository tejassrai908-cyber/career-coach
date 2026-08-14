"""Launcher: starts the Career Coach server and opens the browser.

Run by the "Career Coach" desktop shortcut via pythonw.exe (no console window).
Safe to double-click twice: if the app is already running it just opens the page
instead of starting a second copy fighting over the same port.
"""
import os
import socket
import sys
import threading
import time
import webbrowser

PORT = 5055
URL = f"http://127.0.0.1:{PORT}"
os.chdir(os.path.dirname(os.path.abspath(__file__)))


def already_running():
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


if already_running():
    webbrowser.open(URL)
    sys.exit(0)

threading.Thread(target=lambda: (time.sleep(2.5), webbrowser.open(URL)),
                 daemon=True).start()

import app  # noqa: E402
app.init_db()
app.app.run(host="0.0.0.0", port=PORT, debug=False)
