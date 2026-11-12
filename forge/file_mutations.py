'''Session-local file coordination; it grants no filesystem authority.'''
from __future__ import annotations

import asyncio
import os
from pathlib import Path


class FileMutationLocks:
    def __init__(self):
        self._entries = {}

    async def acquire(self, paths):
        keys = set()
        for raw in paths:
            path = Path(raw).resolve(strict=False)
            keys.add(('path', os.path.normcase(str(path))))
            try:
                info = path.stat()
            except FileNotFoundError:
                parent = path.parent
                while not parent.exists():
                    keys.add(('path', os.path.normcase(str(parent))))
                    parent = parent.parent
            else:
                # Hard links have distinct paths but initially share content.
                keys.add(('inode', info.st_dev, info.st_ino))
        # Reserve every key before waiting. Taking individual locks can let a
        # later call hold a key needed by an earlier multi-file patch, while
        # ordered finalization prevents that later call from releasing it.
        previous = {self._entries[key] for key in keys if key in self._entries}
        finished = asyncio.get_running_loop().create_future()
        for key in keys:
            self._entries[key] = finished
        released = False

        def retire(_=None):
            if not released or any(not item.done() for item in previous):
                return
            if not finished.done():
                finished.set_result(None)
            for key in keys:
                if self._entries.get(key) is finished:
                    del self._entries[key]

        def release():
            nonlocal released
            if released:
                return
            released = True
            # A cancelled waiter still forwards earlier reservations. It must
            # not allow a successor to overtake the original active writer.
            for item in previous:
                if not item.done():
                    item.add_done_callback(retire)
            retire()

        try:
            if previous:
                await asyncio.gather(*(asyncio.shield(item) for item in previous))
        except BaseException:
            release()
            raise
        return release


async def finish_thread_write(function, *args):
    '''A cancelled waiter must not release its lease before the write ends.'''
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Drain even repeated cancellation before allowing another file edit.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
