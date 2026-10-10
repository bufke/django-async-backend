from django.db import models
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

from .utils import names


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

    async def test_writes_run_on_the_async_connection(self):
        tags = self.owner.tags(manager="async_objects")

        async with AsyncCaptureQueriesContext(
            async_connections["default"]
        ) as ctx:
            await tags.aadd(self.django)
            await tags.aset([])

        self.assertTrue(ctx.captured_queries)
        self.assertEqual(await names(tags), [])

    async def test_writes_join_the_surrounding_transaction(self):
        tags = self.owner.tags(manager="async_objects")

        with self.assertRaises(ZeroDivisionError):
            async with async_atomic():
                await tags.aadd(self.django)
                self.assertEqual(await names(tags), ["django"])
                1 / 0

        self.assertEqual(await names(tags), [])
