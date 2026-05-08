from __future__ import annotations

import os
import pty
import select
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from atuin_ai_adapter.config import get_settings


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http_ok(url: str, timeout_s: float = 30.0) -> None:
    import urllib.request

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if 200 <= resp.status < 500:
                    return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for {url}")


class UvicornThread:
    def __init__(self, app, host: str, port: int) -> None:  # type: ignore[no-untyped-def]
        self.server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _drive_atuin_inline(config_dir: Path, prompt: str, run_s: float = 12.0) -> str:
    shell_cmd = (
        f"ATUIN_CONFIG_DIR={config_dir} "
        "devenv shell -- atuin ai inline "
        "--api-endpoint http://127.0.0.1:8787 --api-token local-dev-token"
    )

    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        ["zsh", "-lc", shell_cmd],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    output = bytearray()
    sent_prompt = False
    deadline = time.time() + run_s

    try:
        while time.time() < deadline:
            if not sent_prompt and len(output) > 0:
                os.write(master_fd, prompt.encode("utf-8") + b"\r")
                sent_prompt = True
            ready, _, _ = select.select([master_fd], [], [], 0.25)
            if ready:
                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                output.extend(chunk)
            if proc.poll() is not None:
                break

        os.write(master_fd, b"\x1b")
        time.sleep(0.5)
    finally:
        os.close(master_fd)
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)

    return output.decode("utf-8", errors="ignore")


def _write_atuin_config(config_dir: Path, endpoint: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[ai]",
                "enabled = true",
                f'endpoint = "{endpoint}"',
                'api_token = "local-dev-token"',
                "",
                "[ai.opening]",
                "send_cwd = true",
                "send_last_command = true",
                "",
                "[ai.capabilities]",
                "enable_history_search = false",
                "enable_file_tools = false",
                "enable_command_execution = false",
            ]
        )
    )


def test_atuin_cli_e2e_with_dummy_upstream() -> None:
    import tests.helpers.dummy_openai_server as dummy

    dummy.REQUEST_COUNT = 0
    upstream_port = _free_port()
    dummy_server = UvicornThread(dummy.app, host="127.0.0.1", port=upstream_port)
    dummy_server.start()

    os.environ["VLLM_BASE_URL"] = f"http://127.0.0.1:{upstream_port}"
    os.environ["VLLM_MODEL"] = "dummy-model"
    os.environ["ADAPTER_API_TOKEN"] = "local-dev-token"
    get_settings.cache_clear()

    from atuin_ai_adapter.app import app as adapter_app

    adapter_server = UvicornThread(adapter_app, host="127.0.0.1", port=8787)
    adapter_server.start()

    with tempfile.TemporaryDirectory(prefix="atuin-e2e-dummy-") as tmp_dir:
        cfg_dir = Path(tmp_dir)
        _write_atuin_config(cfg_dir, "http://127.0.0.1:8787")

        try:
            _wait_http_ok(f"http://127.0.0.1:{upstream_port}/v1/models")
            _wait_http_ok("http://127.0.0.1:8787/health")
            output = _drive_atuin_inline(cfg_dir, "list files by size")
            assert "Atuin AI is not yet configured" not in output
            assert dummy.REQUEST_COUNT > 0
        finally:
            adapter_server.stop()
            dummy_server.stop()


def test_atuin_cli_e2e_with_real_upstream() -> None:
    proxy_app = FastAPI()
    state: dict[str, int] = {"requests": 0, "done": 0}

    @proxy_app.get("/v1/models")
    async def models() -> object:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get("http://remora-server:8000/v1/models")
        return resp.json()

    @proxy_app.post("/v1/chat/completions")
    async def chat(payload: dict) -> StreamingResponse:
        state["requests"] += 1

        async def stream():
            async with httpx.AsyncClient(timeout=90.0) as client:
                async with client.stream(
                    "POST",
                    "http://remora-server:8000/v1/chat/completions",
                    json=payload,
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip() == "data: [DONE]":
                            state["done"] += 1
                        yield (line + "\n").encode("utf-8")

        return StreamingResponse(stream(), media_type="text/event-stream")

    proxy_port = _free_port()
    proxy_server = UvicornThread(proxy_app, host="127.0.0.1", port=proxy_port)
    proxy_server.start()

    os.environ["VLLM_BASE_URL"] = f"http://127.0.0.1:{proxy_port}"
    os.environ["VLLM_MODEL"] = "Qwen3.5-9B-UD-Q6_K_XL.gguf"
    os.environ["ADAPTER_API_TOKEN"] = "local-dev-token"
    get_settings.cache_clear()

    from atuin_ai_adapter.app import app as adapter_app

    adapter_server = UvicornThread(adapter_app, host="127.0.0.1", port=8787)
    adapter_server.start()

    with tempfile.TemporaryDirectory(prefix="atuin-e2e-real-") as tmp_dir:
        cfg_dir = Path(tmp_dir)
        _write_atuin_config(cfg_dir, "http://127.0.0.1:8787")

        try:
            _wait_http_ok(f"http://127.0.0.1:{proxy_port}/v1/models")
            _wait_http_ok("http://127.0.0.1:8787/health")
            output = _drive_atuin_inline(cfg_dir, "return a short shell command example")
            assert "Atuin AI is not yet configured" not in output
            assert state["requests"] > 0
            assert state["done"] > 0
        finally:
            adapter_server.stop()
            proxy_server.stop()
