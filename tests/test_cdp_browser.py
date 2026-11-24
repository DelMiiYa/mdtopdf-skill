"""Unit and E2E tests for mdtopdf_skill.cdp_browser and assets/render_bridge.js."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import PIL.Image
import pymupdf
import pytest

from mdtopdf_skill import cdp_browser
from mdtopdf_skill.cdp_browser import (
    DEFAULT_CDP_TIMEOUT_SECONDS,
    CdpProtocolError,
    CdpSession,
    CdpTimeoutError,
    _is_fetch_url_allowed,
    find_browser_executable,
    resolve_cdp_timeout,
)
from mdtopdf_skill.models import PageGeometry, compute_page_geometry

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_ASSETS_DIR = Path(__file__).resolve().parent.parent / "src" / "mdtopdf_skill" / "assets"


def _ws_recv_exact(conn: socket.socket, num_bytes: int) -> bytes:
    """Read exactly num_bytes from a TCP socket."""
    chunks: list[bytes] = []
    remaining = num_bytes
    while remaining > 0:
        data = conn.recv(remaining)
        if not data:
            raise EOFError("Socket closed by client before expected bytes arrived")
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


def _ws_read_client_frame(conn: socket.socket) -> tuple[bool, int, bool, int, bytes]:
    """Parse one RFC6455 client frame, returning (fin, opcode, masked, len_marker, payload)."""
    b1, b2 = _ws_recv_exact(conn, 2)
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    len_marker = b2 & 0x7F
    if len_marker == 126:
        (length,) = struct.unpack("!H", _ws_recv_exact(conn, 2))
    elif len_marker == 127:
        (length,) = struct.unpack("!Q", _ws_recv_exact(conn, 8))
    else:
        length = len_marker
    mask_key = _ws_recv_exact(conn, 4) if masked else b""
    raw_payload = _ws_recv_exact(conn, length) if length > 0 else b""
    if masked:
        payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(raw_payload))
    else:
        payload = raw_payload
    return fin, opcode, masked, len_marker, payload


def _ws_build_server_frame(
    opcode: int,
    payload: bytes,
    *,
    fin: bool = True,
) -> bytes:
    """Encode an unmasked RFC6455 server frame."""
    first_byte = (0x80 if fin else 0x00) | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first_byte, length)
    elif length <= 0xFFFF:
        header = struct.pack("!BBH", first_byte, 126, length)
    else:
        header = struct.pack("!BBQ", first_byte, 127, length)
    return header + payload


class _LoopbackCdpServer:
    """Real localhost TCP RFC6455 WebSocket + CDP server for protocol unit tests."""

    def __init__(
        self,
        handler: Callable[[socket.socket, dict[str, str]], None] | None = None,
    ) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self._sock.settimeout(5.0)
        addr = self._sock.getsockname()
        self.port: int = int(addr[1])
        self.ws_path = "/devtools/browser/test-browser-id"
        self.requests: list[dict[str, Any]] = []
        self.raw_frames: list[tuple[bool, int, bool, int, bytes]] = []
        self.handshake_request: str = ""
        self.handshake_headers: dict[str, str] = {}
        self.errors: list[BaseException] = []
        self._handler = handler
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> _LoopbackCdpServer:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._sock.close()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5.0)
            try:
                headers = self._perform_handshake(conn)
                if headers is None:
                    return
                if self._handler is not None:
                    self._handler(conn, headers)
                else:
                    self._default_cdp_loop(conn)
            except BaseException as exc:  # pragma: no cover - recorded for assertion
                if not self._stop.is_set():
                    self.errors.append(exc)

    def _perform_handshake(self, conn: socket.socket) -> dict[str, str] | None:
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return None
            buf.extend(chunk)
        marker = buf.index(b"\r\n\r\n")
        self.handshake_request = buf[:marker].decode("latin-1")
        lines = self.handshake_request.split("\r\n")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        self.handshake_headers = headers

        ws_key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(
            hashlib.sha1(
                (ws_key + _WS_GUID).encode("ascii"),
                usedforsecurity=False,
            ).digest()
        ).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        ).encode("ascii")
        conn.sendall(response)
        return headers

    def _default_cdp_loop(self, conn: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                fin, opcode, masked, len_marker, payload = _ws_read_client_frame(conn)
            except (EOFError, OSError, TimeoutError):
                return
            self.raw_frames.append((fin, opcode, masked, len_marker, payload))
            if opcode == 0x8:
                return
            if opcode != 0x1:
                continue
            msg = json.loads(payload.decode("utf-8"))
            self.requests.append(msg)
            cmd_id = msg["id"]
            method = msg["method"]
            session_id = msg.get("sessionId")
            result: dict[str, Any] = {}
            if method == "Browser.getVersion":
                result = {"product": "Chrome/157.0.0.0"}
            elif method == "Target.createTarget":
                result = {"targetId": "target-1"}
            elif method == "Target.attachToTarget":
                result = {"sessionId": "session-1"}
            elif method == "Runtime.evaluate":
                result = {"result": {"type": "number", "value": 2}}
            elif method == "Page.printToPDF":
                result = {"data": base64.b64encode(b"%PDF-1.4\n%%EOF\n").decode("ascii")}
            elif method == "Page.navigate":
                reply = {"id": cmd_id, "result": {"frameId": "frame-1"}}
                conn.sendall(_ws_build_server_frame(0x1, json.dumps(reply).encode("utf-8")))
                event = {
                    "method": "Page.loadEventFired",
                    "params": {"timestamp": 1.0},
                    "sessionId": session_id,
                }
                conn.sendall(_ws_build_server_frame(0x1, json.dumps(event).encode("utf-8")))
                continue
            reply = {"id": cmd_id, "result": result}
            conn.sendall(_ws_build_server_frame(0x1, json.dumps(reply).encode("utf-8")))


class _FakePipe:
    """In-memory byte pipe yielding preset lines and blocking or finishing on EOF."""

    def __init__(self, lines: list[bytes], *, stall_after: bool = False) -> None:
        self._lines = list(lines)
        self._idx = 0
        self._stall_after = stall_after
        self._closed = threading.Event()

    def readline(self) -> bytes:
        if self._idx < len(self._lines):
            line = self._lines[self._idx]
            self._idx += 1
            return line
        if self._stall_after:
            self._closed.wait(timeout=10.0)
        return b""

    def close(self) -> None:
        self._closed.set()


class _FakeProc:
    """Mock subprocess.Popen tracking lifecycle calls."""

    def __init__(
        self,
        stderr_lines: list[bytes],
        *,
        stall_stderr: bool = False,
        exit_code: int | None = None,
        wait_timeout_once: bool = False,
    ) -> None:
        self.stderr = _FakePipe(stderr_lines, stall_after=stall_stderr)
        self.returncode: int | None = exit_code
        self.terminated = False
        self.killed = False
        self.wait_calls: list[float | None] = []
        self._wait_timeout_once = wait_timeout_once

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.stderr.close()
        if not self._wait_timeout_once:
            self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.stderr.close()
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self._wait_timeout_once and not self.killed:
            self._wait_timeout_once = False
            raise subprocess.TimeoutExpired(cmd="fake-browser", timeout=timeout or 0.0)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _patch_fake_popen(
    monkeypatch: pytest.MonkeyPatch,
    proc: _FakeProc,
    captured_calls: list[tuple[list[str], dict[str, Any]]] | None = None,
) -> None:
    """Monkeypatch subprocess.Popen inside cdp_browser to return proc."""

    def _fake_popen(cmd: list[str], **kwargs: Any) -> _FakeProc:
        if captured_calls is not None:
            captured_calls.append((list(cmd), dict(kwargs)))
        return proc

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)


class TestFindBrowserExecutable:
    """Browser discovery priority and error reporting tests."""

    def test_mdtopdf_browser_env_override_valid_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        custom = tmp_path / "custom-browser.exe"
        custom.write_bytes(b"")
        monkeypatch.setenv("MDTOPDF_BROWSER", str(custom))
        assert find_browser_executable() == str(custom)

    def test_mdtopdf_browser_explicit_invalid_raises_immediately(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        missing = tmp_path / "missing-browser.exe"
        monkeypatch.setenv("MDTOPDF_BROWSER", str(missing))
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name: "/usr/bin/chrome" if name == "chrome" else None,
        )
        with pytest.raises(FileNotFoundError, match="MDTOPDF_BROWSER"):
            find_browser_executable()

    @pytest.mark.parametrize("raw_val", ["", "   ", "__DIR__", "__MISSING__"])
    def test_mdtopdf_browser_invalid_values_never_returned(
        self,
        raw_val: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        if raw_val == "__DIR__":
            monkeypatch.setenv("MDTOPDF_BROWSER", str(tmp_path))
        elif raw_val == "__MISSING__":
            monkeypatch.setenv("MDTOPDF_BROWSER", str(tmp_path / "nope.exe"))
        else:
            monkeypatch.setenv("MDTOPDF_BROWSER", raw_val)
        monkeypatch.setattr(cdp_browser, "_candidate_browser_paths", list)
        monkeypatch.setattr(shutil, "which", lambda _: None)
        with pytest.raises(FileNotFoundError, match="MDTOPDF_BROWSER"):
            find_browser_executable()

    def test_standard_paths_windows_and_linux(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MDTOPDF_BROWSER", raising=False)
        monkeypatch.setattr(shutil, "which", lambda _: None)

        # Windows branch
        monkeypatch.setattr(sys, "platform", "win32")
        pf86 = tmp_path / "pf86"
        edge_exe = pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        edge_exe.parent.mkdir(parents=True)
        edge_exe.write_bytes(b"")
        monkeypatch.setenv("PROGRAMFILES(X86)", str(pf86))
        monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        assert find_browser_executable() == str(edge_exe)

        # Linux branch
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            Path,
            "is_file",
            lambda self: str(self).replace("\\", "/") == "/usr/bin/google-chrome",
        )
        assert find_browser_executable().replace("\\", "/") == "/usr/bin/google-chrome"

    def test_shutil_which_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MDTOPDF_BROWSER", raising=False)
        monkeypatch.setattr(cdp_browser, "_candidate_browser_paths", list)
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name: "/opt/custom/chromium" if name == "chromium" else None,
        )
        assert find_browser_executable() == "/opt/custom/chromium"

    def test_not_found_raises_file_not_found_mentioning_env_var(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MDTOPDF_BROWSER", raising=False)
        monkeypatch.setattr(cdp_browser, "_candidate_browser_paths", list)
        monkeypatch.setattr(shutil, "which", lambda _: None)
        with pytest.raises(FileNotFoundError, match="MDTOPDF_BROWSER"):
            find_browser_executable()


class TestCdpSessionLifecycleAndFlags:
    """Process launch flags, stderr DevTools parsing, startup timeouts, and cleanup tests."""

    def test_launch_flags_and_isolated_temp_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MDTOPDF_NO_SANDBOX", raising=False)
        with _LoopbackCdpServer() as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            calls: list[tuple[list[str], dict[str, Any]]] = []
            _patch_fake_popen(monkeypatch, proc, calls)

            observed_profile_dir: Path | None = None
            with CdpSession("fake-browser"):
                assert len(calls) == 1
                cmd, kwargs = calls[0]
                assert cmd[0] == "fake-browser"
                for expected_flag in (
                    "--headless",
                    "--remote-debugging-address=127.0.0.1",
                    "--remote-debugging-port=0",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-default-apps",
                    "about:blank",
                ):
                    assert expected_flag in cmd
                user_data_flags = [f for f in cmd if f.startswith("--user-data-dir=")]
                crash_flags = [f for f in cmd if f.startswith("--crash-dumps-dir=")]
                assert len(user_data_flags) == 1
                assert len(crash_flags) == 1
                profile_path = Path(user_data_flags[0].split("=", 1)[1])
                assert profile_path == Path(crash_flags[0].split("=", 1)[1])
                assert profile_path.name.startswith("mdtopdf_cdp_")
                assert profile_path.is_dir()
                observed_profile_dir = profile_path
                assert kwargs["stdin"] == subprocess.DEVNULL
                assert kwargs["stdout"] == subprocess.DEVNULL
                assert kwargs["stderr"] == subprocess.PIPE

            assert observed_profile_dir is not None
            assert not observed_profile_dir.exists()

    def test_windows_creationflags_create_no_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MDTOPDF_NO_SANDBOX", raising=False)
        with _LoopbackCdpServer() as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            calls: list[tuple[list[str], dict[str, Any]]] = []
            _patch_fake_popen(monkeypatch, proc, calls)
            monkeypatch.setattr(sys, "platform", "win32")
            monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

            with CdpSession("msedge.exe"):
                pass

            cmd, kwargs = calls[0]
            assert "--no-sandbox" not in cmd
            assert kwargs["creationflags"] == 0x08000000

    @pytest.mark.parametrize(
        ("opt_val", "euid", "expect_no_sandbox"),
        [
            ("1", 1000, True),
            ("true", 1000, True),
            ("", 0, False),
            ("0", 1000, False),
        ],
    )
    def test_linux_no_sandbox_only_when_explicit_opt_in(
        self,
        opt_val: str,
        euid: int,
        expect_no_sandbox: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _LoopbackCdpServer() as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            calls: list[tuple[list[str], dict[str, Any]]] = []
            _patch_fake_popen(monkeypatch, proc, calls)
            monkeypatch.setattr(sys, "platform", "linux")
            monkeypatch.setattr(os, "geteuid", lambda: euid, raising=False)
            monkeypatch.setenv("CI", "true")  # CI alone must NOT disable the sandbox
            if opt_val:
                monkeypatch.setenv("MDTOPDF_NO_SANDBOX", opt_val)
            else:
                monkeypatch.delenv("MDTOPDF_NO_SANDBOX", raising=False)

            with CdpSession("/usr/bin/google-chrome"):
                pass

            cmd, kwargs = calls[0]
            assert ("--no-sandbox" in cmd) is expect_no_sandbox
            assert "creationflags" not in kwargs

    def test_devtools_stderr_parsing_ignores_noise_and_handles_crlf(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _LoopbackCdpServer() as server:
            proc = _FakeProc(
                [
                    b"[INFO] Initializing headless shell\r\n",
                    b"[WARN] GPU disabled\r\n",
                    (
                        f"DevTools listening on ws://127.0.0.1:{server.port}"
                        f"{server.ws_path}\r\n"
                    ).encode(),
                ]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake-browser") as session:
                assert session.browser_version == "Chrome/157.0.0.0"
            assert f"GET {server.ws_path} HTTP/1.1" in server.handshake_request
            assert server.handshake_headers["host"] == f"127.0.0.1:{server.port}"

    def test_startup_timeout_and_early_process_exit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Case 1: stalled stderr -> CdpTimeoutError + cleanup
        proc_stall = _FakeProc([b"[INFO] starting...\n"], stall_stderr=True)
        _patch_fake_popen(monkeypatch, proc_stall)
        with pytest.raises(CdpTimeoutError), CdpSession("fake-browser", startup_timeout=0.15):
            pass
        assert proc_stall.terminated

        # Case 2: immediate EOF / crash exit -> CdpProtocolError immediately + cleanup
        proc_exit = _FakeProc([b"Fatal error: missing shared lib\n"], exit_code=127)
        _patch_fake_popen(monkeypatch, proc_exit)
        start = time.monotonic()
        with pytest.raises(CdpProtocolError, match="stderr"), CdpSession(
            "fake-browser", startup_timeout=10.0
        ):
            pass
        assert time.monotonic() - start < 2.0

    def test_guaranteed_finally_cleanup_on_exception_and_hung_proc(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Exception inside with-block + hung process requiring kill()
        with _LoopbackCdpServer() as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()],
                wait_timeout_once=True,
            )
            calls: list[tuple[list[str], dict[str, Any]]] = []
            _patch_fake_popen(monkeypatch, proc, calls)

            profile_dir: Path | None = None
            with pytest.raises(RuntimeError, match="boom"), CdpSession("fake-browser"):
                user_flag = [a for a in calls[0][0] if a.startswith("--user-data-dir=")][0]
                profile_dir = Path(user_flag.split("=", 1)[1])
                raise RuntimeError("boom")

            assert proc.terminated
            assert proc.killed
            assert proc.wait_calls == [2.0, 2.0]
            assert profile_dir is not None and not profile_dir.exists()

        # Exception during Target.createTarget in __enter__
        def _fail_create_target(conn: socket.socket, headers: dict[str, str]) -> None:
            _ = headers
            while True:
                _, opcode, _, _, payload = _ws_read_client_frame(conn)
                if opcode != 0x1:
                    continue
                msg = json.loads(payload.decode("utf-8"))
                if msg["method"] == "Browser.getVersion":
                    reply: dict[str, Any] = {"id": msg["id"], "result": {"product": "Edge/157"}}
                else:
                    reply = {
                        "id": msg["id"],
                        "error": {"code": -32000, "message": "createTarget boom"},
                    }
                    conn.sendall(_ws_build_server_frame(0x1, json.dumps(reply).encode()))
                    return
                conn.sendall(_ws_build_server_frame(0x1, json.dumps(reply).encode()))

        with _LoopbackCdpServer(_fail_create_target) as server:
            proc2 = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            calls2: list[tuple[list[str], dict[str, Any]]] = []
            _patch_fake_popen(monkeypatch, proc2, calls2)
            with pytest.raises(CdpProtocolError, match="createTarget boom"), CdpSession("fake"):
                pass
            assert proc2.terminated
            user_flag2 = [a for a in calls2[0][0] if a.startswith("--user-data-dir=")][0]
            assert not Path(user_flag2.split("=", 1)[1]).exists()


class TestRfc6455WebSocketProtocol:
    """Real loopback socket tests for RFC6455 framing, buffering, and timeouts."""

    def test_http_upgrade_handshake_and_accept_validation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Drive directly with custom handshake so server doesn't send valid 101 first
        def _custom_bad_accept_server(accept_line: bytes) -> None:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = int(srv.getsockname()[1])

            def _serve_once() -> None:
                conn, _ = srv.accept()
                with conn:
                    buf = bytearray()
                    while b"\r\n\r\n" not in buf:
                        buf.extend(conn.recv(4096))
                    conn.sendall(accept_line)

            t = threading.Thread(target=_serve_once, daemon=True)
            t.start()
            proc = _FakeProc([f"DevTools listening on ws://127.0.0.1:{port}/devtools/b\n".encode()])
            _patch_fake_popen(monkeypatch, proc)
            try:
                with pytest.raises(CdpProtocolError), CdpSession("fake"):
                    pass
                assert proc.terminated
            finally:
                srv.close()
                t.join(timeout=2.0)

        _custom_bad_accept_server(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: wrong==\r\n\r\n"
        )
        _custom_bad_accept_server(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")

    def test_client_frames_are_masked_and_lengths_encoded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _LoopbackCdpServer() as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake") as session:
                session.evaluate("1 + 1")  # < 126 bytes
                session.inject_style("s1", "body { color: red; } " * 60)  # 126..65535 (126)
                session.inject_style("s2", "/* " + ("a" * 70_000) + " */")  # >= 65536 (127)

            markers = [frame[3] for frame in server.raw_frames if frame[1] == 0x1]
            assert all(frame[0] and frame[2] for frame in server.raw_frames if frame[1] == 0x1)
            assert any(m < 126 for m in markers)
            assert 126 in markers
            assert 127 in markers

    def test_server_frames_126_and_127_decoding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        medium_str = "M" * 2000
        large_str = "L" * 100_000

        def _handler(conn: socket.socket, headers: dict[str, str]) -> None:
            _ = headers
            while True:
                _, opcode, _, _, payload = _ws_read_client_frame(conn)
                if opcode == 0x8:
                    return
                if opcode != 0x1:
                    continue
                msg = json.loads(payload.decode("utf-8"))
                method = msg["method"]
                res: dict[str, Any] = {}
                if method == "Target.createTarget":
                    res = {"targetId": "t1"}
                elif method == "Target.attachToTarget":
                    res = {"sessionId": "s1"}
                elif method == "Runtime.evaluate":
                    expr = msg["params"]["expression"]
                    val = medium_str if expr == "medium" else large_str
                    res = {"result": {"type": "string", "value": val}}
                reply = json.dumps({"id": msg["id"], "result": res}).encode("utf-8")
                conn.sendall(_ws_build_server_frame(0x1, reply))

        with _LoopbackCdpServer(_handler) as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake") as session:
                assert session.evaluate("medium") == medium_str
                assert session.evaluate("large") == large_str

    def test_fragmented_server_frames_and_interleaved_ping_pong(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        observed_pong: list[bytes] = []

        def _handler(conn: socket.socket, headers: dict[str, str]) -> None:
            _ = headers
            while True:
                _, opcode, _, _, payload = _ws_read_client_frame(conn)
                if opcode == 0x8:
                    return
                if opcode != 0x1:
                    continue
                msg = json.loads(payload.decode("utf-8"))
                method = msg["method"]
                if method == "Target.createTarget":
                    reply = json.dumps({"id": msg["id"], "result": {"targetId": "t1"}}).encode()
                    conn.sendall(_ws_build_server_frame(0x1, reply))
                elif method == "Target.attachToTarget":
                    reply = json.dumps({"id": msg["id"], "result": {"sessionId": "s1"}}).encode()
                    conn.sendall(_ws_build_server_frame(0x1, reply))
                elif method == "Runtime.evaluate":
                    full = json.dumps(
                        {"id": msg["id"], "result": {"result": {"type": "string", "value": "ok"}}}
                    ).encode("utf-8")
                    p1, p2, p3 = full[:15], full[15:35], full[35:]
                    conn.sendall(_ws_build_server_frame(0x1, p1, fin=False))
                    # Interleave PING inside fragmented message per RFC6455 §5.4
                    conn.sendall(_ws_build_server_frame(0x9, b"hb", fin=True))
                    fin_p, op_p, masked_p, _, pong_payload = _ws_read_client_frame(conn)
                    assert fin_p and op_p == 0xA and masked_p
                    observed_pong.append(pong_payload)
                    conn.sendall(_ws_build_server_frame(0x0, p2, fin=False))
                    # Unsolicited PONG should be ignored cleanly
                    conn.sendall(_ws_build_server_frame(0xA, b"unsolicited", fin=True))
                    conn.sendall(_ws_build_server_frame(0x0, p3, fin=True))
                else:
                    reply = json.dumps({"id": msg["id"], "result": {}}).encode()
                    conn.sendall(_ws_build_server_frame(0x1, reply))

        with _LoopbackCdpServer(_handler) as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake") as session:
                assert session.evaluate("frag") == "ok"
        assert observed_pong == [b"hb"]

    def test_server_close_frame_raises_protocol_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _handler(conn: socket.socket, headers: dict[str, str]) -> None:
            _ = headers
            while True:
                _, opcode, _, _, payload = _ws_read_client_frame(conn)
                if opcode != 0x1:
                    return
                msg = json.loads(payload.decode("utf-8"))
                method = msg["method"]
                if method == "Target.createTarget":
                    reply = json.dumps({"id": msg["id"], "result": {"targetId": "t1"}}).encode()
                    conn.sendall(_ws_build_server_frame(0x1, reply))
                elif method == "Target.attachToTarget":
                    reply = json.dumps({"id": msg["id"], "result": {"sessionId": "s1"}}).encode()
                    conn.sendall(_ws_build_server_frame(0x1, reply))
                elif method == "Runtime.evaluate":
                    close_payload = struct.pack("!H", 1001) + b"going away"
                    conn.sendall(_ws_build_server_frame(0x8, close_payload, fin=True))
                    return
                else:
                    reply = json.dumps({"id": msg["id"], "result": {}}).encode()
                    conn.sendall(_ws_build_server_frame(0x1, reply))

        with _LoopbackCdpServer(_handler) as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake") as session, pytest.raises(
                CdpProtocolError, match="going away"
            ):
                session.evaluate("1+1")

    def test_socket_timeout_and_mdtopdf_timeout_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MDTOPDF_TIMEOUT", raising=False)
        assert resolve_cdp_timeout() == DEFAULT_CDP_TIMEOUT_SECONDS
        monkeypatch.setenv("MDTOPDF_TIMEOUT", "0.2")
        assert resolve_cdp_timeout() == pytest.approx(0.2)

        for bad_val in ("abc", "0", "-5", "nan", "inf", "-inf"):
            monkeypatch.setenv("MDTOPDF_TIMEOUT", bad_val)
            with pytest.raises(ValueError):
                resolve_cdp_timeout()
        with pytest.raises(ValueError):
            resolve_cdp_timeout(True)

        monkeypatch.setenv("MDTOPDF_TIMEOUT", "0.2")

        def _silent_on_eval(conn: socket.socket, headers: dict[str, str]) -> None:
            _ = headers
            while True:
                try:
                    _, opcode, _, _, payload = _ws_read_client_frame(conn)
                except (EOFError, OSError, TimeoutError):
                    return
                if opcode != 0x1:
                    return
                msg = json.loads(payload.decode("utf-8"))
                if msg["method"] == "Runtime.evaluate":
                    time.sleep(1.5)
                    return
                res: dict[str, Any] = {}
                if msg["method"] == "Target.createTarget":
                    res = {"targetId": "t1"}
                elif msg["method"] == "Target.attachToTarget":
                    res = {"sessionId": "s1"}
                conn.sendall(
                    _ws_build_server_frame(
                        0x1, json.dumps({"id": msg["id"], "result": res}).encode()
                    )
                )

        with _LoopbackCdpServer(_silent_on_eval) as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake") as session, pytest.raises(CdpTimeoutError):
                session.evaluate("slow()")

    def test_cdp_protocol_error_and_runtime_exception_details(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _err_handler(conn: socket.socket, headers: dict[str, str]) -> None:
            _ = headers
            while True:
                _, opcode, _, _, payload = _ws_read_client_frame(conn)
                if opcode != 0x1:
                    return
                msg = json.loads(payload.decode("utf-8"))
                method = msg["method"]
                if method == "Target.createTarget":
                    reply: dict[str, Any] = {"id": msg["id"], "result": {"targetId": "t1"}}
                elif method == "Target.attachToTarget":
                    reply = {"id": msg["id"], "result": {"sessionId": "s1"}}
                elif method == "Page.printToPDF":
                    reply = {
                        "id": msg["id"],
                        "error": {"code": -32000, "message": "Target closed"},
                    }
                elif method == "Runtime.evaluate":
                    reply = {
                        "id": msg["id"],
                        "result": {
                            "result": {"type": "object"},
                            "exceptionDetails": {
                                "text": "Uncaught",
                                "exception": {"description": "ReferenceError: x is not defined"},
                            },
                        },
                    }
                else:
                    reply = {"id": msg["id"], "result": {}}
                conn.sendall(_ws_build_server_frame(0x1, json.dumps(reply).encode()))

        with _LoopbackCdpServer(_err_handler) as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake") as session:
                with pytest.raises(CdpProtocolError, match="ReferenceError: x is not defined"):
                    session.evaluate("x")
                with pytest.raises(CdpProtocolError, match="Target closed"):
                    session.print_to_pdf()


class TestCdpHighLevelMethods:
    """High-level CDP method parameters, sessionId routing, and PDF printing tests."""

    def test_target_creation_flatten_attach_and_domain_enable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _LoopbackCdpServer() as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake"):
                pass

            methods = [req["method"] for req in server.requests]
            assert methods == [
                "Browser.getVersion",
                "Target.createTarget",
                "Target.attachToTarget",
                "Page.enable",
                "Runtime.enable",
            ]
            assert server.requests[1]["params"] == {"url": "about:blank"}
            assert server.requests[2]["params"] == {"targetId": "target-1", "flatten": True}
            assert server.requests[3]["sessionId"] == "session-1"
            assert server.requests[4]["sessionId"] == "session-1"

    def test_emulate_print_viewport_uses_page_geometry(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _LoopbackCdpServer() as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            portrait = compute_page_geometry("A4 portrait", "15mm 16mm")
            landscape = compute_page_geometry("A4 landscape", "15mm 16mm")
            with CdpSession("fake") as session:
                session.emulate_print_viewport(portrait)
                session.emulate_print_viewport(landscape)

            emul_cmds = [r for r in server.requests if r["method"].startswith("Emulation.")]
            assert emul_cmds[0]["method"] == "Emulation.setEmulatedMedia"
            assert emul_cmds[0]["params"] == {"media": "print"}
            assert emul_cmds[1]["method"] == "Emulation.setDeviceMetricsOverride"
            assert emul_cmds[1]["params"] == {
                "width": round(portrait.printable_width_px),
                "height": round(portrait.printable_height_px),
                "deviceScaleFactor": 1,
                "mobile": False,
            }
            assert emul_cmds[3]["params"]["width"] > emul_cmds[3]["params"]["height"]

    def test_navigate_waits_for_load_event_fired_and_handles_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        html_file = tmp_path / "doc.html"
        html_file.write_text("<!DOCTYPE html><html><body>hi</body></html>", encoding="utf-8")

        # Case 1: event emitted BEFORE Page.navigate reply
        def _event_first(conn: socket.socket, headers: dict[str, str]) -> None:
            _ = headers
            while True:
                _, opcode, _, _, payload = _ws_read_client_frame(conn)
                if opcode != 0x1:
                    return
                msg = json.loads(payload.decode("utf-8"))
                method = msg["method"]
                if method == "Target.createTarget":
                    target_reply = json.dumps(
                        {"id": msg["id"], "result": {"targetId": "t1"}}
                    ).encode()
                    conn.sendall(_ws_build_server_frame(0x1, target_reply))
                elif method == "Target.attachToTarget":
                    conn.sendall(
                        _ws_build_server_frame(
                            0x1,
                            json.dumps({"id": msg["id"], "result": {"sessionId": "s1"}}).encode(),
                        )
                    )
                elif method == "Page.navigate":
                    ev = {
                        "method": "Page.loadEventFired",
                        "params": {"timestamp": 1.0},
                        "sessionId": msg["sessionId"],
                    }
                    conn.sendall(_ws_build_server_frame(0x1, json.dumps(ev).encode()))
                    rep = {"id": msg["id"], "result": {"frameId": "f1"}}
                    conn.sendall(_ws_build_server_frame(0x1, json.dumps(rep).encode()))
                else:
                    conn.sendall(
                        _ws_build_server_frame(
                            0x1, json.dumps({"id": msg["id"], "result": {}}).encode()
                        )
                    )

        with _LoopbackCdpServer(_event_first) as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake") as session:
                session.navigate(html_file)

        # Case 2: Page.navigate returns errorText -> CdpProtocolError
        def _nav_error(conn: socket.socket, headers: dict[str, str]) -> None:
            _ = headers
            while True:
                _, opcode, _, _, payload = _ws_read_client_frame(conn)
                if opcode != 0x1:
                    return
                msg = json.loads(payload.decode("utf-8"))
                method = msg["method"]
                res: dict[str, Any] = {}
                if method == "Target.createTarget":
                    res = {"targetId": "t1"}
                elif method == "Target.attachToTarget":
                    res = {"sessionId": "s1"}
                elif method == "Page.navigate":
                    res = {"frameId": "f1", "errorText": "net::ERR_FILE_NOT_FOUND"}
                conn.sendall(
                    _ws_build_server_frame(
                        0x1, json.dumps({"id": msg["id"], "result": res}).encode()
                    )
                )

        with _LoopbackCdpServer(_nav_error) as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake") as session, pytest.raises(
                CdpProtocolError, match="ERR_FILE_NOT_FOUND"
            ):
                session.navigate(html_file)

        # Case 3: Page.loadEventFired never arrives -> CdpTimeoutError
        def _nav_no_event(conn: socket.socket, headers: dict[str, str]) -> None:
            _ = headers
            while True:
                try:
                    _, opcode, _, _, payload = _ws_read_client_frame(conn)
                except (EOFError, OSError, TimeoutError):
                    return
                if opcode != 0x1:
                    return
                msg = json.loads(payload.decode("utf-8"))
                method = msg["method"]
                res: dict[str, Any] = {}
                if method == "Target.createTarget":
                    res = {"targetId": "t1"}
                elif method == "Target.attachToTarget":
                    res = {"sessionId": "s1"}
                elif method == "Page.navigate":
                    res = {"frameId": "f1"}
                conn.sendall(
                    _ws_build_server_frame(
                        0x1, json.dumps({"id": msg["id"], "result": res}).encode()
                    )
                )

        with _LoopbackCdpServer(_nav_no_event) as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake") as session, pytest.raises(CdpTimeoutError):
                session.navigate(html_file, timeout=0.15)

    def test_evaluate_and_inject_style_escaping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with _LoopbackCdpServer() as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake") as session:
                assert session.evaluate("1 + 1") == 2
                session.inject_style(
                    "mdtopdf-autofit-tables",
                    "table { font-size: 8pt; } /* </style> `backtick` 'quote' ภาษาไทย */",
                )

            eval_reqs = [r for r in server.requests if r["method"] == "Runtime.evaluate"]
            assert eval_reqs[0]["params"] == {
                "expression": "1 + 1",
                "awaitPromise": True,
                "returnByValue": True,
            }
            inject_expr = eval_reqs[1]["params"]["expression"]
            assert '"mdtopdf-autofit-tables"' in inject_expr
            assert "el.textContent = css" in inject_expr
            assert r"\u0e20" in inject_expr  # Thai encoded safely with ensure_ascii=True

    def test_print_to_pdf_prefer_css_page_size_vs_header_footer_inches(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _LoopbackCdpServer() as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            geom = compute_page_geometry("A4 portrait", "15mm 16mm")
            thin_margin_geom = compute_page_geometry("A4 portrait", "6mm 16mm")
            with CdpSession("fake") as session:
                pdf1 = session.print_to_pdf()
                pdf2 = session.print_to_pdf(
                    geometry=geom,
                    display_header_footer=True,
                    header_template="<div>H</div>",
                    footer_template="<div>F</div>",
                )
                # Empty templates default to <span></span>
                session.print_to_pdf(geometry=geom, display_header_footer=True)
                # Margins < 8mm gracefully disable displayHeaderFooter
                session.print_to_pdf(
                    geometry=thin_margin_geom,
                    display_header_footer=True,
                    header_template="<div>H</div>",
                )
                with pytest.raises(ValueError, match="geometry"):
                    session.print_to_pdf(display_header_footer=True)

            assert pdf1.startswith(b"%PDF-1.4")
            assert pdf2.startswith(b"%PDF-1.4")

            pdf_reqs = [r["params"] for r in server.requests if r["method"] == "Page.printToPDF"]
            assert pdf_reqs[0] == {
                "printBackground": True,
                "preferCSSPageSize": True,
                "displayHeaderFooter": False,
            }
            assert pdf_reqs[1]["preferCSSPageSize"] is False
            assert pdf_reqs[1]["displayHeaderFooter"] is True
            assert pdf_reqs[1]["paperWidth"] == pytest.approx(210.0 / 25.4)
            assert pdf_reqs[1]["paperHeight"] == pytest.approx(297.0 / 25.4)
            assert pdf_reqs[1]["marginTop"] == pytest.approx(15.0 / 25.4)
            assert pdf_reqs[1]["marginBottom"] == pytest.approx(15.0 / 25.4)
            assert pdf_reqs[1]["marginLeft"] == pytest.approx(16.0 / 25.4)
            assert pdf_reqs[1]["marginRight"] == pytest.approx(16.0 / 25.4)
            assert pdf_reqs[1]["headerTemplate"] == "<div>H</div>"
            assert pdf_reqs[1]["footerTemplate"] == "<div>F</div>"
            assert pdf_reqs[2]["headerTemplate"] == "<span></span>"
            assert pdf_reqs[2]["footerTemplate"] == "<span></span>"
            assert pdf_reqs[3]["displayHeaderFooter"] is False
            assert pdf_reqs[3]["preferCSSPageSize"] is True


class TestRenderBridgeAssetAndRealBrowserSmoke:
    """Static security checks for render_bridge.js and live headless browser E2E test."""

    def test_render_bridge_js_exists_and_security_hygiene(self) -> None:
        bridge_path = _ASSETS_DIR / "render_bridge.js"
        assert bridge_path.is_file()
        content = bridge_path.read_text(encoding="utf-8")
        assert content.strip()
        assert "__mdtopdfRenderAndInspect" in content
        assert "__mdtopdfInspectGeometry" in content
        assert "http://" not in content
        assert "https://" not in content
        assert "eval(" not in content
        assert "new Function(" not in content

        # Verify error-rendering helpers never use innerHTML
        math_err_match = re.search(
            r"function renderMathError\b.*?\n  \}", content, flags=re.DOTALL
        )
        mermaid_err_match = re.search(
            r"function renderMermaidError\b.*?\n  \}", content, flags=re.DOTALL
        )
        assert math_err_match is not None and "innerHTML" not in math_err_match.group(0)
        assert "textContent" in math_err_match.group(0)
        assert mermaid_err_match is not None and "innerHTML" not in mermaid_err_match.group(0)
        assert "textContent" in mermaid_err_match.group(0)

        node_exe = shutil.which("node")
        if node_exe is not None:
            res = subprocess.run(
                [node_exe, "--check", str(bridge_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert res.returncode == 0, res.stderr

    @pytest.mark.e2e
    def test_e2e_cdp_session_render_bridge_katex_mermaid_and_pdf(
        self,
        tmp_path: Path,
    ) -> None:
        try:
            browser_path = find_browser_executable()
        except FileNotFoundError as exc:
            pytest.skip(f"No Chromium/Edge/Chrome browser installed: {exc}")

        nonce = secrets.token_hex(16)
        katex_css_uri = html.escape((_ASSETS_DIR / "katex" / "katex.min.css").resolve().as_uri())
        katex_js_uri = html.escape((_ASSETS_DIR / "katex" / "katex.min.js").resolve().as_uri())
        mermaid_js_uri = html.escape((_ASSETS_DIR / "mermaid.min.js").resolve().as_uri())
        bridge_js_uri = html.escape((_ASSETS_DIR / "render_bridge.js").resolve().as_uri())

        bad_mermaid = "graph TD; A[<img src=x onerror=\"window.__xss=1\">] -->>> B"
        bad_math = r"\nonexistentcmd{<script>window.__xss=2</script>}"

        csp_policy = (
            "default-src 'none'; img-src file: data:; "
            "style-src 'unsafe-inline' file:; font-src file: data:; "
            f"script-src 'nonce-{nonce}';"
        )
        escaped_bad_mermaid = html.escape(bad_mermaid, quote=True)
        html_doc = f"""<!DOCTYPE html>
