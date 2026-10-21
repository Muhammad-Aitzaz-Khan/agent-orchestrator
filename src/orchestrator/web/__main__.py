"""Start the web app: python -m orchestrator.web  (then open http://127.0.0.1:8000)

Options:
    --host HOST   default 127.0.0.1 (this computer only)
    --port PORT   default 8000
"""
import argparse
import webbrowser

import uvicorn

from orchestrator.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="orchestrator.web", description="Run the Relay web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()
    url = f"http://{args.host}:{args.port}"
    print(f"Relay is running at {url}  (press Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
