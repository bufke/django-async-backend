from test_app.models import (
    M2MMemberModel,
    M2MOwnerModel,
    M2MTagModel,
)

from django_async_backend.test import AsyncioTestCase

from .utils import (
    RecordM2MChanged,
    names,
)


class TestAClear(RecordM2MChanged, AsyncioTestCase):
    m2m_sender = M2MOwnerModel.tags.through

    async def asyncSetUp(self):
        self.owner = await M2MOwnerModel.async_objects.acreate(name="owner")
        self.other = await M2MOwnerModel.async_objects.acreate(name="other")
        self.django = await M2MTagModel.async_objects.acreate(name="django")
        self.python = await M2MTagModel.async_objects.acreate(name="python")
        self.tags = self.owner.tags(manager="async_objects")
        await self.tags.aadd(self.django, self.python)
        await self.other.tags(manager="async_objects").aadd(self.django)
        self.sent.clear()

    async def test_clears_relations(self):
        await self.tags.aclear()

        self.assertEqual(await names(self.tags), [])

    async def test_leaves_other_instances_relations(self):
        await self.tags.aclear()

        self.assertEqual(
            await names(self.other.tags(manager="async_objects")), ["django"]
        )

    async def test_keeps_related_objects(self):
        await self.tags.aclear()

        self.assertEqual(
            await names(M2MTagModel.async_objects), ["django", "python"]
        )

    async def test_clears_from_the_reverse_side(self):
        await self.django.owners(manager="async_objects").aclear()

        self.assertEqual(await names(self.tags), ["python"])
        self.assertEqual(
            await names(self.other.tags(manager="async_objects")), []
        )

    async def test_sends_pre_and_post_clear(self):
        await self.tags.aclear()

        self.assertEqual(
            self.sent,
            [
                ("pre_clear", "owner", False, None),
                ("post_clear", "owner", False, None),
            ],
        )


class TestAClearSymmetrical(AsyncioTestCase):
    async def test_clears_both_directions(self):
        alice = await M2MMemberModel.async_objects.acreate(name="alice")
        bob = await M2MMemberModel.async_objects.acreate(name="bob")
        await alice.friends(manager="async_objects").aadd(bob)

        await alice.friends(manager="async_objects").aclear()

        self.assertEqual(await names(bob.friends(manager="async_objects")), [])
