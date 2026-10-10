import asyncio

from django.db import models
from django.db.models import signals
from django.test.utils import isolate_apps
from test_app.models import (
    M2MOwnerModel,
    M2MTagModel,
)

from django_async_backend.db import async_connections
from django_async_backend.db.models.manager import AsyncManager
from django_async_backend.db.transaction import async_atomic
from django_async_backend.test import (
    AsyncCaptureQueriesContext,
    AsyncioTestCase,
)

from .utils import (
    RecordM2MChanged,
    names,
)


class TestManyRelatedManager(AsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = await M2MOwnerModel.async_objects.acreate(name="owner")
        self.django = await M2MTagModel.async_objects.acreate(name="django")

    async def test_default_manager_stays_sync(self):
        self.assertNotIsInstance(self.owner.tags, AsyncManager)
        self.assertTrue(hasattr(self.owner.tags, "add"))

    async def test_async_manager_has_no_sync_writes(self):
        tags = self.owner.tags(manager="async_objects")

        self.assertIsInstance(tags, AsyncManager)
        for name in ("add", "remove", "clear", "set", "create"):
            with self.subTest(name):
                self.assertFalse(hasattr(tags, name))

    async def test_calling_async_manager_keeps_it_async(self):
        tags = self.owner.tags(manager="async_objects")

        self.assertIsInstance(tags(manager="async_objects"), AsyncManager)

    async def test_calling_async_manager_can_go_back_to_sync(self):
        tags = self.owner.tags(manager="async_objects")(manager="objects")

        self.assertNotIsInstance(tags, AsyncManager)
        self.assertTrue(hasattr(tags, "add"))

    async def test_async_default_manager_makes_the_accessor_async(self):
        with isolate_apps("test_app"):

            class Book(models.Model):
                async_objects = AsyncManager()

                class Meta:
                    app_label = "test_app"

            class Shelf(models.Model):
                books = models.ManyToManyField(Book)

                class Meta:
                    app_label = "test_app"

        books = Shelf(pk=1).books

        self.assertIsInstance(books, AsyncManager)
        self.assertFalse(hasattr(books, "add"))

    async def test_is_the_generated_async_class(self):
        tags = self.owner.tags(manager="async_objects")

        self.assertEqual(
            type(tags).__module__,
            "django_async_backend.db.models.fields.related_descriptors",
        )

    async def test_async_class_is_built_once_per_relation(self):
        first = self.owner.tags(manager="async_objects")
        second = self.owner.tags(manager="async_objects")

        self.assertIs(type(first), type(second))

    async def test_writes_run_on_the_async_connection(self):
        tags = self.owner.tags(manager="async_objects")

        async with AsyncCaptureQueriesContext(
            async_connections["default"]
        ) as ctx:
            await tags.aadd(self.django)
            await tags.aremove(self.django)

        statements = [
            query["sql"].split()[0] for query in ctx.captured_queries
        ]
        self.assertEqual(statements, ["INSERT", "DELETE"])


class TestManyRelatedManagerTransaction(RecordM2MChanged, AsyncioTestCase):
    """Every write joins the surrounding async transaction, including the
    paths that send m2m_changed, so a rollback undoes it."""

    m2m_sender = M2MOwnerModel.tags.through

    async def asyncSetUp(self):
        self.owner = await M2MOwnerModel.async_objects.acreate(name="owner")
        self.django = await M2MTagModel.async_objects.acreate(name="django")
        self.python = await M2MTagModel.async_objects.acreate(name="python")
        self.tags = self.owner.tags(manager="async_objects")
        await self.tags.aadd(self.django)

    async def assertRolledBack(self, write):
        with self.assertRaises(ZeroDivisionError):
            async with async_atomic():
                # A write that fell back to the sync connection would wait
                # on this transaction's locks forever.
                async with asyncio.timeout(5):
                    await write()
                1 / 0

        self.assertEqual(
            await names(M2MTagModel.async_objects),
            [
                "django",
                "python",
            ],
        )
        self.assertEqual(await names(self.tags), ["django"])

    async def test_aadd(self):
        await self.assertRolledBack(lambda: self.tags.aadd(self.python))

    async def test_aadd_without_signals(self):
        signals.m2m_changed.disconnect(self.receiver, sender=self.m2m_sender)

        await self.assertRolledBack(lambda: self.tags.aadd(self.python))

    async def test_aremove(self):
        await self.assertRolledBack(lambda: self.tags.aremove(self.django))

    async def test_aclear(self):
        await self.assertRolledBack(self.tags.aclear)

    async def test_aset(self):
        await self.assertRolledBack(lambda: self.tags.aset([self.python]))

    async def test_acreate(self):
        await self.assertRolledBack(lambda: self.tags.acreate(name="rust"))
