"""Headless Chromium/Edge CDP browser discovery, lifecycle, and stdlib WebSocket driver."""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import math
import os
import queue
import re
import shutil
import socket
import struct
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath
from types import TracebackType
from typing import Any
from urllib.parse import unquote, urlparse

from mdtopdf_skill.models import PageGeometry

DEFAULT_CDP_TIMEOUT_SECONDS: float = 120.0
DEFAULT_STARTUP_TIMEOUT_SECONDS: float = 15.0
DEFAULT_NAVIGATE_TIMEOUT_SECONDS: float = 30.0

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_DEVTOOLS_URL_RE = re.compile(r"DevTools listening on (ws://127\.0\.0\.1:(\d+)(/[^\s]+))")
_URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_WIN_SLASH_DRIVE_RE = re.compile(r"^/([A-Za-z])[:|]/(.*)$")
_WIN_DRIVE_REL_RE = re.compile(r"^/?[A-Za-z]:(?![\\/])")
_SAFE_FETCH_DATA_RE = re.compile(
    r"^data:(?:image/(?:png|jpe?g|gif|webp|bmp|x-icon|vnd\.microsoft\.icon|svg\+xml|tiff|avif)"
    r"|font/(?:woff2?|ttf|otf)|application/font-woff2?)"
    r"(?:;[a-z0-9.+_-]+(?:=[^;,]*)?)*,",
    flags=re.IGNORECASE,
)
_MM_PER_INCH = 25.4
_MAX_HTTP_HEADER_BYTES = 65536
_MAX_WS_PAYLOAD_BYTES = 128 * 1024 * 1024
_STDERR_TAIL_LINES = 50


def _is_fetch_url_allowed(
    url: str,
    allowed_file_roots: tuple[Path, ...],
    allow_any_local_file: bool,
) -> bool:
    """Return True if a browser request URL is permitted by offline/containment policy."""
    if not url or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return False

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme == "about":
        return True
    if scheme == "data":
        return _SAFE_FETCH_DATA_RE.match(url) is not None
    if scheme != "file":
        return False

    if parsed.netloc.lower() not in {"", "localhost"}:
        return False
    decoded = unquote(parsed.path)
    if (
        not decoded
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded)
        or decoded.startswith(("//", "\\\\", "/\\\\"))
        or PureWindowsPath(decoded).drive.startswith("\\\\")
    ):
        return False
    win_drive = _WIN_SLASH_DRIVE_RE.match(decoded)
    if win_drive is not None:
        decoded = f"{win_drive.group(1)}:/{win_drive.group(2)}"
    if _WIN_DRIVE_REL_RE.match(decoded):
        return False

    try:
        candidate = Path(decoded).resolve(strict=False)
    except (OSError, ValueError):
        return False

    if allow_any_local_file:
        return True
    return any(candidate.is_relative_to(root) for root in allowed_file_roots)


class CdpError(RuntimeError):
    """Base exception for headless browser launch, WebSocket, and CDP failures."""


class CdpTimeoutError(CdpError, TimeoutError):
    """Raised when browser startup, navigation, or a CDP command exceeds its timeout."""


class CdpProtocolError(CdpError):
    """Raised on WebSocket handshake/framing errors, CDP RPC errors, or JS exceptions."""