<html lang="th">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="{csp_policy}">
  <link rel="stylesheet" href="{katex_css_uri}">
  <style>
    @page {{ size: A4 portrait; margin: 15mm 16mm; }}
    body {{ margin: 0; font-family: sans-serif; }}
    .mermaid-container svg {{ max-width: 100%; height: auto; display: block; }}
  </style>
</head>
<body>
  <p>Inline math: <span class="math-inline" data-math-index="0">x^2 + y^2 = z^2</span></p>
  <div class="math-display" data-math-index="1">{html.escape(bad_math)}</div>
  <div class="mermaid-block" data-mermaid-index="0" data-mermaid-src="graph TD; A--&gt;B;"></div>
  <div class="mermaid-block" data-mermaid-index="1"
       data-mermaid-src="{escaped_bad_mermaid}"></div>
  <table id="wide-table" style="width: 1200px; border-collapse: collapse;">
    <tr><th>C1</th><th>C2</th><th>C3</th></tr>
    <tr><td>A</td><td>B</td><td>C</td></tr>
  </table>
  <pre style="width: 900px;">wide pre</pre>
  <script nonce="{nonce}" src="{katex_js_uri}"></script>
  <script nonce="{nonce}" src="{mermaid_js_uri}"></script>
  <script nonce="{nonce}" src="{bridge_js_uri}"></script>
