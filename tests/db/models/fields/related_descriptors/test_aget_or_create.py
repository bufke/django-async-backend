from test_app.models import (
    M2MGroupModel,
    M2MOwnerModel,
)

from django_async_backend.test import AsyncioTestCase

from .utils import names


class TestAGetOrCreate(AsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = await M2MOwnerModel.async_objects.acreate(name="owner")
        self.tags = self.owner.tags(manager="async_objects")

    async def test_creates_and_adds_when_missing(self):
        tag, created = await self.tags.aget_or_create(name="django")

        self.assertTrue(created)
        self.assertEqual(await names(self.tags), ["django"])

    async def test_returns_related_object(self):
        existing = await self.tags.acreate(name="django")

        tag, created = await self.tags.aget_or_create(name="django")

        self.assertFalse(created)
        self.assertEqual(tag.pk, existing.pk)
        self.assertEqual(
            await M2MOwnerModel.tags.through.async_objects.acount(), 1
        )

    async def test_uses_through_defaults_when_created(self):
        group = await M2MGroupModel.async_objects.acreate(name="group")

        member, created = await group.members(
            manager="async_objects"
        ).aget_or_create(name="alice", through_defaults={"role": "admin"})

        self.assertTrue(created)
        membership = await M2MGroupModel.members.through.async_objects.aget(
            member=member
        )
        self.assertEqual(membership.role, "admin")