def _validate_positive_finite_float(value: float | int | str, name: str) -> float:
    """Validate that value parses as a finite float strictly greater than zero."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive finite number, got bool")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a valid positive number, got {value!r}") from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")
    return parsed


def resolve_cdp_timeout(timeout: float | None = None) -> float:
    """Resolve and validate the CDP timeout in seconds (explicit > MDTOPDF_TIMEOUT > 120s)."""
    if timeout is not None:
        return _validate_positive_finite_float(timeout, "timeout")
    env_raw = os.environ.get("MDTOPDF_TIMEOUT", "").strip()
    if not env_raw:
        return DEFAULT_CDP_TIMEOUT_SECONDS
    return _validate_positive_finite_float(env_raw, "MDTOPDF_TIMEOUT")


def _candidate_browser_paths() -> list[Path]:
    """Return platform-specific standard Edge/Chrome/Chromium executable paths."""
    if sys.platform == "win32":
        raw_roots = [
            os.environ.get("PROGRAMFILES(X86)", "").strip(),
            os.environ.get("PROGRAMFILES", "").strip(),
            os.environ.get("LOCALAPPDATA", "").strip(),
            r"C:\Program Files (x86)",
            r"C:\Program Files",
        ]
        roots: list[str] = []
        seen_roots: set[str] = set()
        for root in raw_roots:
            if root:
                norm = root.lower()
                if norm not in seen_roots:
                    seen_roots.add(norm)
                    roots.append(root)

        relative_targets = (
            ("Microsoft", "Edge", "Application", "msedge.exe"),
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("Chromium", "Application", "chrome.exe"),
        )
        candidates: list[Path] = []
        for parts in relative_targets:
            for root in roots:
                candidates.append(Path(root, *parts))
        return candidates

    posix_paths = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/usr/bin/microsoft-edge",
        "/usr/bin/microsoft-edge-stable",
    ]
    if sys.platform == "darwin":
        posix_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            *posix_paths,
        ]
    return [Path(path) for path in posix_paths]


def _which_browser_names() -> tuple[str, ...]:
    """Return ordered executable names to probe via shutil.which."""
    if sys.platform == "win32":
        return ("msedge", "chrome", "chromium", "google-chrome")
    return (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "microsoft-edge-stable",
        "chrome",
        "msedge",
    )


def find_browser_executable() -> str:
    """Discover a local Edge/Chrome/Chromium executable.

    Checks ``MDTOPDF_BROWSER`` first, then platform standard paths, then ``PATH``.
    Raises ``FileNotFoundError`` mentioning ``MDTOPDF_BROWSER`` if none is found.
    """
    env_browser = os.environ.get("MDTOPDF_BROWSER", "").strip()
    if env_browser:
        env_path = Path(env_browser)
        if env_path.is_file():
            return str(env_path)
        which_override = shutil.which(env_browser)
        if which_override is not None:
            return which_override
        raise FileNotFoundError(
            f"Browser specified by MDTOPDF_BROWSER={env_browser!r} was not found or is not a file."
        )

    for candidate in _candidate_browser_paths():
        if candidate.is_file():
            return str(candidate)

    for name in _which_browser_names():
        found = shutil.which(name)
        if found is not None:
            return found

    raise FileNotFoundError(
        "No supported Chromium-based browser (Edge, Chrome, or Chromium) was found. "
        "Install Edge/Chrome/Chromium or set MDTOPDF_BROWSER to the browser executable path."
    )


def _normalize_navigation_url(url_or_path: str | Path) -> str:
    """Convert a local filesystem Path/string to a file:// URI while preserving URIs."""
    if isinstance(url_or_path, Path):
        return url_or_path.resolve().as_uri()
    raw = str(url_or_path).strip()
    if not raw:
        raise ValueError("Navigation target url_or_path must not be empty")
    if _URI_SCHEME_RE.match(raw) or raw.startswith("about:") or raw.startswith("data:"):
        return raw
    return Path(raw).resolve().as_uri()


def _unmask_or_mask_bytes(data: bytes, mask_key: bytes) -> bytes:
    """Apply 4-byte RFC6455 XOR masking key."""
    return bytes(byte ^ mask_key[idx & 3] for idx, byte in enumerate(data))


