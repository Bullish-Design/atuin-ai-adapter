"""End-to-end tests for adapter integration.

Tier 1 (HTTP-level) always runs and is the stable CI coverage.
Tier 2 (CLI-level) is opt-in via RUN_CLI_E2E=1 because PTY driving is fragile.
"""

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
import pytest
import uvicorn

from atuin_ai_adapter.config import get_settings
from tests.conftest import extract_events, extract_text, load_call, parse_sse_frames, save_response


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
    def __init__(self, app: object, host: str, port: int) -> None:
        self.server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


class TestHttpE2EWithDummyUpstream:
    def test_simple_call_through_real_servers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tests.helpers.dummy_openai_server as dummy

        dummy.REQUEST_COUNT = 0
        upstream_port = _free_port()
        adapter_port = _free_port()

        dummy_server = UvicornThread(dummy.app, host="127.0.0.1", port=upstream_port)
        dummy_server.start()

        monkeypatch.setenv("VLLM_BASE_URL", f"http://127.0.0.1:{upstream_port}")
        monkeypatch.setenv("VLLM_MODEL", "dummy-model")
        monkeypatch.setenv("ADAPTER_API_TOKEN", "e2e-test-token")
        monkeypatch.setenv("ADAPTER_PORT", str(adapter_port))
        get_settings.cache_clear()

        from atuin_ai_adapter.app import app as adapter_app

        adapter_server = UvicornThread(adapter_app, host="127.0.0.1", port=adapter_port)
        adapter_server.start()

        try:
            _wait_http_ok(f"http://127.0.0.1:{upstream_port}/v1/models")
            _wait_http_ok(f"http://127.0.0.1:{adapter_port}/health")

            resp = httpx.post(
                f"http://127.0.0.1:{adapter_port}/api/cli/chat",
                headers={
                    "Authorization": "Bearer e2e-test-token",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                json=load_call("simple"),
                timeout=30.0,
            )

            assert resp.status_code == 200
            frames = parse_sse_frames(resp.text)
            assert "text" in extract_events(frames)
            assert extract_events(frames)[-1] == "done"
            assert dummy.REQUEST_COUNT > 0
            assert "DUMMY_E2E_TOKEN" in extract_text(frames)
            save_response("e2e_simple_dummy", resp.text, tag="http")
        finally:
            adapter_server.stop()
            dummy_server.stop()
            get_settings.cache_clear()

    def test_all_fixtures_through_real_servers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tests.helpers.dummy_openai_server as dummy

        dummy.REQUEST_COUNT = 0
        upstream_port = _free_port()
        adapter_port = _free_port()

        dummy_server = UvicornThread(dummy.app, host="127.0.0.1", port=upstream_port)
        dummy_server.start()

        monkeypatch.setenv("VLLM_BASE_URL", f"http://127.0.0.1:{upstream_port}")
        monkeypatch.setenv("VLLM_MODEL", "dummy-model")
        monkeypatch.setenv("ADAPTER_API_TOKEN", "e2e-test-token")
        get_settings.cache_clear()

        from atuin_ai_adapter.app import app as adapter_app

        adapter_server = UvicornThread(adapter_app, host="127.0.0.1", port=adapter_port)
        adapter_server.start()

        try:
            _wait_http_ok(f"http://127.0.0.1:{upstream_port}/v1/models")
            _wait_http_ok(f"http://127.0.0.1:{adapter_port}/health")

            for fixture_name in ["simple", "conversation", "with_tools", "minimal", "no_context"]:
                resp = httpx.post(
                    f"http://127.0.0.1:{adapter_port}/api/cli/chat",
                    headers={
                        "Authorization": "Bearer e2e-test-token",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                    json=load_call(fixture_name),
                    timeout=30.0,
                )
                assert resp.status_code == 200
                frames = parse_sse_frames(resp.text)
                assert extract_events(frames)[-1] == "done"
                save_response(f"e2e_{fixture_name}_dummy", resp.text, tag="http")

            assert dummy.REQUEST_COUNT >= 5
        finally:
            adapter_server.stop()
            dummy_server.stop()
            get_settings.cache_clear()

    def test_auth_rejection_through_real_servers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tests.helpers.dummy_openai_server as dummy

        upstream_port = _free_port()
        adapter_port = _free_port()

        dummy_server = UvicornThread(dummy.app, host="127.0.0.1", port=upstream_port)
        dummy_server.start()

        monkeypatch.setenv("VLLM_BASE_URL", f"http://127.0.0.1:{upstream_port}")
        monkeypatch.setenv("VLLM_MODEL", "dummy-model")
        monkeypatch.setenv("ADAPTER_API_TOKEN", "e2e-test-token")
        get_settings.cache_clear()

        from atuin_ai_adapter.app import app as adapter_app

        adapter_server = UvicornThread(adapter_app, host="127.0.0.1", port=adapter_port)
        adapter_server.start()

        try:
            _wait_http_ok(f"http://127.0.0.1:{adapter_port}/health")

            resp = httpx.post(
                f"http://127.0.0.1:{adapter_port}/api/cli/chat",
                headers={"Authorization": "Bearer wrong-token"},
                json=load_call("simple"),
                timeout=10.0,
            )
            assert resp.status_code == 401
        finally:
            adapter_server.stop()
            dummy_server.stop()
            get_settings.cache_clear()


cli_e2e = pytest.mark.skipif(
    os.getenv("RUN_CLI_E2E") != "1",
    reason="Set RUN_CLI_E2E=1 to run Atuin CLI E2E tests.",
)


def _write_atuin_config(config_dir: Path, endpoint: str, token: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[ai]",
                "enabled = true",
                f'endpoint = "{endpoint}"',
                f'api_token = "{token}"',
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


def _drive_atuin_inline(config_dir: Path, prompt: str, run_s: float = 15.0) -> str:
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
            if not sent_prompt and len(output) > 50:
                time.sleep(1.0)
                os.write(master_fd, prompt.encode("utf-8") + b"\n")
                sent_prompt = True

            ready, _, _ = select.select([master_fd], [], [], 0.25)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)

            if proc.poll() is not None:
                break

        try:
            os.write(master_fd, b"\x1b")
            time.sleep(0.5)
        except OSError:
            pass
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    return output.decode("utf-8", errors="ignore")


@cli_e2e
def test_atuin_cli_smoke_with_dummy_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    import tests.helpers.dummy_openai_server as dummy

    dummy.REQUEST_COUNT = 0
    upstream_port = _free_port()
    adapter_port = 8787

    dummy_server = UvicornThread(dummy.app, host="127.0.0.1", port=upstream_port)
    dummy_server.start()

    monkeypatch.setenv("VLLM_BASE_URL", f"http://127.0.0.1:{upstream_port}")
    monkeypatch.setenv("VLLM_MODEL", "dummy-model")
    monkeypatch.setenv("ADAPTER_API_TOKEN", "local-dev-token")
    get_settings.cache_clear()

    from atuin_ai_adapter.app import app as adapter_app

    adapter_server = UvicornThread(adapter_app, host="127.0.0.1", port=adapter_port)
    adapter_server.start()

    with tempfile.TemporaryDirectory(prefix="atuin-e2e-") as tmp_dir:
        cfg_dir = Path(tmp_dir)
        _write_atuin_config(cfg_dir, f"http://127.0.0.1:{adapter_port}", "local-dev-token")

        try:
            _wait_http_ok(f"http://127.0.0.1:{upstream_port}/v1/models")
            _wait_http_ok(f"http://127.0.0.1:{adapter_port}/health")
            output = _drive_atuin_inline(cfg_dir, "list files by size")
            assert "Atuin AI is not yet configured" not in output
            if dummy.REQUEST_COUNT > 0:
                save_response("cli_smoke_dummy", output, tag="pty")
        finally:
            adapter_server.stop()
            dummy_server.stop()
            get_settings.cache_clear()
