from __future__ import annotations

from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from meeting_scribe.config import load_settings


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    settings = load_settings()
    uvicorn.run("meeting_scribe.server.app:app", host=settings.app.host, port=settings.app.port, reload=False)


if __name__ == "__main__":
    main()
