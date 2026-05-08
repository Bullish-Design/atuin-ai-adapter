from __future__ import annotations

import argparse
import asyncio
import json

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()
REQUEST_COUNT = 0


@app.get("/v1/models")
async def models() -> dict[str, object]:
    return {"object": "list", "data": [{"id": "dummy-model", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat_completions() -> StreamingResponse:
    global REQUEST_COUNT
    REQUEST_COUNT += 1

    async def gen():
        chunks = [
            {"choices": [{"delta": {"content": "DUMMY_E2E_TOKEN"}}]},
            {"choices": [{"delta": {"content": "_OK"}}]},
        ]
        for chunk in chunks:
            yield f"data: {json.dumps(chunk)}\n\n"
            await asyncio.sleep(0.05)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/metrics")
async def metrics() -> dict[str, int]:
    return {"request_count": REQUEST_COUNT}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
