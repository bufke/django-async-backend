from test_app.models import (
    M2MGroupModel,
    M2MMemberModel,
    M2MOwnerModel,
    M2MTagModel,
)

from django_async_backend.test import AsyncioTestCase

from .utils import (
    RecordM2MChanged,
    names,
)


class TestASet(RecordM2MChanged, AsyncioTestCase):
    m2m_sender = M2MOwnerModel.tags.through

    async def asyncSetUp(self):
        self.owner = await M2MOwnerModel.async_objects.acreate(name="owner")
        self.django = await M2MTagModel.async_objects.acreate(name="django")
        self.python = await M2MTagModel.async_objects.acreate(name="python")
        self.rust = await M2MTagModel.async_objects.acreate(name="rust")
        self.tags = self.owner.tags(manager="async_objects")
        await self.tags.aadd(self.django, self.python)
        self.sent.clear()

    async def through_pks(self):
        through = M2MOwnerModel.tags.through
        return {
            row.m2mtagmodel_id: row.pk
            async for row in through.async_objects.filter(
                m2mownermodel=self.owner
            )
        }

    async def test_replaces_relations(self):
        await self.tags.aset([self.python, self.rust])

        self.assertEqual(await names(self.tags), ["python", "rust"])

    async def test_keeps_rows_that_stay(self):
        before = await self.through_pks()

        await self.tags.aset([self.python, self.rust])

        after = await self.through_pks()
        self.assertEqual(after[self.python.pk], before[self.python.pk])

    async def test_clear_recreates_rows(self):
        before = await self.through_pks()

        await self.tags.aset([self.python, self.rust], clear=True)

        after = await self.through_pks()
        self.assertEqual(await names(self.tags), ["python", "rust"])
        self.assertNotEqual(after[self.python.pk], before[self.python.pk])

    async def test_accepts_primary_keys(self):
        await self.tags.aset([self.rust.pk])

        self.assertEqual(await names(self.tags), ["rust"])

    async def test_accepts_async_queryset(self):
        await self.tags.aset(M2MTagModel.async_objects.filter(name="rust"))

        self.assertEqual(await names(self.tags), ["rust"])

    async def test_queryset_is_evaluated_before_clearing(self):
        await self.tags.aset(self.tags.all(), clear=True)

        self.assertEqual(await names(self.tags), ["django", "python"])

    async def test_empty_removes_everything(self):
        await self.tags.aset([])

        self.assertEqual(await names(self.tags), [])

    async def test_sends_remove_then_add_for_the_difference(self):
        await self.tags.aset([self.python, self.rust])

        self.assertEqual(
            self.sent,
            [
                ("pre_remove", "owner", False, [self.django.pk]),
                ("post_remove", "owner", False, [self.django.pk]),
                ("pre_add", "owner", False, [self.rust.pk]),
                ("post_add", "owner", False, [self.rust.pk]),
            ],
        )


class TestASetThroughDefaults(AsyncioTestCase):
    async def test_applies_to_new_rows_only(self):
        group = await M2MGroupModel.async_objects.acreate(name="group")
        alice = await M2MMemberModel.async_objects.acreate(name="alice")
        bob = await M2MMemberModel.async_objects.acreate(name="bob")
        members = group.members(manager="async_objects")
        await members.aadd(alice)

        await members.aset([alice, bob], through_defaults={"role": "admin"})

        through = M2MGroupModel.members.through
        roles = {
            member: role
            async for member, role in through.async_objects.values_list(
                "member__name", "role"
            )
        }
        self.assertEqual(roles, {"alice": "member", "bob": "admin"})
