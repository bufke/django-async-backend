"""
A connection must stay usable by the next task to reach for its alias.

The store is context-local, but a wrapper is an object, and Django and
asgiref copy context between tasks: a child task starts from a copy of
its parent's, and ``Signal.asend`` and ``async_to_sync`` also write the
child's copy back into the caller. A wrapper can therefore be seen by a
task other than the one that first used it, and that task is refused.

Each scenario runs in a task of its own with a fresh context, standing
in for a request or worker task whose context holds no wrapper yet.

The ``expected_failure_because`` tests are the bug (#92).
"""

import asyncio
import contextvars
import traceback
from functools import wraps
from unittest import expectedFailure

from asgiref.sync import (
    async_to_sync,
    sync_to_async,
)
from django.dispatch import Signal
from django.tasks import (
    TaskResultStatus,
    task,
)

from django_async_backend.db import (
    async_connections,
    async_new_connection,
)
from django_async_backend.test import AsyncioTransactionTestCase

OWNERSHIP_ERROR = "can only be used by the task that first used it"


def expected_failure_because(reason):
    """``expectedFailure``, but only for a failure mentioning ``reason``.

    Any other failure is reported as an error: unittest stops expecting
    a failure before it runs cleanups.
    """

    def decorator(test):
        @expectedFailure
        @wraps(test)
        async def inner(self):
            try:
                await test(self)
            except Exception as exc:
                if reason not in "".join(traceback.format_exception(exc)):
                    self.addCleanup(
                        self.fail, "failed for another reason: %r" % exc
                    )
                raise

        return inner

    return decorator


async def query():
    async with await async_connections["default"].cursor() as cursor:
        await cursor.execute("SELECT 1")


@task
async def query_task():
    await query()


async def run_as_new_context(scenario):
    """Run ``scenario`` in a task of its own with a fresh context,
    releasing whatever it leaves in the store."""

    async def scope():
        try:
            return await scenario()
        finally:
            for connection in async_connections.all(initialized_only=True):
                await connection.close()
                del async_connections[connection.alias]

    return await asyncio.create_task(scope(), context=contextvars.Context())


def signal_with_querying_receiver():
    signal = Signal()

    async def receiver(**kwargs):
        await query()

    signal.connect(receiver, weak=False)
    return signal


class ContextBoundaryTests(AsyncioTransactionTestCase):
    def assertSucceeded(self, result):
        self.assertEqual(
            result.status,
            TaskResultStatus.SUCCESSFUL,
            result.errors and result.errors[0].traceback,
        )

    @expected_failure_because(OWNERSHIP_ERROR)
    async def test_caller_after_async_receiver(self):
        # asend runs async receivers in child tasks over one copy of the
        # caller's context, then writes that copy back into the caller,
        # wrapper included.
        signal = signal_with_querying_receiver()

        async def scenario():
            await signal.asend(sender=None)
            await query()

        await run_as_new_context(scenario)

    @expected_failure_because(OWNERSHIP_ERROR)
    async def test_caller_after_sync_middleware(self):
        # A sync-only middleware puts the rest of the chain behind
        # sync_to_async -> async_to_sync. The inner part runs in a new
        # task on the same loop, and both hops copy context back.
        async def scenario():
            await sync_to_async(async_to_sync(query))()
            await query()

        await run_as_new_context(scenario)

    @expected_failure_because(OWNERSHIP_ERROR)
    async def test_parent_after_child_task_queried_first(self):
        # A wrapper that exists but was never used is shared by reference
        # with every child task, and the first to query owns it. Here
        # that is the child, and the parent is locked out of its own
        # alias. request_started's receiver creates such a wrapper for
        # every async alias before the view runs.
        async def scenario():
            async_connections["default"]
            await asyncio.create_task(query())
            await query()

        await run_as_new_context(scenario)

    @expected_failure_because(OWNERSHIP_ERROR)
    async def test_immediate_task_after_view_queried(self):
        # aenqueue hops to a thread, and the immediate backend runs an
        # async task back on this loop through async_to_sync: a new task,
        # started from a copy of the enqueuing context.
        async def scenario():
            await query()
            return await query_task.aenqueue()

        self.assertSucceeded(await run_as_new_context(scenario))

    @expected_failure_because(OWNERSHIP_ERROR)
    async def test_immediate_task_after_view_closed(self):
        # As above, after a close(): closing returns the connection but
        # leaves the wrapper owned.
        async def scenario():
            await query()
            await async_connections["default"].close()
            return await query_task.aenqueue()

        self.assertSucceeded(await run_as_new_context(scenario))

    async def test_scenarios_succeed_without_the_crossing(self):
        # Each scenario above minus its last step, so a failure there can
        # only come from crossing the boundary, not from the setup.
        signal = signal_with_querying_receiver()

        async def child_task():
            async_connections["default"]
            await asyncio.create_task(query())

        async def immediate_task():
            return await query_task.aenqueue()

        scenarios = {
            "async receiver": lambda: signal.asend(sender=None),
            "sync middleware": sync_to_async(async_to_sync(query)),
            "child task": child_task,
        }
        for name, scenario in scenarios.items():
            with self.subTest(name):
                await run_as_new_context(scenario)

        self.assertSucceeded(await run_as_new_context(immediate_task))

    async def test_parent_after_child_on_new_connection(self):
        # The documented way to give a child its own connection must
        # keep leaving the parent's alone.
        async def scenario():
            await query()
            await asyncio.create_task(async_new_connection(query()))
            await query()

        await run_as_new_context(scenario)
