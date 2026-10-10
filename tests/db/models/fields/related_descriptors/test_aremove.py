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


class TestARemove(RecordM2MChanged, AsyncioTestCase):
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

    async def test_removes_instances(self):
        await self.tags.aremove(self.django)

        self.assertEqual(await names(self.tags), ["python"])

    async def test_removes_primary_keys(self):
        await self.tags.aremove(self.django.pk, self.python.pk)

        self.assertEqual(await names(self.tags), [])

    async def test_leaves_other_instances_relations(self):
        await self.tags.aremove(self.django)

        self.assertEqual(
            await names(self.other.tags(manager="async_objects")), ["django"]
        )

    async def test_removes_from_the_reverse_side(self):
        await self.django.owners(manager="async_objects").aremove(self.owner)

        self.assertEqual(await names(self.tags), ["python"])

    async def test_unrelated_object_is_ignored(self):
        rust = await M2MTagModel.async_objects.acreate(name="rust")

        await self.tags.aremove(rust)

        self.assertEqual(await names(self.tags), ["django", "python"])

    async def test_sends_pre_and_post_remove(self):
        await self.tags.aremove(self.django)

        self.assertEqual(
            self.sent,
            [
                ("pre_remove", "owner", False, [self.django.pk]),
                ("post_remove", "owner", False, [self.django.pk]),
            ],
        )


class TestARemoveSymmetrical(AsyncioTestCase):
    async def test_removes_both_directions(self):
        alice = await M2MMemberModel.async_objects.acreate(name="alice")
        bob = await M2MMemberModel.async_objects.acreate(name="bob")
        await alice.friends(manager="async_objects").aadd(bob)

        await bob.friends(manager="async_objects").aremove(alice)

        self.assertEqual(
            await M2MMemberModel.friends.through.async_objects.acount(), 0
        )
