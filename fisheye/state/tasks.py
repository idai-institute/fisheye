"""Cancellation boundaries for work that cannot be stopped once dispatched."""

import asyncio


async def complete_on_cancel(awaitable):
    """Propagate cancellation only after the inner task has settled.

    In particular, cancelling a to_thread waiter cannot stop its SQLite write.
    Repeated cancellations must not let the caller release resources early.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # Retrieve a failure even when cancellation wins.
        raise
