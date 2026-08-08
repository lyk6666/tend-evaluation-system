from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from .contracts import RunStatus, WorkItemView
from .store import RunStore


class WorkExecutor(Protocol):
    async def __call__(self, item: WorkItemView) -> dict[str, Any]: ...


async def unavailable_executor(item: WorkItemView) -> dict[str, Any]:
    raise RuntimeError(f"method execution adapter is not ready for {item.method_id}")


class RunOrchestrator:
    """Persistent task-boundary scheduler with cooperative pause and active cancellation."""

    def __init__(
        self,
        store: RunStore,
        executor: WorkExecutor | Callable[[WorkItemView], Awaitable[dict[str, Any]]] = unavailable_executor,
        *,
        poll_interval: float = 0.25,
        retry_initial_delay: float = 2.0,
        retry_max_delay: float = 60.0,
        max_generation_attempts: int = 2,
    ):
        self.store = store
        self.executor = executor
        self.poll_interval = poll_interval
        self.retry_initial_delay = retry_initial_delay
        self.retry_max_delay = retry_max_delay
        self.max_generation_attempts = max_generation_attempts
        self._manager: asyncio.Task[None] | None = None
        self._run_tasks: dict[str, asyncio.Task[None]] = {}
        self._stopping = False

    async def start(self) -> None:
        self.store.initialize()
        self.store.recover_incomplete()
        self._stopping = False
        self._manager = asyncio.create_task(self._manage(), name="tend-run-manager")

    async def stop(self) -> None:
        self._stopping = True
        if self._manager:
            self._manager.cancel()
            with suppress(asyncio.CancelledError):
                await self._manager
        tasks = list(self._run_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._run_tasks.clear()
        close = getattr(self.executor, "aclose", None)
        if callable(close):
            await close()

    async def _manage(self) -> None:
        while not self._stopping:
            for run_id in self.store.actionable_run_ids():
                task = self._run_tasks.get(run_id)
                if task is None or task.done():
                    self._run_tasks[run_id] = asyncio.create_task(
                        self._run(run_id), name=f"tend-run-{run_id[:8]}"
                    )
            completed = [run_id for run_id, task in self._run_tasks.items() if task.done()]
            for run_id in completed:
                task = self._run_tasks.pop(run_id)
                with suppress(asyncio.CancelledError, Exception):
                    task.result()
            await asyncio.sleep(self.poll_interval)

    async def _run(self, run_id: str) -> None:
        self.store.mark_running(run_id)
        run = self.store.get_run(run_id)
        if run is None:
            return
        active: set[asyncio.Task[None]] = set()
        worker_counter = 0
        try:
            while not self._stopping:
                run = self.store.get_run(run_id)
                if run is None:
                    return
                if run.status == RunStatus.CANCELLING:
                    for task in active:
                        task.cancel()
                    if active:
                        await asyncio.gather(*active, return_exceptions=True)
                    self.store.cancel_remaining(run_id)
                    return
                if run.status == RunStatus.PAUSING:
                    if not active:
                        self.store.mark_paused(run_id)
                        return
                elif run.status != RunStatus.RUNNING:
                    return
                else:
                    while len(active) < run.concurrency:
                        worker_counter += 1
                        item = self.store.claim_next(run_id, f"worker-{worker_counter}")
                        if item is None:
                            break
                        task = asyncio.create_task(self._execute(item))
                        active.add(task)

                if active:
                    done, active = await asyncio.wait(
                        active,
                        timeout=self.poll_interval,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        with suppress(asyncio.CancelledError, Exception):
                            task.result()
                else:
                    finalized = self.store.finalize_if_complete(run_id)
                    if finalized and finalized.status in {
                        RunStatus.COMPLETED,
                        RunStatus.FAILED,
                        RunStatus.CANCELLED,
                    }:
                        return
                    await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            for task in active:
                task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            raise
        finally:
            run = self.store.get_run(run_id)
            if run and run.status == RunStatus.COMPLETED:
                finalize_run = getattr(self.executor, "finalize_run", None)
                if callable(finalize_run):
                    await finalize_run(run_id)
            if run and run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                close_run = getattr(self.executor, "close_run", None)
                if callable(close_run):
                    await close_run(run_id)

    async def _execute(self, item: WorkItemView) -> None:
        try:
            result = await self.executor(item)
        except asyncio.CancelledError:
            run = self.store.get_run(item.run_id)
            if run and run.status == RunStatus.CANCELLING:
                self.store.finish_work(item.id, cancelled=True)
            else:
                self.store.requeue_work(item.id)
            raise
        except Exception as error:  # noqa: BLE001 - separate transport retries from model responses
            message = f"{type(error).__name__}: {error}"
            consumed_generation_attempt = bool(
                getattr(error, "consumes_generation_attempt", False)
            )
            next_generation_attempt = item.generation_attempt + int(consumed_generation_attempt)
            if (
                consumed_generation_attempt
                and next_generation_attempt >= self.max_generation_attempts
            ):
                self.store.finish_work(
                    item.id,
                    error=(
                        "generation blocked after "
                        f"{next_generation_attempt} API-backed responses: {message}"
                    ),
                    consumed_generation_attempt=True,
                )
            else:
                self.store.retry_work(
                    item.id,
                    error=message,
                    delay_seconds=self._retry_delay(item.attempt),
                    consumed_generation_attempt=consumed_generation_attempt,
                )
        else:
            self.store.finish_work(item.id, result=result)

    def _retry_delay(self, attempt: int) -> float:
        exponent = max(0, min(attempt - 1, 12))
        return min(self.retry_max_delay, self.retry_initial_delay * (2**exponent))
