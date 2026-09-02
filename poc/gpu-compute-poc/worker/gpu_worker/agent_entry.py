"""PyInstaller entry point for the standalone Windows Worker agent."""

from gpu_worker.client import main


if __name__ == "__main__":
    raise SystemExit(main())
