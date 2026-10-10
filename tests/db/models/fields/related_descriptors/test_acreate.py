from test_app.models import (
    M2MGroupModel,
    M2MOwnerModel,
    M2MTagModel,
)

from django_async_backend.test import AsyncioTestCase

from .utils import names


class TestACreate(AsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = await M2MOwnerModel.async_objects.acreate(name="owner")
        self.tags = self.owner.tags(manager="async_objects")

    async def test_creates_and_adds(self):
        tag = await self.tags.acreate(name="django")

        self.assertIsNotNone(tag.pk)
        self.assertEqual(await names(self.tags), ["django"])

    async def test_creates_from_the_reverse_side(self):
        tag = await M2MTagModel.async_objects.acreate(name="django")

        owner = await tag.owners(manager="async_objects").acreate(name="new")

        self.assertEqual(
            await names(owner.tags(manager="async_objects")), ["django"]
        )

    async def test_uses_through_defaults(self):
        group = await M2MGroupModel.async_objects.acreate(name="group")

        member = await group.members(manager="async_objects").acreate(
            name="alice", through_defaults={"role": "admin"}
        )

        membership = await M2MGroupModel.members.through.async_objects.aget(
            member=member
        )
        self.assertEqual(membership.role, "admin")
