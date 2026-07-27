import argparse
from collections.abc import Sequence

import uvicorn

from home_assist_agent.settings import AppSettings


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="home-assist-agent",
        description="Home Assist Agent 本地服务",
    )
    parser.parse_args(argv)
    settings = AppSettings()
    uvicorn.run(
        "home_assist_agent.main:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
