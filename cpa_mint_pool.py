#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded CPA mint worker pool (register R + mint M, community mint-style).

Replaces unbounded ``threading.Thread`` per account with a fixed worker pool
and a maxsize queue for backpressure when OAuth/browser mint is slow.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

LogFn = Callable[[str], None]
ExportFn = Callable[..., dict[str, Any]]
WriteLocalFn = Callable[..., dict[str, Any]]


@dataclass
class MintJob:
    email: str
    password: str
    sso: str
    log: Optional[LogFn] = None
    delay_sec: float = 5.0
    enqueued_at: float = field(default_factory=time.time)


@dataclass
class MintPoolStats:
    submitted: int = 0
    completed_ok: int = 0
    completed_fail: int = 0
    dropped: int = 0
    in_flight: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "submitted": self.submitted,
            "completed_ok": self.completed_ok,
            "completed_fail": self.completed_fail,
            "dropped": self.dropped,
            "in_flight": self.in_flight,
        }


class MintPool:
    """Process CPA mint jobs with worker threads + queue backpressure."""

    def __init__(self) -> None:
        self._q: queue.Queue[MintJob | None] | None = None
        self._workers: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._stats = MintPoolStats()
        self._export_fn: ExportFn | None = None
        self._write_local_fn: WriteLocalFn | None = None
        self._started = False
        self._worker_n = 0
        self._queue_max = 0

    @property
    def started(self) -> bool:
        return self._started

    def stats(self) -> dict[str, int]:
        with self._lock:
            return self._stats.as_dict()

    def ensure_started(
        self,
        *,
        workers: int,
        queue_max: int,
        export_fn: ExportFn,
        write_local_fn: WriteLocalFn | None = None,
        log: LogFn | None = None,
    ) -> None:
        workers = max(1, min(int(workers or 1), 10))
        queue_max = max(workers, min(int(queue_max or workers * 2), 40))
        with self._lock:
            if self._started:
                # allow growing workers later? keep simple: already started
                self._export_fn = export_fn
                self._write_local_fn = write_local_fn
                return
            self._q = queue.Queue(maxsize=queue_max)
            self._export_fn = export_fn
            self._write_local_fn = write_local_fn
            self._worker_n = workers
            self._queue_max = queue_max
            self._started = True
            for i in range(workers):
                t = threading.Thread(
                    target=self._worker_loop,
                    name=f"cpa-mint-{i}",
                    daemon=True,
                )
                t.start()
                self._workers.append(t)
        if log:
            log(f"[cpa] mint pool started workers={workers} queue_max={queue_max}")

    def _worker_loop(self) -> None:
        assert self._q is not None
        while True:
            job = self._q.get()
            try:
                if job is None:
                    return
                with self._lock:
                    self._stats.in_flight += 1
                self._run_job(job)
            finally:
                if job is not None:
                    with self._lock:
                        self._stats.in_flight = max(0, self._stats.in_flight - 1)
                self._q.task_done()

    def _run_job(self, job: MintJob) -> None:
        log = job.log
        if job.delay_sec > 0:
            time.sleep(job.delay_sec)
        export_fn = self._export_fn
        if export_fn is None:
            with self._lock:
                self._stats.completed_fail += 1
            return
        try:
            r = export_fn(
                job.email,
                job.password,
                sso=job.sso,
                log_callback=log,
                page=None,
            )
            if r.get("ok"):
                if log:
                    log(f"[+] CPA xAI 导出成功: {r.get('path', '')}")
                write_local = self._write_local_fn
                if write_local is not None:
                    try:
                        write_local(r, log_callback=log)
                    except Exception as exc:
                        if log:
                            log(f"[!] 本机 Grok auth 写入失败: {exc}")
                with self._lock:
                    self._stats.completed_ok += 1
            else:
                if log and not r.get("skipped"):
                    log(f"[!] CPA xAI 导出失败: {r.get('error', '未知错误')}")
                with self._lock:
                    self._stats.completed_fail += 1
        except Exception as exc:
            if log:
                log(f"[!] CPA xAI 导出异常: {exc}")
            with self._lock:
                self._stats.completed_fail += 1

    def submit(
        self,
        job: MintJob,
        *,
        block_sec: float = 30.0,
        log: LogFn | None = None,
    ) -> bool:
        """Enqueue job. Returns False if dropped after backpressure wait."""
        if not self._started or self._q is None:
            if log:
                log("[!] mint pool not started")
            return False
        try:
            self._q.put(job, timeout=max(0.0, float(block_sec)))
            with self._lock:
                self._stats.submitted += 1
            if log:
                qsize = self._q.qsize()
                log(f"[cpa] mint queued email={job.email} q={qsize}/{self._queue_max}")
            return True
        except queue.Full:
            with self._lock:
                self._stats.dropped += 1
            if log:
                log(
                    f"[!] mint queue full ({self._queue_max}), drop {job.email} "
                    f"(backpressure)"
                )
            return False

    def wait_done(
        self,
        *,
        timeout: float = 300.0,
        log: LogFn | None = None,
        skip_if: Callable[[], bool] | None = None,
    ) -> None:
        if not self._started or self._q is None:
            return
        if skip_if and skip_if():
            timeout = min(float(timeout or 0), 5.0)
            if log:
                log(f"[*] 停止中，仅短暂等待 mint 队列（{timeout:.0f}s）...")
        if log and not (skip_if and skip_if()):
            with self._lock:
                st = self._stats.as_dict()
            log(
                f"[*] 等待 mint 队列完成 submitted={st['submitted']} "
                f"ok={st['completed_ok']} fail={st['completed_fail']} "
                f"in_flight={st['in_flight']} q={self._q.qsize()}"
            )
        # queue.join with timeout via polling
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            if skip_if and skip_if():
                break
            # unfinished tasks = qsize + in_flight roughly; use join with short timeout
            try:
                # Queue.join has no timeout on older py — poll unfinished_tasks
                unfinished = getattr(self._q, "unfinished_tasks", None)
                if unfinished is not None and int(unfinished) <= 0:
                    break
            except Exception:
                pass
            with self._lock:
                in_flight = self._stats.in_flight
            if self._q.qsize() == 0 and in_flight == 0:
                break
            time.sleep(0.4)
        with self._lock:
            st = self._stats.as_dict()
        if log:
            if st["in_flight"] or (self._q and self._q.qsize()):
                log(
                    f"[!] mint 队列未清空 in_flight={st['in_flight']} "
                    f"q={self._q.qsize() if self._q else 0}"
                )
            else:
                log(
                    f"[+] mint 队列已完成 ok={st['completed_ok']} "
                    f"fail={st['completed_fail']} dropped={st['dropped']}"
                )

    def summary_line(self) -> str:
        st = self.stats()
        return (
            f"[*] CPA mint: ok={st['completed_ok']} fail={st['completed_fail']} "
            f"queued_total={st['submitted']} dropped={st['dropped']} "
            f"in_flight={st['in_flight']}"
        )


_GLOBAL_POOL = MintPool()


def get_mint_pool() -> MintPool:
    return _GLOBAL_POOL


def resolve_worker_count(cfg: dict[str, Any]) -> int:
    """Auto mint workers: config cpa_mint_workers, else min(concurrent, 4)."""
    raw = cfg.get("cpa_mint_workers", None)
    if raw is not None and str(raw).strip() != "":
        try:
            n = int(raw)
            if n > 0:
                return max(1, min(n, 10))
            if n == 0:
                # 0 = inline / no pool (caller decides)
                return 0
        except Exception:
            pass
    conc = int(cfg.get("concurrent_count") or cfg.get("register_threads") or 2)
    return max(1, min(conc, 4))


def resolve_queue_max(cfg: dict[str, Any], workers: int) -> int:
    raw = cfg.get("cpa_mint_queue_max", None)
    if raw is not None and str(raw).strip() != "":
        try:
            return max(workers, min(int(raw), 40))
        except Exception:
            pass
    return max(workers * 2, workers)
