"""Serialize file writes in the single backend worker, including sync and editors."""
import asyncio
import functools
import inspect
import weakref

_locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def serialize_file_write(parameter: str):
    def decorate(fn):
        @functools.wraps(fn)
        async def locked(*args, **kwargs):
            key = kwargs[parameter]
            lock = _locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _locks[key] = lock
            async with lock:
                return await fn(*args, **kwargs)
        locked.__signature__ = inspect.signature(fn, eval_str=True)
        return locked
    return decorate
