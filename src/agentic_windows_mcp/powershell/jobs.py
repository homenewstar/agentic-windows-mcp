"""Background PowerShell jobs (issue #2).

The synchronous executor waits for the child's output pipes to close, so a
command that starts a detached long-lived process (a dev server via
``Start-Process`` / ``cmd /c start /b``) blocks until the tool timeout even
though the command itself already succeeded — and the caller cannot tell
"failed" from "succeeded but reporting hung".

Jobs fix that: the command is launched once, the call returns a job id
immediately, and the job's *process state* (running / exited + exit code) is
tracked independently of its pipes. Two daemon threads drain stdout/stderr
into capped buffers, so output stays retrievable incrementally via offsets
even after the process exits, including output a detached grandchild writes
into the inherited pipes afterwards.
"""

from __future__ import annotations

import codecs
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_STREAM_CHARS = 1_000_000
MAX_JOBS = 32


class StreamBuffer:
    """Append-only text buffer with a rolling cap.

    ``dropped`` counts characters discarded from the head once the cap is
    exceeded, so readers can convert between absolute stream offsets and
    buffer positions and can tell that early output was lost.
    """

    def __init__(self, cap: int = MAX_STREAM_CHARS) -> None:
        self._cap = cap
        self._chunks: list[str] = []
        self._length = 0
        self.dropped = 0
        self._lock = threading.Lock()

    def append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._chunks.append(text)
            self._length += len(text)
            overflow = self._length - self._cap
            while overflow > 0 and self._chunks:
                head = self._chunks[0]
                if len(head) <= overflow:
                    self._chunks.pop(0)
                    self._length -= len(head)
                    self.dropped += len(head)
                    overflow -= len(head)
                else:
                    self._chunks[0] = head[overflow:]
                    self._length -= overflow
                    self.dropped += overflow
                    overflow = 0

    @property
    def total(self) -> int:
        """Absolute number of characters ever written to the stream."""
        with self._lock:
            return self.dropped + self._length

    def read(self, offset: int) -> tuple[str, int, int]:
        """Return ``(text, next_offset, dropped)`` for chars from ``offset``.

        Offsets are absolute (they count characters dropped by the cap too);
        an offset inside the dropped region reads from the oldest retained
        character instead.
        """
        with self._lock:
            total = self.dropped + self._length
            start = max(offset, self.dropped)
            if start >= total:
                return "", total, self.dropped
            text = "".join(self._chunks)[start - self.dropped:]
            return text, total, self.dropped


@dataclass
class Job:
    id: str
    command: str
    pid: int
    started_at: float  # wall clock
    _proc: subprocess.Popen = field(repr=False)
    _mono: float = field(repr=False)  # monotonic start
    stdout: StreamBuffer = field(default_factory=StreamBuffer)
    stderr: StreamBuffer = field(default_factory=StreamBuffer)
    stopped: bool = False
    _ended_mono: float | None = field(default=None, repr=False)

    def _refresh(self) -> int | None:
        code = self._proc.poll()
        if code is not None and self._ended_mono is None:
            self._ended_mono = time.monotonic()
        return code

    @property
    def running(self) -> bool:
        return self._refresh() is None

    @property
    def exit_code(self) -> int | None:
        return self._refresh()

    @property
    def elapsed(self) -> float:
        self._refresh()
        end = self._ended_mono if self._ended_mono is not None else time.monotonic()
        return max(0.0, end - self._mono)

    def status(self) -> dict:
        return {
            "job_id": self.id,
            "state": "running" if self.running else "exited",
            "exit_code": self.exit_code,
            "pid": self.pid,
            "elapsed_seconds": round(self.elapsed, 2),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.started_at)),
            "command": self.command,
            "stopped": self.stopped,
            "stdout_chars": self.stdout.total,
            "stderr_chars": self.stderr.total,
        }


def _pump(stream, buffer: StreamBuffer) -> None:
    """Drain one pipe into its buffer until EOF (daemon thread)."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            buffer.append(decoder.decode(chunk))
        tail = decoder.decode(b"", final=True)
        if tail:
            buffer.append(tail)
    except (ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


class JobManager:
    """Owns every background job for the lifetime of the server process."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def start(self, command: str, args: list[str], env: dict, cwd: str) -> Job:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,  # never inherit the MCP pipe stdin
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            creationflags=creationflags,
        )
        with self._lock:
            self._seq += 1
            job = Job(
                id=f"job-{self._seq}",
                command=command,
                pid=proc.pid,
                started_at=time.time(),
                _proc=proc,
                _mono=time.monotonic(),
            )
            self._jobs[job.id] = job
            self._evict_locked()
        for stream, buffer in ((proc.stdout, job.stdout), (proc.stderr, job.stderr)):
            threading.Thread(target=_pump, args=(stream, buffer), daemon=True).start()
        logger.info("Started background job %s (pid %s)", job.id, job.pid)
        return job

    def _evict_locked(self) -> None:
        """Drop the oldest exited jobs once the registry exceeds MAX_JOBS."""
        while len(self._jobs) > MAX_JOBS:
            exited = [j for j in self._jobs.values() if not j.running]
            if not exited:
                break
            oldest = min(exited, key=lambda j: j.started_at)
            del self._jobs[oldest.id]

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"Unknown job id: {job_id}")
        return job

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._jobs)

    def output(self, job_id: str, stream: str, offset: int) -> dict:
        job = self.get(job_id)
        if stream == "stdout":
            buffers = [("stdout", job.stdout)]
        elif stream == "stderr":
            buffers = [("stderr", job.stderr)]
        elif stream == "both":
            buffers = [("stdout", job.stdout), ("stderr", job.stderr)]
        else:
            raise ValueError("stream must be one of: stdout, stderr, both")
        result: dict = {"job_id": job.id, "state": "running" if job.running else "exited"}
        for name, buffer in buffers:
            # For "both", a single shared offset is ambiguous across two
            # streams; callers page streams individually. With stream="both"
            # the offset applies to each stream independently.
            text, next_offset, dropped = buffer.read(max(0, offset))
            result[name] = text
            result[f"{name}_next_offset"] = next_offset
            result[f"{name}_dropped"] = dropped
        return result

    def stop(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job.running:
            subprocess.run(
                ["taskkill", "/PID", str(job.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            job.stopped = True
            try:
                job._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return job.status()


JOBS = JobManager()
