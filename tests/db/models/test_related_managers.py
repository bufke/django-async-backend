from django.core.exceptions import SynchronousOnlyOperation
from django.db import DEFAULT_DB_ALIAS
from test_app.models import (
    TaggedModel,
    TagModel,
    TestModel,
)

from django_async_backend.db import async_connections
from django_async_backend.db.models.manager import AsyncManager
from django_async_backend.test import (
    AsyncCaptureQueriesContext,
    AsyncioTestCase,
)


class TestReverseForeignKeyRelatedManager(AsyncioTestCase):
    """Reverse FK access through obj.related_set(manager="async_objects").

    Django builds the related manager dynamically from any named manager on
    the related model, so the ``manager`` kwarg yields a RelatedManager that
    subclasses AsyncManager and runs on the async connection.
    """

    async def asyncSetUp(self):
        self.parent = await TestModel.async_objects.acreate(name="parent")
        self.child1 = await TestModel.async_objects.acreate(
            name="child1", relative=self.parent
        )
        self.child2 = await TestModel.async_objects.acreate(
            name="child2", relative=self.parent
        )
        await TestModel.async_objects.acreate(name="unrelated")

    async def test_returns_async_manager_subclass(self):
        manager = self.parent.relatives(manager="async_objects")
        self.assertIsInstance(manager, AsyncManager)

    async def test_iteration_uses_async_connection(self):
        manager = self.parent.relatives(manager="async_objects")

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            names = sorted([obj.name async for obj in manager.all()])

        self.assertEqual(names, ["child1", "child2"])
        self.assertEqual(len(ctx.captured_queries), 1)

    async def test_terminal_methods_use_async_connection(self):
        manager = self.parent.relatives(manager="async_objects")

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            self.assertEqual(await manager.acount(), 2)
            self.assertTrue(await manager.aexists())

            first = await manager.order_by("name").afirst()
            self.assertEqual(first.name, "child1")

            child2 = await manager.aget(name="child2")
            self.assertEqual(child2.pk, self.child2.pk)

            self.assertEqual(await manager.filter(name="child1").acount(), 1)

            names = [
                name
                async for name in manager.values_list(
                    "name", flat=True
                ).order_by("name")
            ]
            self.assertEqual(names, ["child1", "child2"])

        self.assertEqual(len(ctx.captured_queries), 6)

    async def test_results_have_parent_cached(self):
        # _apply_rel_filters seeds _known_related_objects, so accessing the
        # forward FK on fetched rows does not need another query.
        manager = self.parent.relatives(manager="async_objects")
        children = [obj async for obj in manager.all()]

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            for child in children:
                self.assertEqual(child.relative.pk, self.parent.pk)

        self.assertEqual(len(ctx.captured_queries), 0)

    async def test_forward_fk_attribute_access_raises(self):
        # There is no async attribute access for the forward side; an
        # uncached access fails loudly instead of silently using the sync
        # connection. Use Model.async_objects.aget(...) instead.
        child = await TestModel.async_objects.aget(name="child1")
        with self.assertRaises(SynchronousOnlyOperation):
            child.relative

    async def test_mutation_methods_are_not_supported(self):
        # The dynamic RelatedManager overrides create/get_or_create/... with
        # sync implementations that require sync queryset methods AsyncManager
        # does not provide. They must fail; use e.g.
        # TestModel.async_objects.acreate(relative=parent) instead.
        manager = self.parent.relatives(manager="async_objects")
        with self.assertRaises(AttributeError):
            await manager.acreate(name="child3")
        with self.assertRaises(AttributeError):
            await manager.aget_or_create(name="child3")


class TestManyToManyRelatedManager(AsyncioTestCase):
    """M2M access through obj.m2m_field(manager="async_objects")."""

    async def asyncSetUp(self):
        self.tagged = await TaggedModel.async_objects.acreate(name="tagged1")
        self.other = await TaggedModel.async_objects.acreate(name="tagged2")
        self.tag_a = await TagModel.async_objects.acreate(name="a")
        self.tag_b = await TagModel.async_objects.acreate(name="b")
        # The auto-created through model has no async_objects manager, so
        # populate the through table with raw SQL on the async connection.
        async with await async_connections[
            DEFAULT_DB_ALIAS
        ].cursor() as cursor:
            for tagged_id, tag_id in [
                (self.tagged.pk, self.tag_a.pk),
                (self.tagged.pk, self.tag_b.pk),
                (self.other.pk, self.tag_b.pk),
            ]:
                await cursor.execute(
                    "INSERT INTO tagged_model_tags "
                    "(taggedmodel_id, tagmodel_id) VALUES (%s, %s);",
                    [tagged_id, tag_id],
                )

    async def test_forward_reads_use_async_connection(self):
        manager = self.tagged.tags(manager="async_objects")
        self.assertIsInstance(manager, AsyncManager)

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            names = sorted([tag.name async for tag in manager.all()])
            self.assertEqual(names, ["a", "b"])

            self.assertEqual(await manager.acount(), 2)
            self.assertTrue(await manager.aexists())

            tag_a = await manager.aget(name="a")
            self.assertEqual(tag_a.pk, self.tag_a.pk)

            self.assertEqual(await manager.filter(name="b").acount(), 1)

        self.assertEqual(len(ctx.captured_queries), 5)

    async def test_reverse_reads_use_async_connection(self):
        manager = self.tag_b.tagged(manager="async_objects")

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            names = sorted([obj.name async for obj in manager.all()])
            self.assertEqual(names, ["tagged1", "tagged2"])
            self.assertEqual(await manager.acount(), 2)

        self.assertEqual(len(ctx.captured_queries), 2)

    async def test_acreate_is_not_supported(self):
        manager = self.tagged.tags(manager="async_objects")
        with self.assertRaises(AttributeError):
            await manager.acreate(name="c")
