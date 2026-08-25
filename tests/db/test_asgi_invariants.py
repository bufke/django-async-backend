"""Properties that hold for every request through a real ASGI stack.

Driving Django's own ``ASGIHandler`` with ``ApplicationCommunicator`` runs
the request signals that manage the connection store the way a server
does. Each test asserts something true of any request rather than the
output of one view, so the assertions keep their meaning as the views
change.
"""

import asyncio
import json
import sys

from asgiref.testing import ApplicationCommunicator
from django.core.asgi import get_asgi_application
from django.core.signals import got_request_exception
from django.http import HttpResponse
from django.urls import path

from django_async_backend.db import async_connections, async_new_connection
from django_async_backend.test import (
    AsyncioTransactionTestCase,
    override_settings,
)

CONNECTION_COUNT = (
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
)


async def scalar(sql):
    connection = async_connections["default"]
    async with await connection.cursor() as cursor:
        await cursor.execute(sql)
        return (await cursor.fetchone())[0]


async def backend_pid(request):
    return HttpResponse(await scalar("SELECT pg_backend_pid()"))


async def fan_out(request):
    """Query, then hand the connection to a child task both ways."""
    await scalar("SELECT 1")
    report = {}

    async def child():
        return await scalar("SELECT pg_backend_pid()")

    try:
        await asyncio.create_task(child())
    except RuntimeError:
        report["shared"] = "refused"
    else:
        report["shared"] = "allowed"

    report["own"] = await asyncio.create_task(async_new_connection(child()))
    return HttpResponse(json.dumps(report))


urlpatterns = [
    path("", backend_pid),
    path("fan-out", fan_out),
]


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"])
class ASGIRequestInvariantTests(AsyncioTransactionTestCase):
    async def _release_test_owned_connections(self):
        """Leave the store as a server would, with nothing in it.

        This runs inside the test method, not setUp: the base class
        re-stamps ownership of every alias before the method body, which
        builds the entries again.
        """
        for connection in async_connections.all():
            await connection.close()
            del async_connections[connection.alias]

    def _capture_view_exceptions(self):
        captured = []

        def record(sender, **kwargs):
            captured.append(sys.exc_info()[1])

        got_request_exception.connect(record)
        self.addCleanup(got_request_exception.disconnect, record)
        return captured

    async def _request(self, path="/"):
        communicator = ApplicationCommunicator(
            get_asgi_application(),
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "path": path,
                "query_string": b"",
                "headers": [(b"host", b"testserver")],
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 0),
            },
        )
        await communicator.send_input({"type": "http.request", "body": b""})
        start = await communicator.receive_output(timeout=10)
        body = await communicator.receive_output(timeout=10)
        await communicator.wait(timeout=10)
        return start["status"], body["body"]

    async def test_concurrent_requests_do_not_share_a_connection(self):
        captured = self._capture_view_exceptions()
        await self._release_test_owned_connections()
        before = await scalar(CONNECTION_COUNT)

        results = await asyncio.gather(*(self._request() for _ in range(8)))

        self.assertEqual(
            [status for status, _ in results],
            [200] * 8,
            "a request failed with: %r" % (captured,),
        )
        # Distinct backends because these settings are unpooled: eight
        # requests in flight at once hold eight connections. Sharing
        # would usually show up as a 500 above, from the ownership
        # guard; this catches sharing that slips past it.
        backends = {body for _, body in results}
        self.assertEqual(
            len(backends),
            8,
            "concurrent requests shared a connection: %r" % (backends,),
        )
        self.assertEqual(
            await scalar(CONNECTION_COUNT),
            before,
            "requests finishing together left connections open",
        )

    async def test_a_request_releases_the_connection_it_opened(self):
        captured = self._capture_view_exceptions()
        await self._release_test_owned_connections()
        before = await scalar(CONNECTION_COUNT)

        status, _ = await self._request()

        self.assertEqual(status, 200, "request failed: %r" % (captured,))
        self.assertEqual(
            await scalar(CONNECTION_COUNT),
            before,
            "the request left a connection open",
        )

    async def test_a_second_request_is_served_and_releases(self):
        captured = self._capture_view_exceptions()
        await self._release_test_owned_connections()
        before = await scalar(CONNECTION_COUNT)

        first_status, _ = await self._request()
        second_status, _ = await self._request()

        self.assertEqual(
            [first_status, second_status],
            [200, 200],
            "a request failed with: %r" % (captured,),
        )
        self.assertEqual(
            await scalar(CONNECTION_COUNT),
            before,
            "a connection outlived the requests that opened it",
        )

    async def test_a_child_task_may_not_share_the_request_connection(self):
        captured = self._capture_view_exceptions()
        await self._release_test_owned_connections()

        status, body = await self._request("/fan-out")

        self.assertEqual(status, 200, "request failed: %r" % (captured,))
        report = json.loads(body)
        self.assertEqual(report["shared"], "refused")
        self.assertTrue(
            report["own"],
            "async_new_connection() did not give the child a connection",
        )
