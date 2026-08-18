"""Minimal HTTP endpoint so Railway/Heroku see the container as alive."""
from aiohttp import web


async def _ok(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_health_server(host: str = "0.0.0.0", port: int = 8000) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", _ok)
    app.router.add_get("/health", _ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner


async def stop_health_server(runner: web.AppRunner) -> None:
    await runner.cleanup()
