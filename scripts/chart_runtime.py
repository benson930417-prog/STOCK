"""Resource/queue controls only; no market source or chart rendering rules."""
import asyncio
from pathlib import Path
from starlette.responses import JSONResponse


def service_memory_bytes():
    """Read this service's complete cgroup, including Chromium subprocesses."""
    try:
        group = next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines()
                     if line.startswith('0::'))
        return int((Path('/sys/fs/cgroup') / group.lstrip('/') / 'memory.current').read_text())
    except (OSError, ValueError, StopIteration):
        return None


class BrowserRequestGuard:
    """One complete browser request at a time, with bounded queue and execution.

    Pure ASGI: timeout cancels/awaits the actual handler before releasing the
    lock. Timing only BaseHTTPMiddleware.call_next can leave its task running.
    """
    def __init__(self, app, *, lock, on_failure, queue_seconds=5, work_seconds=180):
        self.app, self.lock, self.on_failure = app, lock, on_failure
        self.queue_seconds, self.work_seconds = queue_seconds, work_seconds

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') not in {
                '/snapshot', '/market-text', '/market-debug'}:
            return await self.app(scope, receive, send)
        try:
            await asyncio.wait_for(self.lock.acquire(), self.queue_seconds)
        except asyncio.TimeoutError:
            return await JSONResponse({'detail': 'Chart worker busy; retry later'},
                                      status_code=503)(scope, receive, send)
        started = False
        failed = False

        async def track_send(message):
            nonlocal started, failed
            if message['type'] == 'http.response.start':
                started = True
                failed = message['status'] >= 500
            await send(message)

        try:
            await asyncio.wait_for(self.app(scope, receive, track_send), self.work_seconds)
        except asyncio.CancelledError:
            failed = True
            raise
        except Exception as exc:
            failed = True
            if started:
                raise
            await JSONResponse({'detail': f'Chart worker unavailable: {type(exc).__name__}'},
                               status_code=503)(scope, receive, send)
        finally:
            if failed:
                self.on_failure()
            self.lock.release()
