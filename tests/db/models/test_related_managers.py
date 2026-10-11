from django.core.exceptions import SynchronousOnlyOperation
from django.db import DEFAULT_DB_ALIAS
from django.db.models.signals import m2m_changed
from test_app.models import (
    M2MGroupModel,
    M2MMemberModel,
    M2MMembershipModel,
    M2MOwnerModel,
    M2MTagModel,
    TestModel,
)

from django_async_backend.db import async_connections
from django_async_backend.db.models.manager import AsyncManager
from django_async_backend.test import (
    AsyncCaptureQueriesContext,
    AsyncioTestCase,
)


class TestReverseForeignKeyRelatedManager(AsyncioTestCase):
    async def asyncSetUp(self):
        self.parent = await TestModel.async_objects.acreate(name="parent")
        self.child1 = await TestModel.async_objects.acreate(
            name="child1", relative=self.parent
        )
        self.child2 = await TestModel.async_objects.acreate(
            name="child2", relative=self.parent
        )
        await TestModel.async_objects.acreate(name="unrelated")

    async def test_manager_is_async(self):
        manager = self.parent.relatives(manager="async_objects")

        self.assertIsInstance(manager, AsyncManager)

    async def test_iteration(self):
        manager = self.parent.relatives(manager="async_objects")

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            names = sorted([obj.name async for obj in manager.all()])

        self.assertEqual(names, ["child1", "child2"])
        self.assertEqual(len(ctx.captured_queries), 1)

    async def test_terminal_methods(self):
        manager = self.parent.relatives(manager="async_objects")

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            self.assertEqual(await manager.acount(), 2)
            self.assertTrue(await manager.aexists())
            first = await manager.order_by("name").afirst()
            child2 = await manager.aget(name="child2")
            filtered = await manager.filter(name="child1").acount()
            names = [
                name
                async for name in manager.order_by("name").values_list(
                    "name", flat=True
                )
            ]

        self.assertEqual(first.pk, self.child1.pk)
        self.assertEqual(child2.pk, self.child2.pk)
        self.assertEqual(filtered, 1)
        self.assertEqual(names, ["child1", "child2"])
        self.assertEqual(len(ctx.captured_queries), 6)

    async def test_related_object_is_cached_on_results(self):
        manager = self.parent.relatives(manager="async_objects")
        children = [obj async for obj in manager.filter(name="child1")]
        self.assertEqual(len(children), 1)

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            self.assertIs(children[0].relative, self.parent)

        self.assertEqual(len(ctx.captured_queries), 0)

    async def test_uncached_forward_access_raises(self):
        child = await TestModel.async_objects.aget(name="child1")

        with self.assertRaises(SynchronousOnlyOperation):
            child.relative


class TestManyToManyRelatedManager(AsyncioTestCase):
    async def asyncSetUp(self):
        self.through = M2MOwnerModel.tags.through
        self.owner = await M2MOwnerModel.async_objects.acreate(name="owner")
        self.other = await M2MOwnerModel.async_objects.acreate(name="other")
        await M2MOwnerModel.async_objects.acreate(name="unlinked")
        self.tag_a = await M2MTagModel.async_objects.acreate(name="a")
        self.tag_b = await M2MTagModel.async_objects.acreate(name="b")
        await M2MTagModel.async_objects.acreate(name="unlinked")
        await self.through.async_objects.abulk_create(
            [
                self.through(m2mownermodel=self.owner, m2mtagmodel=self.tag_a),
                self.through(m2mownermodel=self.owner, m2mtagmodel=self.tag_b),
                self.through(m2mownermodel=self.other, m2mtagmodel=self.tag_b),
            ]
        )

    async def test_manager_is_async(self):
        self.assertIsInstance(
            self.owner.tags(manager="async_objects"), AsyncManager
        )
        self.assertIsInstance(
            self.tag_b.owners(manager="async_objects"), AsyncManager
        )

    async def test_forward_reads(self):
        manager = self.owner.tags(manager="async_objects")

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            names = sorted([tag.name async for tag in manager.all()])
            count = await manager.acount()
            exists = await manager.aexists()
            tag_a = await manager.aget(name="a")
            filtered = await manager.filter(name="b").acount()

        self.assertEqual(names, ["a", "b"])
        self.assertEqual(count, 2)
        self.assertTrue(exists)
        self.assertEqual(tag_a.pk, self.tag_a.pk)
        self.assertEqual(filtered, 1)
        self.assertEqual(len(ctx.captured_queries), 5)

    async def test_reverse_reads(self):
        manager = self.tag_b.owners(manager="async_objects")

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            names = sorted([obj.name async for obj in manager.all()])
            count = await manager.acount()

        self.assertEqual(names, ["other", "owner"])
        self.assertEqual(count, 2)
        self.assertEqual(len(ctx.captured_queries), 2)

    async def test_filter_reuses_the_relation_join(self):
        manager = self.owner.tags(manager="async_objects")

        self.assertEqual(
            await manager.filter(owners__name="other").acount(),
            0,
            "owners__name must filter the relation's own join",
        )

    async def test_write_through_the_through_model(self):
        tag_c = await M2MTagModel.async_objects.acreate(name="c")
        manager = self.owner.tags(manager="async_objects")

        async with AsyncCaptureQueriesContext(
            async_connections[DEFAULT_DB_ALIAS]
        ) as ctx:
            await self.through.async_objects.acreate(
                m2mownermodel=self.owner, m2mtagmodel=tag_c
            )
            await self.through.async_objects.filter(
                m2mownermodel=self.owner, m2mtagmodel=self.tag_a
            ).adelete()

        self.assertEqual(len(ctx.captured_queries), 2)
        self.assertEqual(
            sorted([tag.name async for tag in manager.all()]), ["b", "c"]
        )
        self.assertEqual(
            await self.tag_b.owners(manager="async_objects").acount(),
            2,
            "removing owner/a must not touch other/b",
        )

    async def test_through_model_writes_do_not_send_m2m_changed(self):
        actions = []

        def receiver(action, **kwargs):
            actions.append(action)

        m2m_changed.connect(receiver, sender=self.through)
        self.addCleanup(m2m_changed.disconnect, receiver, sender=self.through)

        await self.through.async_objects.acreate(
            m2mownermodel=self.other, m2mtagmodel=self.tag_a
        )
        await self.through.async_objects.filter(
            m2mownermodel=self.other
        ).adelete()

        self.assertEqual(actions, [])


class TestExplicitThroughModel(AsyncioTestCase):
    async def asyncSetUp(self):
        self.group = await M2MGroupModel.async_objects.acreate(name="group")
        self.alice = await M2MMemberModel.async_objects.acreate(name="alice")
        self.bob = await M2MMemberModel.async_objects.acreate(name="bob")

    async def test_add_and_remove(self):
        await M2MMembershipModel(
            group=self.group, member=self.alice, role="admin"
        ).async_save()
        await M2MMembershipModel(
            group=self.group, member=self.bob
        ).async_save()

        members = self.group.members(manager="async_objects")
        self.assertEqual(
            sorted([m.name async for m in members.all()]), ["alice", "bob"]
        )
        self.assertEqual(
            await self.alice.groups(manager="async_objects").acount(), 1
        )

        await M2MMembershipModel.async_objects.filter(
            group=self.group, member=self.alice
        ).adelete()

        self.assertEqual([m.name async for m in members.all()], ["bob"])
