from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from .events import EventBuffer
from .models import CancellationToken, TaskCancelled, TaskEvent, TaskEventKind, TaskSnapshot, TaskState
from .process import ProcessRunner

Worker = Callable[[CancellationToken, Callable[[TaskEvent], None]], Any]
DoneCallback = Callable[[TaskSnapshot], None]
ACTIVE_STATES = frozenset({TaskState.CREATED, TaskState.RUNNING, TaskState.CANCELLING})


class _SnapshotStore(dict):
    """记录每次写入的 revision，供 snapshots() 判断缓存 tuple 是否失效。"""

    __slots__ = ("revision",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.revision = 0

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        self.revision += 1

    def __delitem__(self, key: Any) -> None:
        super().__delitem__(key)
        self.revision += 1

    def clear(self) -> None:
        super().clear()
        self.revision += 1

    def pop(self, *args: Any) -> Any:
        result = super().pop(*args)
        self.revision += 1
        return result

    def popitem(self) -> Any:
        result = super().popitem()
        self.revision += 1
        return result

    def setdefault(self, key: Any, default: Any = None) -> Any:
        result = super().setdefault(key, default)
        self.revision += 1
        return result

    def update(self, *args: Any, **kwargs: Any) -> None:
        super().update(*args, **kwargs)
        self.revision += 1


class TaskEngine:
    def __init__(self, *, event_capacity: int = 0) -> None:
        self._events = EventBuffer(event_capacity)
        self._snapshots: dict[str, TaskSnapshot] = _SnapshotStore()
        self._snapshots_cache: tuple[TaskSnapshot, ...] | None = None
        self._snapshots_revision: int | None = None
        self._tokens: dict[str, CancellationToken] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    @property
    def events(self) -> EventBuffer:
        return self._events

    def start_callable(self, name: str, worker: Worker, *, on_done: DoneCallback | None = None, cancellable: bool = True, _task_id: str | None = None) -> str:
        task_id = _task_id or uuid4().hex
        token = CancellationToken()
        created = TaskSnapshot(task_id=task_id, name=name, cancellable=cancellable)

        def emit(event: TaskEvent) -> None:
            self._events.publish(event if event.task_id == task_id else replace(event, task_id=task_id))

        def transition(state: TaskState, **changes: Any) -> TaskSnapshot:
            with self._lock:
                current = self._snapshots[task_id]
                if current.state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}:
                    return current
                if current.state is TaskState.CANCELLING and state in {TaskState.RUNNING, TaskState.SUCCEEDED}:
                    state = TaskState.CANCELLED
                    changes = {"message": "任务已取消", "error": TaskCancelled("任务已取消")}
                updated = current.transition(state, **changes)
                self._snapshots[task_id] = updated
            emit(TaskEvent(task_id, TaskEventKind.STATE, message=state.value))
            return updated

        def target() -> None:
            try:
                token.raise_if_cancelled()
                transition(TaskState.RUNNING)
                result = worker(token, emit)
                token.raise_if_cancelled()
            except TaskCancelled as exc:
                final = transition(TaskState.CANCELLED, message=str(exc), error=exc)
            except BaseException as exc:
                emit(TaskEvent(task_id, TaskEventKind.ERROR, message=str(exc), payload=exc))
                final = transition(TaskState.FAILED, message=str(exc), error=exc)
            else:
                emit(TaskEvent(task_id, TaskEventKind.RESULT, payload=result))
                final = transition(TaskState.SUCCEEDED, result=result)
            finally:
                with self._lock:
                    self._threads.pop(task_id, None)
            if on_done is not None:
                on_done(final)

        thread = threading.Thread(target=target, name=f"toolbox-task-{task_id[:8]}", daemon=True)
        with self._lock:
            self._snapshots[task_id] = created
            self._tokens[task_id] = token
            self._threads[task_id] = thread
        emit(TaskEvent(task_id, TaskEventKind.STATE, message=TaskState.CREATED.value))
        thread.start()
        return task_id

    def start_process(self, name: str, command: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None, on_done: DoneCallback | None = None, on_output: Callable[[str], None] | None = None, cancellable: bool = True) -> str:
        task_id = uuid4().hex

        def worker(token: CancellationToken, emit: Callable[[TaskEvent], None]) -> int:
            def output(line: str) -> None:
                emit(TaskEvent(task_id, TaskEventKind.LOG, message=line))
                if on_output is not None:
                    on_output(line)
            return ProcessRunner().run(list(command), cwd=cwd, env=env, token=token, on_output=output).returncode

        return self.start_callable(name, worker, on_done=on_done, cancellable=cancellable, _task_id=task_id)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            snapshot = self._snapshots.get(task_id)
            if snapshot is None or snapshot.state not in ACTIVE_STATES or not snapshot.cancellable:
                return False
            self._snapshots[task_id] = snapshot.transition(TaskState.CANCELLING)
            token = self._tokens[task_id]
            # Publish the state and signal together: a worker must not observe
            # accepted cancellation while still seeing an uncancelled token.
            token.cancel()
        self._events.publish(TaskEvent(task_id, TaskEventKind.STATE, message=TaskState.CANCELLING.value))
        return True

    def snapshot(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            return self._snapshots[task_id]

    def snapshots(self) -> tuple[TaskSnapshot, ...]:
        with self._lock:
            store = self._snapshots
            if not isinstance(store, _SnapshotStore):
                # 外部整体替换了 _snapshots；重新纳入 revision 追踪。
                store = self._snapshots = _SnapshotStore(store)
            if (self._snapshots_cache is None
                    or self._snapshots_revision != store.revision):
                self._snapshots_cache = tuple(store.values())
                self._snapshots_revision = store.revision
            return self._snapshots_cache

    def active_snapshots(self) -> tuple[TaskSnapshot, ...]:
        return tuple(item for item in self.snapshots() if item.state in ACTIVE_STATES)

    def has_running_tasks(self) -> bool:
        return bool(self.active_snapshots())

    def shutdown(self, timeout: float = 5.0) -> tuple[TaskSnapshot, ...]:
        for snapshot in self.active_snapshots():
            self.cancel(snapshot.task_id)
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                threads = tuple(self._threads.values())
            if not threads or time.monotonic() >= deadline:
                return self.active_snapshots()
            for thread in threads:
                thread.join(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
