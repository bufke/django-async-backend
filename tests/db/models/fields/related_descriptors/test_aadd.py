from django.db.models import signals
from test_app.models import (
    M2MGroupModel,
    M2MMemberModel,
    M2MOwnerModel,
    M2MTagModel,
)

from django_async_backend.db import async_connections
from django_async_backend.test import (
    AsyncCaptureQueriesContext,
    AsyncioTestCase,
)

from .utils import (
    RecordM2MChanged,
    names,
)


class TestAAdd(AsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = await M2MOwnerModel.async_objects.acreate(name="owner")
        self.django = await M2MTagModel.async_objects.acreate(name="django")
        self.python = await M2MTagModel.async_objects.acreate(name="python")
        self.tags = self.owner.tags(manager="async_objects")

    async def test_adds_instances(self):
        await self.tags.aadd(self.django, self.python)

        self.assertEqual(await names(self.tags), ["django", "python"])

    async def test_adds_primary_keys(self):
        await self.tags.aadd(self.django.pk)

        self.assertEqual(await names(self.tags), ["django"])

    async def test_adds_from_the_reverse_side(self):
        await self.django.owners(manager="async_objects").aadd(self.owner)

        self.assertEqual(await names(self.tags), ["django"])

    async def test_existing_relation_is_not_duplicated(self):
        await self.tags.aadd(self.django)
        await self.tags.aadd(self.django, self.python)

        self.assertEqual(
            await M2MOwnerModel.tags.through.async_objects.acount(), 2
        )

    async def test_inserts_in_one_query(self):
        async with AsyncCaptureQueriesContext(
            async_connections["default"]
        ) as ctx:
            await self.tags.aadd(self.django, self.python)

        self.assertEqual(len(ctx), 1, ctx.captured_queries)
        self.assertIn("ON CONFLICT DO NOTHING", ctx.captured_queries[0]["sql"])

    async def test_nothing_to_add_runs_no_query(self):
        async with AsyncCaptureQueriesContext(
            async_connections["default"]
        ) as ctx:
            await self.tags.aadd()

        self.assertEqual(len(ctx), 0)

    async def test_instance_of_another_model_raises(self):
        with self.assertRaisesRegex(
            TypeError, "'M2MTagModel' instance expected"
        ):
            await self.tags.aadd(self.owner)

    async def test_unsaved_instance_raises(self):
        with self.assertRaisesRegex(ValueError, "Cannot add"):
            await self.tags.aadd(M2MTagModel(name="unsaved"))


class TestAAddSymmetrical(AsyncioTestCase):
    async def test_adds_both_directions(self):
        alice = await M2MMemberModel.async_objects.acreate(name="alice")
        bob = await M2MMemberModel.async_objects.acreate(name="bob")

        await alice.friends(manager="async_objects").aadd(bob)

        self.assertEqual(
            await names(alice.friends(manager="async_objects")), ["bob"]
        )
        self.assertEqual(
            await names(bob.friends(manager="async_objects")), ["alice"]
        )


class TestAAddThroughDefaults(AsyncioTestCase):
    async def asyncSetUp(self):
        self.group = await M2MGroupModel.async_objects.acreate(name="group")
        self.alice = await M2MMemberModel.async_objects.acreate(name="alice")
        self.members = self.group.members(manager="async_objects")

    async def roles(self):
        through = M2MGroupModel.members.through
        return [
            role
            async for role in through.async_objects.values_list(
                "role", flat=True
            )
        ]

    async def test_uses_field_default(self):
        await self.members.aadd(self.alice)

        self.assertEqual(await self.roles(), ["member"])

    async def test_uses_through_defaults(self):
        await self.members.aadd(self.alice, through_defaults={"role": "admin"})

        self.assertEqual(await self.roles(), ["admin"])

    async def test_resolves_callable_through_defaults(self):
        await self.members.aadd(
            self.alice, through_defaults={"role": lambda: "owner"}
        )

        self.assertEqual(await self.roles(), ["owner"])


class TestAAddSignals(RecordM2MChanged, AsyncioTestCase):
    m2m_sender = M2MOwnerModel.tags.through

    async def asyncSetUp(self):
        self.owner = await M2MOwnerModel.async_objects.acreate(name="owner")
        self.django = await M2MTagModel.async_objects.acreate(name="django")
        self.python = await M2MTagModel.async_objects.acreate(name="python")
        self.tags = self.owner.tags(manager="async_objects")

    async def test_sends_pre_and_post_add_with_new_pks_only(self):
        await self.tags.aadd(self.django)
        self.sent.clear()

        await self.tags.aadd(self.django, self.python)

        self.assertEqual(
            self.sent,
            [
                ("pre_add", "owner", False, [self.python.pk]),
                ("post_add", "owner", False, [self.python.pk]),
            ],
        )

    async def test_reverse_side_sends_reverse(self):
        await self.django.owners(manager="async_objects").aadd(self.owner)

        self.assertEqual(
            self.sent,
            [
                ("pre_add", "django", True, [self.owner.pk]),
                ("post_add", "django", True, [self.owner.pk]),
            ],
        )

    async def test_async_receiver_is_awaited(self):
        received = []

        async def receiver(action, **kwargs):
            received.append(action)

        signals.m2m_changed.connect(receiver, sender=self.m2m_sender)
        self.addCleanup(
            signals.m2m_changed.disconnect, receiver, sender=self.m2m_sender
        )

        await self.tags.aadd(self.django)

        self.assertEqual(received, ["pre_add", "post_add"])
