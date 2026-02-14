import asyncio
import json
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

from app.checker import DockerUpdateChecker

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 300))

app = FastAPI(title="Docker Update Dashboard")
checker = DockerUpdateChecker(check_interval=CHECK_INTERVAL)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _sse_line(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/api/containers")
async def get_containers():
    containers = await checker.get_all_containers()

    containers.sort(key=lambda c: (not c["update_available"], c["name"].lower()))

    summary = {
        "total": len(containers),
        "updates_available": sum(1 for c in containers if c["update_available"]),
        "up_to_date": sum(
            1 for c in containers if not c["update_available"] and not c["error"]
        ),
        "errors": sum(1 for c in containers if c["error"]),
    }

    return {"containers": containers, "summary": summary}


@app.get("/api/refresh")
async def refresh():
    checker.clear_cache()
    return await get_containers()


@app.post("/api/containers/{container_id}/update")
async def update_container(container_id: str):
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def run_update():
        for event in checker.update_container_stream(container_id):
            loop.call_soon_threadsafe(queue.put_nowait, event)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, run_update)

    async def event_stream():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield _sse_line(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post("/api/update-all")
async def update_all():
    containers_data = await checker.get_all_containers()
    updatable = [c for c in containers_data if c.get("update_available")]

    if not updatable:
        async def empty_stream():
            yield _sse_line({"type": "complete", "success": True, "message": "Keine Updates verfügbar"})

        return StreamingResponse(empty_stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def run_updates():
        total = len(updatable)
        success_count = 0
        for i, c in enumerate(updatable):
            loop.call_soon_threadsafe(queue.put_nowait, {
                "type": "container_start",
                "container": c["name"],
                "index": i + 1,
                "total": total,
            })
            last_success = False
            for event in checker.update_container_stream(c["id"]):
                loop.call_soon_threadsafe(queue.put_nowait, event)
                if event.get("type") == "complete":
                    last_success = event.get("success", False)
            if last_success:
                success_count += 1

        loop.call_soon_threadsafe(queue.put_nowait, {
            "type": "all_complete",
            "success": success_count == total,
            "message": f"{success_count}/{total} Container aktualisiert",
        })
        loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, run_updates)

    async def event_stream():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield _sse_line(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True))