class CdpSession:
    """Context manager driving a single isolated headless browser session via localhost CDP."""

    browser_path: str
    browser_version: str | None

    def __init__(
        self,
        browser_path: str | Path | None = None,
        *,
        timeout: float | None = None,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        allowed_file_roots: Sequence[str | Path] | None = None,
        allow_any_local_file: bool = False,
    ) -> None:
        self.browser_path = (
            str(browser_path) if browser_path is not None else find_browser_executable()
        )
        self.browser_version = None
        self._timeout = resolve_cdp_timeout(timeout)
        self._startup_timeout = _validate_positive_finite_float(startup_timeout, "startup_timeout")
        self._allowed_file_roots: tuple[Path, ...] = tuple(
            Path(root).resolve(strict=False) for root in (allowed_file_roots or ())
        )
        self._allow_any_local_file = bool(allow_any_local_file)
        self._fetch_guard_enabled = (
            allowed_file_roots is not None or self._allow_any_local_file
        )
        self._ignored_cmd_ids: set[int] = set()
        self.blocked_requests: list[str] = []
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._rx_buffer = bytearray()
        self._events: deque[dict[str, Any]] = deque()
        self._cmd_id = 0
        self._session_id: str | None = None
        self._target_id: str | None = None
        self._stderr_thread: threading.Thread | None = None
        self._started = False
        self._closed = False

    def __enter__(self) -> CdpSession:
        if self._started or self._closed:
            raise CdpError("CdpSession cannot be entered more than once")
        try:
            self._start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def _build_launch_command(self, profile_dir: str) -> list[str]:
        """Build headless Chromium/Edge launch arguments."""
        cmd = [
            self.browser_path,
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
            f"--user-data-dir={profile_dir}",
            f"--crash-dumps-dir={profile_dir}",
        ]
        # The Chromium sandbox stays ON unless the user explicitly opts out
        # (needed e.g. when running as root in containers). Never default to
        # disabling it: Markdown input may be untrusted.
        if os.environ.get("MDTOPDF_NO_SANDBOX", "").strip().lower() in {"1", "true", "yes"}:
            cmd.append("--no-sandbox")
        cmd.append("about:blank")
        return cmd

    def _format_error(self, message: str) -> str:
        """Annotate an error message with browser version when known."""
        if self.browser_version:
            return f"{message} (browser={self.browser_version})"
        return message

    def _start(self) -> None:
        """Launch the browser, establish RFC6455 WebSocket, and attach to a blank target."""
        self._temp_dir = tempfile.TemporaryDirectory(
            prefix="mdtopdf_cdp_",
            ignore_cleanup_errors=True,
        )
        cmd = self._build_launch_command(self._temp_dir.name)
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self._proc = subprocess.Popen(cmd, **popen_kwargs)  # nosec B603
        except OSError as exc:
            raise CdpProtocolError(
                f"Failed to launch browser {self.browser_path!r}: {exc}"
            ) from exc

        line_queue: queue.Queue[str | None] = queue.Queue()
        stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        stderr_pipe = self._proc.stderr

        def _drain_stderr() -> None:
            if stderr_pipe is None:
                line_queue.put(None)
                return
            try:
                for raw_line in iter(stderr_pipe.readline, b""):
                    text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    stderr_tail.append(text)
                    line_queue.put(text)
            finally:
                with contextlib.suppress(OSError):
                    stderr_pipe.close()
                line_queue.put(None)

        self._stderr_thread = threading.Thread(
            target=_drain_stderr,
            name="mdtopdf-cdp-stderr",
            daemon=True,
        )
        self._stderr_thread.start()

        port, ws_path = self._wait_for_devtools_endpoint(line_queue, stderr_tail)
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=self._startup_timeout)
        except TimeoutError as exc:
            raise CdpTimeoutError(
                f"Timed out connecting to browser CDP socket 127.0.0.1:{port}"
            ) from exc
        except OSError as exc:
            raise CdpProtocolError(
                f"Failed to connect to browser CDP socket 127.0.0.1:{port}: {exc}"
            ) from exc

        self._sock = sock
        self._ws_handshake(port, ws_path)

        with contextlib.suppress(CdpError):
            version_info = self._send_cdp("Browser.getVersion")
            product = version_info.get("product")
            if isinstance(product, str) and product.strip():
                self.browser_version = product.strip()

        target_res = self._send_cdp("Target.createTarget", {"url": "about:blank"})
        target_id = target_res.get("targetId")
        if not isinstance(target_id, str) or not target_id:
            raise CdpProtocolError(
                self._format_error(f"Target.createTarget returned invalid targetId: {target_res!r}")
            )
        self._target_id = target_id

        attach_res = self._send_cdp(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        session_id = attach_res.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise CdpProtocolError(
                self._format_error(
                    f"Target.attachToTarget returned invalid sessionId: {attach_res!r}"
                )
            )
        self._session_id = session_id

        self._send_cdp("Page.enable", session_id=session_id)
        self._send_cdp("Runtime.enable", session_id=session_id)
        if self._fetch_guard_enabled:
            self._send_cdp(
                "Fetch.enable",
                {"patterns": [{"urlPattern": "*"}]},
                session_id=session_id,
            )
        self._started = True

    def _wait_for_devtools_endpoint(
        self,
        line_queue: queue.Queue[str | None],
        stderr_tail: deque[str],
    ) -> tuple[int, str]:
        """Read stderr queue until DevTools URL is observed or timeout/EOF occurs."""
        deadline = time.monotonic() + self._startup_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                tail_text = "\n".join(stderr_tail)
                raise CdpTimeoutError(
                    f"Timed out after {self._startup_timeout:.1f}s waiting for DevTools "
                    f"WebSocket URL from {self.browser_path!r}. stderr: {tail_text}"
                )
            try:
                line = line_queue.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    # Drain briefly in case the reader thread has not pushed EOF/last line yet.
                    try:
                        line = line_queue.get(timeout=0.05)
                    except queue.Empty:
                        tail_text = "\n".join(stderr_tail)
                        raise CdpProtocolError(
                            f"Browser exited prematurely (code={self._proc.returncode}) before "
                            f"reporting DevTools URL. stderr: {tail_text}"
                        ) from None
                else:
                    continue

            if line is None:
                exit_code = self._proc.poll() if self._proc is not None else None
                tail_text = "\n".join(stderr_tail)
                raise CdpProtocolError(
                    f"Browser stderr closed before reporting DevTools URL "
                    f"(exit_code={exit_code}). stderr: {tail_text}"
                )

            match = _DEVTOOLS_URL_RE.search(line)
            if match is not None:
                port = int(match.group(2))
                ws_path = match.group(3)
                if not (1 <= port <= 65535):
                    raise CdpProtocolError(f"Invalid DevTools port parsed from stderr: {port}")
                return port, ws_path

    def _ws_handshake(self, port: int, ws_path: str) -> None:
        """Perform RFC6455 HTTP/1.1 Upgrade handshake and verify Sec-WebSocket-Accept."""
        sock = self._sock
        if sock is None:
            raise CdpProtocolError("WebSocket is not connected")

        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {ws_path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")

        deadline = time.monotonic() + self._startup_timeout
        try:
            sock.settimeout(self._startup_timeout)
            sock.sendall(request)
        except TimeoutError as exc:
            raise CdpTimeoutError("Timed out sending WebSocket Upgrade request") from exc
        except OSError as exc:
            raise CdpProtocolError(f"Failed sending WebSocket Upgrade request: {exc}") from exc

        header_buf = bytearray()
        while True:
            marker = header_buf.find(b"\r\n\r\n")
            if marker != -1:
                raw_headers = bytes(header_buf[:marker])
                remainder = bytes(header_buf[marker + 4 :])
                if remainder:
                    self._rx_buffer.extend(remainder)
                break
            if len(header_buf) >= _MAX_HTTP_HEADER_BYTES:
                raise CdpProtocolError("WebSocket HTTP handshake response headers exceeded limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise CdpTimeoutError("Timed out waiting for WebSocket Upgrade response")
            try:
                sock.settimeout(max(0.01, remaining))
                chunk = sock.recv(4096)
            except TimeoutError as exc:
                raise CdpTimeoutError("Timed out reading WebSocket Upgrade response") from exc
            except OSError as exc:
                raise CdpProtocolError(f"Socket error during WebSocket handshake: {exc}") from exc
            if not chunk:
                raise CdpProtocolError("Peer closed socket during WebSocket handshake")
            header_buf.extend(chunk)

        header_text = raw_headers.decode("latin-1")
        lines = header_text.split("\r\n")
        if not lines:
            raise CdpProtocolError("Empty WebSocket handshake response")

        status_parts = lines[0].split(" ", 2)
        if len(status_parts) < 2 or status_parts[1] != "101":
            raise CdpProtocolError(f"WebSocket upgrade rejected: {lines[0]!r}")

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                name, val = line.split(":", 1)
                headers[name.strip().lower()] = val.strip()

        upgrade_hdr = headers.get("upgrade", "")
        if upgrade_hdr.lower() != "websocket":
            raise CdpProtocolError(
                f"Missing or invalid Upgrade header in WebSocket handshake: {upgrade_hdr!r}"
            )
        conn_tokens = [tok.strip().lower() for tok in headers.get("connection", "").split(",")]
        if "upgrade" not in conn_tokens:
            raise CdpProtocolError(
                f"Missing Upgrade token in Connection header: {headers.get('connection')!r}"
            )

        expected_accept = base64.b64encode(
            hashlib.sha1(
                (ws_key + _WS_GUID).encode("ascii"),
                usedforsecurity=False,
            ).digest()
        ).decode("ascii")
        actual_accept = headers.get("sec-websocket-accept", "")
        if actual_accept != expected_accept:
            raise CdpProtocolError(
                f"Invalid Sec-WebSocket-Accept header: expected {expected_accept!r}, "
                f"got {actual_accept!r}"
            )

    def _read_exact(self, num_bytes: int, deadline: float) -> bytes:
        """Read exactly num_bytes from buffered socket before deadline."""
        sock = self._sock
        if sock is None:
            raise CdpProtocolError("WebSocket is not connected")

        while len(self._rx_buffer) < num_bytes:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise CdpTimeoutError("Timed out reading from CDP WebSocket")
            try:
                sock.settimeout(max(0.01, remaining))
                chunk = sock.recv(max(4096, num_bytes - len(self._rx_buffer)))
            except TimeoutError as exc:
                raise CdpTimeoutError("Timed out reading from CDP WebSocket") from exc
            except OSError as exc:
                raise CdpProtocolError(f"WebSocket read failed: {exc}") from exc
            if not chunk:
                raise CdpProtocolError("CDP WebSocket closed unexpectedly by peer")
            self._rx_buffer.extend(chunk)

        out = bytes(self._rx_buffer[:num_bytes])
        del self._rx_buffer[:num_bytes]
        return out

    def _ws_send_frame(self, opcode: int, payload: bytes, timeout: float | None = None) -> None:
        """Send a masked RFC6455 client frame."""
        sock = self._sock
        if sock is None:
            raise CdpProtocolError("WebSocket is not connected")

        first_byte = 0x80 | (opcode & 0x0F)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", first_byte, 0x80 | length)
        elif length <= 0xFFFF:
            header = struct.pack("!BBH", first_byte, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", first_byte, 0x80 | 127, length)

        mask_key = os.urandom(4)
        masked_payload = _unmask_or_mask_bytes(payload, mask_key)
        frame = header + mask_key + masked_payload

        send_timeout = self._timeout if timeout is None else max(0.01, timeout)
        try:
            sock.settimeout(send_timeout)
            sock.sendall(frame)
        except TimeoutError as exc:
            raise CdpTimeoutError("Timed out writing to CDP WebSocket") from exc
        except OSError as exc:
            raise CdpProtocolError(f"WebSocket write failed: {exc}") from exc

    def _ws_recv_message(self, deadline: float) -> str:
        """Receive one complete RFC6455 text message, handling fragmentation and control frames."""
        fragments: list[bytes] = []
        msg_opcode: int | None = None

        while True:
            b1, b2 = self._read_exact(2, deadline)
            fin = bool(b1 & 0x80)
            rsv = b1 & 0x70
            if rsv != 0:
                raise CdpProtocolError(f"Unsupported WebSocket RSV bits: {rsv:#x}")
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F

            if length == 126:
                (length,) = struct.unpack("!H", self._read_exact(2, deadline))
            elif length == 127:
                (length,) = struct.unpack("!Q", self._read_exact(8, deadline))

            if length > _MAX_WS_PAYLOAD_BYTES:
                raise CdpProtocolError(f"WebSocket payload length exceeds limit: {length}")

            mask_key = self._read_exact(4, deadline) if masked else b""
            payload = self._read_exact(length, deadline) if length > 0 else b""
            if masked:
                payload = _unmask_or_mask_bytes(payload, mask_key)

            if opcode >= 0x8:
                if not fin or length > 125:
                    raise CdpProtocolError(
                        "Invalid fragmented or oversized WebSocket control frame"
                    )
                if opcode == 0x8:
                    close_code: int | None = None
                    close_reason = ""
                    if len(payload) >= 2:
                        (close_code,) = struct.unpack("!H", payload[:2])
                        close_reason = payload[2:].decode("utf-8", errors="replace")
                    with contextlib.suppress(CdpError, OSError):
                        self._ws_send_frame(0x8, payload[:2], timeout=1.0)
                    raise CdpProtocolError(
                        self._format_error(
                            f"WebSocket closed by peer (code={close_code}, reason={close_reason!r})"
                        )
                    )
                if opcode == 0x9:
                    pong_timeout = max(0.01, deadline - time.monotonic())
                    self._ws_send_frame(0xA, payload, timeout=pong_timeout)
                    continue
                if opcode == 0xA:
                    continue
                raise CdpProtocolError(f"Unsupported WebSocket control opcode: {opcode:#x}")

            if opcode == 0x0:
                if msg_opcode is None:
                    raise CdpProtocolError("Unexpected WebSocket continuation frame")
                fragments.append(payload)
            elif opcode in (0x1, 0x2):
                if msg_opcode is not None:
                    raise CdpProtocolError("Interleaved WebSocket data frame before message end")
                msg_opcode = opcode
                fragments.append(payload)
            else:
                raise CdpProtocolError(f"Unsupported WebSocket data opcode: {opcode:#x}")

            if fin:
                if msg_opcode != 0x1:
                    raise CdpProtocolError(
                        f"Expected WebSocket text frame, got opcode {msg_opcode:#x}"
                    )
                full_payload = b"".join(fragments)
                try:
                    return full_payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise CdpProtocolError(f"Invalid UTF-8 in WebSocket text frame: {exc}") from exc

    def _handle_fetch_request_paused(self, msg: dict[str, Any], deadline: float) -> None:
        """Allow or fail a paused CDP Fetch request according to local-file containment."""
        params = msg.get("params")
        if not isinstance(params, dict):
            return
        request_id = params.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            return
        req_obj = params.get("request")
        url = req_obj.get("url", "") if isinstance(req_obj, dict) else ""
        if not isinstance(url, str):
            url = ""
        sid = msg.get("sessionId")
        effective_sid = sid if isinstance(sid, str) and sid else self._session_id

        allowed = _is_fetch_url_allowed(
            url,
            self._allowed_file_roots,
            self._allow_any_local_file,
        )
        self._cmd_id += 1
        cmd_id = self._cmd_id
        self._ignored_cmd_ids.add(cmd_id)

        if allowed:
            reply_payload: dict[str, Any] = {
                "id": cmd_id,
                "method": "Fetch.continueRequest",
                "params": {"requestId": request_id},
            }
        else:
            self.blocked_requests.append(url)
            reply_payload = {
                "id": cmd_id,
                "method": "Fetch.failRequest",
                "params": {"requestId": request_id, "errorReason": "BlockedByClient"},
            }
        if effective_sid is not None:
            reply_payload["sessionId"] = effective_sid

        encoded = json.dumps(reply_payload, separators=(",", ":")).encode("utf-8")
        remaining = max(0.05, deadline - time.monotonic())
        self._ws_send_frame(0x1, encoded, timeout=remaining)

    def _send_cdp(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a CDP JSON-RPC command and wait for its matching response."""
        if self._closed or self._sock is None:
            raise CdpProtocolError("CDP session is closed")

        self._cmd_id += 1
        cmd_id = self._cmd_id
        payload: dict[str, Any] = {"id": cmd_id, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id is not None:
            payload["sessionId"] = session_id

        effective_timeout = (
            _validate_positive_finite_float(timeout, "timeout")
            if timeout is not None
            else self._timeout
        )
        deadline = time.monotonic() + effective_timeout
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._ws_send_frame(0x1, encoded, timeout=effective_timeout)

        while True:
            raw_text = self._ws_recv_message(deadline)
            try:
                msg = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise CdpProtocolError(f"Invalid JSON received from CDP: {exc}") from exc
            if not isinstance(msg, dict):
                raise CdpProtocolError(f"Expected JSON object from CDP, got {type(msg).__name__}")

            msg_id = msg.get("id")
            if isinstance(msg_id, int) and msg_id in self._ignored_cmd_ids:
                self._ignored_cmd_ids.discard(msg_id)
                continue

            if self._fetch_guard_enabled and msg.get("method") == "Fetch.requestPaused":
                self._handle_fetch_request_paused(msg, deadline)
                continue

            if msg_id == cmd_id:
                err = msg.get("error")
                if err is not None:
                    if isinstance(err, dict):
                        err_msg = str(err.get("message", err))
                        err_code = err.get("code")
                        raise CdpProtocolError(
                            self._format_error(
                                f"CDP error for {method} (code={err_code}): {err_msg}"
                            )
                        )
                    raise CdpProtocolError(
                        self._format_error(f"CDP error for {method}: {err!r}")
                    )
                result = msg.get("result", {})
                if not isinstance(result, dict):
                    raise CdpProtocolError(
                        self._format_error(
                            f"Expected object result for {method}, got {type(result).__name__}"
                        )
                    )
                return result

            if "method" in msg:
                self._events.append(msg)

    def _require_session_id(self) -> str:
        """Return active attached page sessionId or raise CdpProtocolError."""
        if self._closed or not self._started or self._session_id is None:
            raise CdpProtocolError("CDP page session is not active")
        return self._session_id

    def _pop_matching_event(self, method: str, session_id: str) -> dict[str, Any] | None:
        """Pop the first buffered event matching method and sessionId."""
        for idx, event in enumerate(self._events):
            if event.get("method") == method and event.get("sessionId") == session_id:
                del self._events[idx]
                return event
        return None

    def _clear_events(self, method: str, session_id: str) -> None:
        """Discard stale buffered events for method and sessionId before a new trigger."""
        self._events = deque(
            ev
            for ev in self._events
            if not (ev.get("method") == method and ev.get("sessionId") == session_id)
        )

    def _wait_for_event(
        self,
        method: str,
        session_id: str,
        timeout: float,
    ) -> dict[str, Any]:
        """Wait for a CDP event matching method and sessionId up to timeout seconds."""
        existing = self._pop_matching_event(method, session_id)
        if existing is not None:
            return existing

        deadline = time.monotonic() + timeout
        while True:
            raw_text = self._ws_recv_message(deadline)
            try:
                msg = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise CdpProtocolError(f"Invalid JSON event received from CDP: {exc}") from exc
            if not isinstance(msg, dict):
                raise CdpProtocolError(
                    f"Expected JSON object event from CDP, got {type(msg).__name__}"
                )
            msg_id = msg.get("id")
            if isinstance(msg_id, int) and msg_id in self._ignored_cmd_ids:
                self._ignored_cmd_ids.discard(msg_id)
                continue
            if self._fetch_guard_enabled and msg.get("method") == "Fetch.requestPaused":
                self._handle_fetch_request_paused(msg, deadline)
                continue
            if "method" in msg:
                if msg.get("method") == method and msg.get("sessionId") == session_id:
                    return msg
                self._events.append(msg)

    def emulate_print_viewport(self, geometry: PageGeometry) -> None:
        """Configure print media emulation and exact printable viewport metrics."""
        session_id = self._require_session_id()
        width_px = max(1, round(geometry.printable_width_px))
        height_px = max(1, round(geometry.printable_height_px))

        self._send_cdp(
            "Emulation.setEmulatedMedia",
            {"media": "print"},
            session_id=session_id,
        )
        self._send_cdp(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width_px,
                "height": height_px,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
            session_id=session_id,
        )

    def navigate(self, url_or_path: str | Path, timeout: float | None = None) -> None:
        """Navigate to a URL or local file path and await Page.loadEventFired."""
        session_id = self._require_session_id()
        target_url = _normalize_navigation_url(url_or_path)
        if timeout is not None:
            effective_timeout = _validate_positive_finite_float(timeout, "timeout")
        elif self._timeout != DEFAULT_CDP_TIMEOUT_SECONDS:
            effective_timeout = self._timeout
        else:
            effective_timeout = DEFAULT_NAVIGATE_TIMEOUT_SECONDS

        self._clear_events("Page.loadEventFired", session_id)
        deadline = time.monotonic() + effective_timeout
        nav_res = self._send_cdp(
            "Page.navigate",
            {"url": target_url},
            session_id=session_id,
            timeout=effective_timeout,
        )
        error_text = nav_res.get("errorText")
        if isinstance(error_text, str) and error_text:
            raise CdpProtocolError(
                self._format_error(f"Navigation to {target_url!r} failed: {error_text}")
            )

        remaining = deadline - time.monotonic()
        if self._pop_matching_event("Page.loadEventFired", session_id) is not None:
            return
        if remaining <= 0.0:
            raise CdpTimeoutError(
                self._format_error(f"Timed out waiting for Page.loadEventFired on {target_url!r}")
            )
        self._wait_for_event("Page.loadEventFired", session_id, remaining)

    def evaluate(
        self,
        expression: str,
        await_promise: bool = True,
        return_by_value: bool = True,
    ) -> Any:
        """Evaluate a JS expression in the active page and return its value."""
        session_id = self._require_session_id()
        eval_res = self._send_cdp(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": return_by_value,
            },
            session_id=session_id,
        )
        exception_details = eval_res.get("exceptionDetails")
        if exception_details is not None:
            detail_msg = self._extract_exception_message(exception_details)
            raise CdpProtocolError(self._format_error(f"JavaScript exception: {detail_msg}"))

        if not return_by_value:
            return eval_res.get("result")

        remote_obj = eval_res.get("result")
        if isinstance(remote_obj, dict):
            return remote_obj.get("value")
        return None

    @staticmethod
    def _extract_exception_message(exception_details: object) -> str:
        """Extract a human-readable error message from CDP Runtime.exceptionDetails."""
        if not isinstance(exception_details, dict):
            return str(exception_details)
        exception_obj = exception_details.get("exception")
        if isinstance(exception_obj, dict):
            desc = exception_obj.get("description")
            if isinstance(desc, str) and desc.strip():
                return desc.strip()
        text = exception_details.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        return repr(exception_details)

    def inject_style(self, style_id: str, css_text: str) -> None:
        """Create or update a <style id=...> element via textContent without HTML parsing."""
        if not style_id:
            raise ValueError("style_id must not be empty")
        id_json = json.dumps(style_id, ensure_ascii=True)
        css_json = json.dumps(css_text, ensure_ascii=True)
        expression = (
            "(() => {"
            f" const id = {id_json};"
            f" const css = {css_json};"
            " let el = document.getElementById(id);"
            " if (!el) {"
            "   el = document.createElement('style');"
            "   el.id = id;"
            "   (document.head || document.documentElement).appendChild(el);"
            " }"
            " el.textContent = css;"
            "})()"
        )
        self.evaluate(expression, await_promise=False, return_by_value=True)

    def print_to_pdf(
        self,
        *,
        geometry: PageGeometry | None = None,
        display_header_footer: bool = False,
        header_template: str = "",
        footer_template: str = "",
        prefer_css_page_size: bool | None = None,
        print_background: bool = True,
    ) -> bytes:
        """Render the current page to PDF bytes via CDP Page.printToPDF."""
        session_id = self._require_session_id()

        effective_header_footer = display_header_footer
        if (
            effective_header_footer
            and geometry is not None
            and (geometry.margin_top_mm < 8.0 or geometry.margin_bottom_mm < 8.0)
        ):
            effective_header_footer = False

        if effective_header_footer:
            if geometry is None:
                raise ValueError("geometry is required when display_header_footer is True")
            prefer_css = False if prefer_css_page_size is None else prefer_css_page_size
            params: dict[str, Any] = {
                "printBackground": print_background,
                "preferCSSPageSize": prefer_css,
                "displayHeaderFooter": True,
                "headerTemplate": header_template or "<span></span>",
                "footerTemplate": footer_template or "<span></span>",
                "paperWidth": geometry.page_width_mm / _MM_PER_INCH,
                "paperHeight": geometry.page_height_mm / _MM_PER_INCH,
                "marginTop": geometry.margin_top_mm / _MM_PER_INCH,
                "marginRight": geometry.margin_right_mm / _MM_PER_INCH,
                "marginBottom": geometry.margin_bottom_mm / _MM_PER_INCH,
                "marginLeft": geometry.margin_left_mm / _MM_PER_INCH,
            }
        else:
            prefer_css = True if prefer_css_page_size is None else prefer_css_page_size
            params = {
                "printBackground": print_background,
                "preferCSSPageSize": prefer_css,
                "displayHeaderFooter": False,
            }

        pdf_res = self._send_cdp("Page.printToPDF", params, session_id=session_id)
        data_b64 = pdf_res.get("data")
        if not isinstance(data_b64, str):
            raise CdpProtocolError(
                self._format_error("Page.printToPDF response missing base64 'data' field")
            )
        try:
            return base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CdpProtocolError(
                self._format_error(f"Page.printToPDF returned invalid base64 PDF data: {exc}")
            ) from exc

    def close(self) -> None:
        """Idempotently close WebSocket, terminate/kill browser process, and clean temp dir."""
        sock = self._sock
        self._sock = None
        if sock is not None:
            with contextlib.suppress(CdpError, OSError):
                # Best-effort WebSocket close frame before closing socket.
                first_byte = 0x88
                mask_key = os.urandom(4)
                frame = struct.pack("!BB", first_byte, 0x80) + mask_key
                sock.settimeout(0.5)
                sock.sendall(frame)
            with contextlib.suppress(OSError):
                sock.close()

        proc = self._proc
        self._proc = None
        if proc is not None:
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        proc.kill()
                    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                        proc.wait(timeout=2.0)
            else:
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    proc.wait(timeout=0.5)

        stderr_thread = self._stderr_thread
        self._stderr_thread = None
        if stderr_thread is not None and stderr_thread.is_alive():
            stderr_thread.join(timeout=0.5)

        temp_dir = self._temp_dir
        self._temp_dir = None
        if temp_dir is not None:
            temp_path = Path(temp_dir.name)
            with contextlib.suppress(OSError):
                temp_dir.cleanup()
            if temp_path.exists():
                for _ in range(5):
                    time.sleep(0.05)
                    with contextlib.suppress(OSError):
                        shutil.rmtree(temp_path)
                    if not temp_path.exists():
                        break

        self._session_id = None
        self._target_id = None
        self._started = False
        self._closed = True
