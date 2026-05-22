import asyncio

from django.db import DEFAULT_DB_ALIAS
from django.utils.decorators import async_only_middleware

from django_async_backend.db import async_connections


@async_only_middleware
def close_async_connections(get_response):
    """Close async DB connections after each request.

    Django's request_finished signal closes sync connections, but
    async_connections needs its own cleanup to return connections
    to the pool. Add this to MIDDLEWARE when using ASGI:

        MIDDLEWARE = [
            "django_async_backend.middleware.close_async_connections",
            ...
        ]
    """

    async def middleware(request):
        try:
            return await get_response(request)
        finally:
            await asyncio.shield(async_connections.close_all())

    return middleware


@async_only_middleware
def acquire_pool_connections(get_response):
    """Pre-acquire the default DB wrapper's pool connection at request
    entry, paired with a shielded putconn() on exit.

    The default lazy-connect pattern (the wrapper calls pool.getconn()
    inside the ORM machinery, e.g. from cursor()) leaks under client-
    side cancellation pressure: if the request is cancelled by the
    client (short timeout, browser tab close, etc.) during one of
    psycopg's internal awaits in `pool.getconn()` — specifically inside
    the waiter queue — the connection is checked out from psycopg's
    `_in_use` ledger but the wrapper's `self.connection` assignment
    never runs, so the request-exit cleanup can't `putconn()` it.
    Over enough cancelled requests, the pool wedges.

    This middleware closes the window two ways:

    1. **Timing.** Acquire the connection AT request entry, where
       almost no time has elapsed since the ASGI task was scheduled.
       Client timeouts (typically ≥ ~10ms) haven't fired yet, so
       cancellation rarely arrives during getconn() itself.

    2. **Pairing.** Bind getconn() and putconn() to the middleware's
       own try/finally (with shielded putconn) so that even if every
       other cleanup path fails, putconn runs.

    Mirrors the timing & pairing properties of raw
    `async with pool.connection():`, which is robust under cancellation
    pressure (verified empirically: bare psycopg via `pool.connection()`
    survives the same load that wedges the default lazy-connect path).

    Use INSTEAD of `close_async_connections` for ASGI deployments that
    face client-side cancellation (browsers, mobile clients, aggressive
    HTTP timeouts, load tests). Add to MIDDLEWARE:

        MIDDLEWARE = [
            "django_async_backend.middleware.acquire_pool_connections",
            ...
        ]

    Caveats:

    - Pre-acquires for EVERY request — including DB-free endpoints like
      /_health/. Under pool saturation, non-DB endpoints will wait for
      a connection. If that's not acceptable, stay on
      `close_async_connections` or opt in selectively via a sub-route
      middleware.
    - Pre-acquires only for the `default` alias. If your project uses
      multiple database aliases under load, additional protection
      (per-alias middleware, or extending this helper) is needed.
    - Wrappers created inside `_independent_connection()` (gather +
      parallel queries) still lazy-connect from their gather child
      tasks and remain vulnerable to the same leak. Workloads heavy on
      `asyncio.gather + _independent_connection` need further work.
    """

    async def middleware(request):
        wrapper = async_connections[DEFAULT_DB_ALIAS]
        if not wrapper.pool:
            # No pool — fall through to the standard lazy + close path.
            try:
                return await get_response(request)
            finally:
                await asyncio.shield(async_connections.close_all())
            return
        # Pool configured: pre-acquire at request entry, with a
        # `conn = None` sentinel so the outer finally can safely
        # putconn() any conn we obtained — even if cancellation
        # arrived at the getconn() await itself.
        conn = None
        try:
            await wrapper.pool.open()
            conn = await wrapper.pool.getconn()
            wrapper.connection = conn
            await wrapper.set_autocommit(
                wrapper.settings_dict["AUTOCOMMIT"]
            )
            await wrapper.init_connection_state()
            try:
                return await get_response(request)
            finally:
                # Clear `wrapper.connection` BEFORE close_all so
                # close_all treats this wrapper as already-closed and
                # skips its putconn() — we'll do it ourselves below
                # via the outer finally. Other wrappers (alias !=
                # default, or wrappers created inside
                # _independent_connection) still get their normal close
                # path.
                wrapper.connection = None
                await asyncio.shield(async_connections.close_all())
        finally:
            # Always clear our reference and putconn() the conn if we
            # got one. Shielded so a re-cancel at the putconn await
            # can't skip it.
            wrapper.connection = None
            if conn is not None:
                await asyncio.shield(wrapper.pool.putconn(conn))

    return middleware