</body>
</html>
"""
        html_path = tmp_path / "smoke.html"
        html_path.write_text(html_doc, encoding="utf-8")

        open_pdf: Any = pymupdf.open
        geom: PageGeometry = compute_page_geometry("A4 portrait", "15mm 16mm")
        with CdpSession(browser_path) as session:
            session.emulate_print_viewport(geom)
            session.navigate(html_path)
            report = session.evaluate(
                f"window.__mdtopdfRenderAndInspect({geom.printable_width_px:.3f})"
            )

            assert isinstance(report, dict)
            assert len(report["math_errors"]) == 1
            assert report["math_errors"][0]["index"] == 1
            assert report["math_errors"][0]["display_mode"] is True

            assert len(report["mermaid_errors"]) == 1
            assert report["mermaid_errors"][0]["index"] == 1

            # Confirm valid KaTeX and Mermaid rendered, XSS did not execute, and no orphan divs
            dom_checks = session.evaluate(
                """(() => ({
                    katexRendered: document.querySelectorAll('.katex').length,
                    mathErrorText: document.querySelector('.math-error')?.textContent || '',
                    mermaidSvg: document.querySelectorAll('.mermaid-container svg').length,
                    mermaidErrorBadge:
                      document.querySelector('.mermaid-error-badge')?.textContent || '',
                    orphanDivs: document.querySelectorAll('[id^="dmermaid-svg-"]').length,
                    xssFlag: window.__xss || 0,
                    tableIdxAttr:
                      document.querySelector('#wide-table')?.getAttribute('data-mdtopdf-table-idx'),
                }))()""",
                await_promise=False,
            )
            assert dom_checks["katexRendered"] >= 1
            assert "<script>" in dom_checks["mathErrorText"]
            assert dom_checks["mermaidSvg"] == 1
            assert dom_checks["mermaidErrorBadge"] == "Mermaid Render Error"
            assert dom_checks["orphanDivs"] == 0
            assert dom_checks["xssFlag"] == 0
            assert dom_checks["tableIdxAttr"] == "0"

            assert len(report["table_overflows"]) == 1
            assert report["table_overflows"][0]["table_index"] == 0
            assert any(item["tag"] == "pre" for item in report["wide_elements"])

            # Tier-2 proportional scale via transform: scale(...) should clear table_overflows
            session.inject_style(
                "mdtopdf-autofit-tables",
                "table[data-mdtopdf-table-idx='0'] "
                "{ transform: scale(0.5); transform-origin: top left; }",
            )
            recheck = session.evaluate(
                f"window.__mdtopdfInspectGeometry({geom.printable_width_px:.3f})",
                await_promise=False,
            )
            assert recheck["table_overflows"] == []

            pdf_no_hf = session.print_to_pdf()
            assert pdf_no_hf.startswith(b"%PDF-")
            with open_pdf(stream=pdf_no_hf, filetype="pdf") as doc1:
                assert doc1.page_count >= 1

            pdf_with_hf = session.print_to_pdf(
                geometry=geom,
                display_header_footer=True,
                header_template=(
                    "<div style='font-size:8px;width:100%;padding:0 12mm;'>Header</div>"
                ),
                footer_template=(
                    "<div style='font-size:8px;width:100%;padding:0 12mm;'>"
                    "<span class='pageNumber'></span>/<span class='totalPages'></span></div>"
                ),
            )
            assert pdf_with_hf.startswith(b"%PDF-")
            with open_pdf(stream=pdf_with_hf, filetype="pdf") as doc2:
                assert doc2.page_count >= 1


class TestSecurityAuditCdpContainmentAndCleanup:
    """Regression tests for CDP 127.0.0.1 binding, temp cleanup, and Fetch containment."""

    def test_is_fetch_url_allowed_policy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allowed_root = (tmp_path / "work").resolve()
        outside_root = (tmp_path / "secret").resolve()
        allowed_root.mkdir()
        outside_root.mkdir()

        ok_file = allowed_root / "ภาพ 1.png"
        ok_file.write_bytes(b"PNG")
        outside_file = outside_root / "secret.png"
        outside_file.write_bytes(b"PNG")

        roots = (allowed_root,)
        assert _is_fetch_url_allowed(ok_file.as_uri(), roots, False) is True
        assert _is_fetch_url_allowed("about:blank", roots, False) is True
        assert _is_fetch_url_allowed("data:image/png;base64,iVBORw0KGgo=", roots, False) is True

        assert _is_fetch_url_allowed(outside_file.as_uri(), roots, False) is False
        assert _is_fetch_url_allowed("https://evil.example/a.png", roots, False) is False
        assert _is_fetch_url_allowed("http://127.0.0.1:8080/a.png", roots, False) is False
        assert _is_fetch_url_allowed("file://evilhost/share/a.png", roots, False) is False
        assert _is_fetch_url_allowed("file://///evilhost/share/a.png", roots, False) is False
        assert _is_fetch_url_allowed("data:text/html,<script>1</script>", roots, False) is False

        # Windows drive URIs must remain blocked on POSIX even with allow_any_local_file=False
        monkeypatch.setattr("mdtopdf_skill.cdp_browser.sys.platform", "linux")
        assert _is_fetch_url_allowed("file:///C:/outside/secret.png", roots, False) is False
        assert _is_fetch_url_allowed("file:///Z|/outside/secret.png", roots, False) is False
        monkeypatch.undo()

        # allow_any_local_file=True permits local files outside roots, still blocking remote/UNC
        assert _is_fetch_url_allowed(outside_file.as_uri(), roots, True) is True
        assert _is_fetch_url_allowed("https://evil.example/a.png", roots, True) is False
        assert _is_fetch_url_allowed("file://evilhost/share/a.png", roots, True) is False

    def test_cdp_fetch_guard_intercepts_and_blocks_disallowed_urls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allowed_dir = tmp_path / "work"
        secret_dir = tmp_path / "secret"
        allowed_dir.mkdir()
        secret_dir.mkdir()
        ok_html = allowed_dir / "doc.html"
        ok_html.write_text("<html></html>", encoding="utf-8")
        secret_uri = (secret_dir / "secret.png").resolve().as_uri()
        fetch_actions: list[tuple[str, dict[str, Any]]] = []

        def _handler(conn: socket.socket, headers: dict[str, str]) -> None:
            _ = headers
            while True:
                try:
                    _, opcode, _, _, payload = _ws_read_client_frame(conn)
                except (EOFError, OSError, TimeoutError):
                    return
                if opcode != 0x1:
                    return
                msg = json.loads(payload.decode("utf-8"))
                method = msg["method"]
                cmd_id = msg["id"]
                if method == "Target.createTarget":
                    conn.sendall(
                        _ws_build_server_frame(
                            0x1, json.dumps({"id": cmd_id, "result": {"targetId": "t1"}}).encode()
                        )
                    )
                elif method == "Target.attachToTarget":
                    conn.sendall(
                        _ws_build_server_frame(
                            0x1,
                            json.dumps({"id": cmd_id, "result": {"sessionId": "s1"}}).encode(),
                        )
                    )
                elif method == "Page.navigate":
                    conn.sendall(
                        _ws_build_server_frame(
                            0x1, json.dumps({"id": cmd_id, "result": {"frameId": "f1"}}).encode()
                        )
                    )
                    conn.sendall(
                        _ws_build_server_frame(
                            0x1,
                            json.dumps(
                                {
                                    "method": "Fetch.requestPaused",
                                    "sessionId": "s1",
                                    "params": {
                                        "requestId": "req-ok",
                                        "request": {"url": ok_html.resolve().as_uri()},
                                    },
                                }
                            ).encode(),
                        )
                    )
                elif method == "Fetch.continueRequest":
                    fetch_actions.append((method, msg["params"]))
                    ok_reply = json.dumps({"id": cmd_id, "result": {}}).encode()
                    conn.sendall(_ws_build_server_frame(0x1, ok_reply))
                    conn.sendall(
                        _ws_build_server_frame(
                            0x1,
                            json.dumps(
                                {
                                    "method": "Fetch.requestPaused",
                                    "sessionId": "s1",
                                    "params": {
                                        "requestId": "req-secret",
                                        "request": {"url": secret_uri},
                                    },
                                }
                            ).encode(),
                        )
                    )
                elif method == "Fetch.failRequest":
                    fetch_actions.append((method, msg["params"]))
                    ok_reply = json.dumps({"id": cmd_id, "result": {}}).encode()
                    conn.sendall(_ws_build_server_frame(0x1, ok_reply))
                    conn.sendall(
                        _ws_build_server_frame(
                            0x1,
                            json.dumps(
                                {
                                    "method": "Page.loadEventFired",
                                    "sessionId": "s1",
                                    "params": {"timestamp": 1.0},
                                }
                            ).encode(),
                        )
                    )
                else:
                    if method == "Fetch.enable":
                        fetch_actions.append((method, msg["params"]))
                    ok_reply = json.dumps({"id": cmd_id, "result": {}}).encode()
                    conn.sendall(_ws_build_server_frame(0x1, ok_reply))

        with _LoopbackCdpServer(_handler) as server:
            proc = _FakeProc(
                [f"DevTools listening on ws://127.0.0.1:{server.port}{server.ws_path}\n".encode()]
            )
            _patch_fake_popen(monkeypatch, proc)
            with CdpSession("fake", allowed_file_roots=[allowed_dir]) as session:
                session.navigate(ok_html)
                assert session.blocked_requests == [secret_uri]

        assert fetch_actions == [
            ("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}),
            ("Fetch.continueRequest", {"requestId": "req-ok"}),
            ("Fetch.failRequest", {"requestId": "req-secret", "errorReason": "BlockedByClient"}),
        ]

    def test_ignores_non_loopback_devtools_url_and_cleans_up_on_keyboard_interrupt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Case 1: non-127.0.0.1 DevTools endpoint is ignored, times out, terminates, cleans dir
        proc_bad_host = _FakeProc(
            [b"DevTools listening on ws://0.0.0.0:9222/devtools/browser/bad\n"],
            stall_stderr=True,
        )
        calls: list[tuple[list[str], dict[str, Any]]] = []
        _patch_fake_popen(monkeypatch, proc_bad_host, calls)
        with pytest.raises(CdpTimeoutError), CdpSession("fake", startup_timeout=0.15):
            pass
        assert proc_bad_host.terminated
        user_flag = [a for a in calls[0][0] if a.startswith("--user-data-dir=")][0]
        assert not Path(user_flag.split("=", 1)[1]).exists()

        # Case 2: KeyboardInterrupt during _start kills/terminates proc and removes temp profile
        proc_kb = _FakeProc([], stall_stderr=True)
        calls_kb: list[tuple[list[str], dict[str, Any]]] = []
        _patch_fake_popen(monkeypatch, proc_kb, calls_kb)
        monkeypatch.setattr(
            CdpSession,
            "_wait_for_devtools_endpoint",
            lambda self, q, tail: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt), CdpSession("fake"):
            pass
        assert proc_kb.terminated
        user_flag_kb = [a for a in calls_kb[0][0] if a.startswith("--user-data-dir=")][0]
        assert not Path(user_flag_kb.split("=", 1)[1]).exists()

    @pytest.mark.e2e
    def test_e2e_fetch_guard_and_mermaid_svg_sanitizer_block_outside_files_and_xss(
        self, tmp_path: Path
    ) -> None:
        try:
            browser_path = find_browser_executable()
        except FileNotFoundError as exc:
            pytest.skip(f"No Chromium/Edge/Chrome browser installed: {exc}")

        work_dir = tmp_path / "work"
        secret_dir = tmp_path / "secret"
        work_dir.mkdir()
        secret_dir.mkdir()

        ok_img = work_dir / "ok.png"
        secret_img = secret_dir / "secret.png"
        PIL.Image.new("RGB", (40, 40), (0, 160, 0)).save(ok_img, format="PNG")
        PIL.Image.new("RGB", (40, 40), (220, 0, 0)).save(secret_img, format="PNG")

        ok_uri = html.escape(ok_img.resolve().as_uri(), quote=True)
        secret_uri = html.escape(secret_img.resolve().as_uri(), quote=True)
        mermaid_js_uri = html.escape(
            (_ASSETS_DIR / "mermaid.min.js").resolve().as_uri(), quote=True
        )
        bridge_js_uri = html.escape(
            (_ASSETS_DIR / "render_bridge.js").resolve().as_uri(), quote=True
        )

        nonce = secrets.token_hex(16)
        csp_policy = (
            "default-src 'none'; img-src file: data:; "
            "style-src 'unsafe-inline' file:; font-src file: data:; "
            f"script-src 'nonce-{nonce}';"
        )
        mermaid_diagram = (
            "graph TD\n"
            '  A["<img src=\'../secret/secret.png\' width=\'40\' height=\'40\'/>"] --> '
            'B["<div id=\'mbg\' style=\'width:40px;height:40px;'
            'background-image:url(../secret/secret.png)\'>hi</div>"]\n'
            '  click A "javascript:window.__xss=1"\n'
        )
        html_doc = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="{csp_policy}">
</head>
<body>
  <img id="ok-img" src="{ok_uri}">
  <img id="secret-img" src="{secret_uri}">
  <div id="imgset-bg"
       style="width:40px;height:40px;background-image:image-set('{secret_uri}' 1x)"></div>
  <div class="mermaid-block" data-mermaid-index="0"
       data-mermaid-src="{html.escape(mermaid_diagram, quote=True)}"></div>
  <script nonce="{nonce}" src="{mermaid_js_uri}"></script>
  <script nonce="{nonce}" src="{bridge_js_uri}"></script>
</body>
</html>
"""
        html_file = work_dir / "security_probe.html"
        html_file.write_text(html_doc, encoding="utf-8")

        geom = compute_page_geometry("A4 portrait", "15mm 16mm")
        with CdpSession(
            browser_path,
            allowed_file_roots=[work_dir, _ASSETS_DIR],
            allow_any_local_file=False,
        ) as session:
            session.emulate_print_viewport(geom)
            session.navigate(html_file)
            report = session.evaluate(
                f"window.__mdtopdfRenderAndInspect({geom.printable_width_px:.3f})"
            )
            assert report["mermaid_errors"] == []

            probe = session.evaluate(
                """(() => {
                    const okImg = document.getElementById('ok-img');
                    const secretImg = document.getElementById('secret-img');
                    const mermaidImgs = Array.from(
                        document.querySelectorAll('.mermaid-container img')
                    ).map(i => i.getAttribute('src'));
                    const mermaidLinks = Array.from(
                        document.querySelectorAll('.mermaid-container a')
                    ).map(a => a.getAttribute('href') || a.getAttribute('xlink:href'));
                    const mbg = document.getElementById('mbg');
                    return {
                        okWidth: okImg ? okImg.naturalWidth : 0,
                        secretWidth: secretImg ? secretImg.naturalWidth : -1,
                        mermaidImgs,
                        mermaidLinks,
                        mbgBg: mbg ? getComputedStyle(mbg).backgroundImage : null,
                        xss: window.__xss || 0,
                    };
                })()""",
                await_promise=False,
            )

        assert probe["okWidth"] == 40
        assert probe["secretWidth"] == 0
        assert all(src is None for src in probe["mermaidImgs"])
        assert all(
            href is None or not href.lower().startswith("javascript:")
            for href in probe["mermaidLinks"]
        )
        assert probe["mbgBg"] in (None, "none")
        assert probe["xss"] == 0
        assert secret_img.resolve().as_uri() in session.blocked_requests
