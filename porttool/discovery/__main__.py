"""``python -m porttool.discovery run ...``: discovery without the main application."""

from porttool.discovery.cli import discover

if __name__ == "__main__":
    discover(prog_name="porttool discover")
