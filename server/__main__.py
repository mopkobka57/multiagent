"""Entry point: python -m multiagent.server"""

import webbrowser
import threading

import uvicorn

PORT = 8420


def main():
    url = f"http://localhost:{PORT}"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Agent Monitor starting at {url}")
    uvicorn.run(
        "multiagent.server.app:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
