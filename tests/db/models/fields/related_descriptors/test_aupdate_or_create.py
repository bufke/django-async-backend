from test_app.models import (
    M2MGroupModel,
    M2MMemberModel,
)

from django_async_backend.test import AsyncioTestCase

from .utils import names


class TestAUpdateOrCreate(AsyncioTestCase):
    async def asyncSetUp(self):
        self.group = await M2MGroupModel.async_objects.acreate(name="group")
        self.members = self.group.members(manager="async_objects")

    async def test_creates_and_adds_when_missing(self):
        member, created = await self.members.aupdate_or_create(name="alice")

        self.assertTrue(created)
        self.assertEqual(await names(self.members), ["alice"])

    async def test_updates_related_object(self):
        existing = await self.members.acreate(name="alice")

        member, created = await self.members.aupdate_or_create(
            pk=existing.pk, defaults={"name": "alicia"}
        )

        self.assertFalse(created)
        self.assertEqual(
            (await M2MMemberModel.async_objects.aget(pk=existing.pk)).name,
            "alicia",
        )
        self.assertEqual(await self.members.acount(), 1)
