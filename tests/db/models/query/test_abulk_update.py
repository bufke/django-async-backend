from django.db.models import F
from test_app.models import TestModel

from django_async_backend.test import AsyncioTestCase


class TestABulkUpdate(AsyncioTestCase):
    async def asyncSetUp(self):
        self.item1 = await TestModel.async_object.acreate(
            name="Item1", value=1
        )
        self.item2 = await TestModel.async_object.acreate(
            name="Item2", value=2
        )
        self.item3 = await TestModel.async_object.acreate(
            name="Item3", value=3
        )

    async def _values_by_name(self):
        return {
            obj.name: obj.value async for obj in TestModel.async_object.all()
        }

    async def test_returns_row_count(self):
        self.item1.value = 10
        self.item2.value = 20
        self.item3.value = 30
        rows = await TestModel.async_object.all().abulk_update(
            [self.item1, self.item2, self.item3], ["value"]
        )
        self.assertEqual(rows, 3)
        self.assertEqual(
            await self._values_by_name(),
            {"Item1": 10, "Item2": 20, "Item3": 30},
        )

    async def test_multiple_fields(self):
        self.item1.name = "New1"
        self.item1.value = 100
        self.item2.name = "New2"
        self.item2.value = 200
        rows = await TestModel.async_object.all().abulk_update(
            [self.item1, self.item2], ["name", "value"]
        )
        self.assertEqual(rows, 2)
        self.assertEqual(
            await self._values_by_name(),
            {"New1": 100, "New2": 200, "Item3": 3},
        )

    async def test_batch_size_forces_multiple_batches(self):
        # batch_size=1 produces one UPDATE (one awaited aupdate) per object,
        # exercising the per-batch loop inside `async with async_atomic`.
        self.item1.value = 7
        self.item2.value = 7
        self.item3.value = 7
        rows = await TestModel.async_object.all().abulk_update(
            [self.item1, self.item2, self.item3], ["value"], batch_size=1
        )
        self.assertEqual(rows, 3)
        self.assertEqual(
            await self._values_by_name(),
            {"Item1": 7, "Item2": 7, "Item3": 7},
        )

    async def test_f_expression_values(self):
        # An attribute set to a resolvable expression (F) must pass through
        # unwrapped into the CASE/WHEN rather than being treated as a literal.
        self.item1.value = F("value") + 100
        self.item2.value = F("value") + 100
        self.item3.value = F("value") + 100
        rows = await TestModel.async_object.all().abulk_update(
            [self.item1, self.item2, self.item3], ["value"]
        )
        self.assertEqual(rows, 3)
        self.assertEqual(
            await self._values_by_name(),
            {"Item1": 101, "Item2": 102, "Item3": 103},
        )

    async def test_uneven_batches(self):
        # batch_size=2 over 3 objects exercises the uneven final batch.
        self.item1.value = 9
        self.item2.value = 9
        self.item3.value = 9
        rows = await TestModel.async_object.all().abulk_update(
            [self.item1, self.item2, self.item3], ["value"], batch_size=2
        )
        self.assertEqual(rows, 3)
        self.assertEqual(
            await self._values_by_name(),
            {"Item1": 9, "Item2": 9, "Item3": 9},
        )

    async def test_empty_objs_returns_zero(self):
        rows = await TestModel.async_object.all().abulk_update([], ["value"])
        self.assertEqual(rows, 0)
        self.assertEqual(
            await self._values_by_name(),
            {"Item1": 1, "Item2": 2, "Item3": 3},
        )

    async def test_manager_level_exposure(self):
        # abulk_update is surfaced on the manager via from_queryset, not only
        # on the queryset returned by .all().
        self.item1.value = 50
        rows = await TestModel.async_object.abulk_update(
            [self.item1], ["value"]
        )
        self.assertEqual(rows, 1)
        item = await TestModel.async_object.aget(name="Item1")
        self.assertEqual(item.value, 50)


class TestABulkUpdateValidation(AsyncioTestCase):
    async def asyncSetUp(self):
        self.item1 = await TestModel.async_object.acreate(
            name="Item1", value=1
        )

    async def test_no_fields_raises(self):
        with self.assertRaises(ValueError):
            await TestModel.async_object.all().abulk_update([self.item1], [])

    async def test_non_positive_batch_size_raises(self):
        with self.assertRaises(ValueError):
            await TestModel.async_object.all().abulk_update(
                [self.item1], ["value"], batch_size=0
            )

    async def test_object_without_pk_raises(self):
        unsaved = TestModel(name="Unsaved", value=1)
        with self.assertRaises(ValueError):
            await TestModel.async_object.all().abulk_update(
                [unsaved], ["value"]
            )

    async def test_pk_field_raises(self):
        with self.assertRaises(ValueError):
            await TestModel.async_object.all().abulk_update(
                [self.item1], ["id"]
            )
