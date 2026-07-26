import uvicorn

from home_assist_agent.settings import AppSettings


def main() -> None:
    settings = AppSettings()
    uvicorn.run(
        "home_assist_agent.main:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
