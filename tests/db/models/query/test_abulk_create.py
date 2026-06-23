from django.db import DEFAULT_DB_ALIAS, IntegrityError
from test_app.models import (
    ChildModel,
    OrderedItemModel,
    TestModel,
)

from django_async_backend.db import async_connections
from django_async_backend.test import (
    AsyncioTestCase,
    AsyncioTransactionTestCase,
)


class TestABulkCreate(AsyncioTestCase):
    async def test_creates_rows(self):
        objs = await TestModel.async_object.abulk_create(
            [
                TestModel(name="Item1", value=1),
                TestModel(name="Item2", value=2),
                TestModel(name="Item3", value=3),
            ]
        )
        self.assertEqual(len(objs), 3)
        count = await TestModel.async_object.acount()
        self.assertEqual(count, 3)
        values = sorted(
            [obj.value async for obj in TestModel.async_object.all()]
        )
        self.assertEqual(values, [1, 2, 3])

    async def test_sets_primary_keys(self):
        # PostgreSQL returns the inserted ids via RETURNING, so the pk of each
        # object should be populated after abulk_create.
        objs = await TestModel.async_object.abulk_create(
            [
                TestModel(name="Item1", value=1),
                TestModel(name="Item2", value=2),
            ]
        )
        for obj in objs:
            self.assertIsNotNone(obj.pk)
            self.assertFalse(obj._state.adding)
        fetched = await TestModel.async_object.aget(pk=objs[0].pk)
        self.assertEqual(fetched.name, "Item1")

    async def test_empty_returns_input(self):
        objs = await TestModel.async_object.abulk_create([])
        self.assertEqual(objs, [])
        count = await TestModel.async_object.acount()
        self.assertEqual(count, 0)

    async def test_batch_size_wraps_in_transaction(self):
        # More than one batch forces an atomic() block around the inserts.
        objs = await TestModel.async_object.abulk_create(
            [TestModel(name=f"Item{i}", value=i) for i in range(5)],
            batch_size=2,
        )
        self.assertEqual(len(objs), 5)
        count = await TestModel.async_object.acount()
        self.assertEqual(count, 5)
        # Every object gets a distinct primary key back, even though the
        # inserts were split across batches.
        self.assertTrue(all(obj.pk is not None for obj in objs))
        self.assertEqual(len({obj.pk for obj in objs}), 5)

    async def test_creates_rows_with_explicit_pk(self):
        # Objects that already carry a primary key take the objs_with_pk
        # branch (separate from the autoincrement objs_without_pk path).
        # Use ids well above the autoincrement sequence (which is shared
        # across tests and not rolled back) so they never collide with
        # sequence-assigned ids.
        objs = await TestModel.async_object.abulk_create(
            [
                TestModel(id=1_000_001, name="Item1", value=1),
                TestModel(id=1_000_002, name="Item2", value=2),
            ]
        )
        self.assertEqual(len(objs), 2)
        for obj in objs:
            self.assertFalse(obj._state.adding)
            self.assertEqual(obj._state.db, "default")
        fetched = await TestModel.async_object.aget(pk=1_000_001)
        self.assertEqual(fetched.name, "Item1")
        count = await TestModel.async_object.acount()
        self.assertEqual(count, 2)

    async def test_mixed_pk_batches_nested_atomic(self):
        # Both objs_with_pk and objs_without_pk present makes abulk_create
        # wrap the whole operation in an outer atomic; a small batch_size
        # then makes _batched_insert open its own (nested) atomic per list.
        objs = await TestModel.async_object.abulk_create(
            [
                TestModel(id=1_000_011, name="WithPk1", value=1),
                TestModel(id=1_000_012, name="WithPk2", value=2),
                TestModel(id=1_000_013, name="WithPk3", value=3),
                TestModel(name="NoPk1", value=4),
                TestModel(name="NoPk2", value=5),
                TestModel(name="NoPk3", value=6),
            ],
            batch_size=2,
        )
        self.assertEqual(len(objs), 6)
        count = await TestModel.async_object.acount()
        self.assertEqual(count, 6)
        with_pk = await TestModel.async_object.filter(
            pk__in=[1_000_011, 1_000_012, 1_000_013]
        ).acount()
        self.assertEqual(with_pk, 3)
        for obj in objs:
            if obj.name.startswith("NoPk"):
                self.assertIsNotNone(obj.pk)

    async def test_order_with_respect_to_raises(self):
        # _handle_order_with_respect_to would run a sync ORM query in the
        # event loop, so abulk_create rejects such models up front.
        with self.assertRaises(NotImplementedError):
            await OrderedItemModel.async_object.abulk_create(
                [OrderedItemModel()]
            )

    async def test_invalid_batch_size_raises(self):
        with self.assertRaises(ValueError):
            await TestModel.async_object.abulk_create(
                [TestModel(name="Item1", value=1)], batch_size=0
            )

    async def test_multi_table_inherited_raises(self):
        with self.assertRaises(ValueError):
            await ChildModel.async_object.abulk_create(
                [ChildModel(parent_value=1, child_value=2)]
            )

    async def test_conflict_raises_without_ignore(self):
        await TestModel.async_object.abulk_create(
            [TestModel(name="Item1", value=1)]
        )
        with self.assertRaises(IntegrityError):
            await TestModel.async_object.abulk_create(
                [TestModel(name="Item1", value=99)]
            )

    async def test_ignore_conflicts(self):
        await TestModel.async_object.abulk_create(
            [TestModel(name="Item1", value=1)]
        )
        await TestModel.async_object.abulk_create(
            [
                TestModel(name="Item1", value=99),
                TestModel(name="Item2", value=2),
            ],
            ignore_conflicts=True,
        )
        item1 = await TestModel.async_object.aget(name="Item1")
        self.assertEqual(item1.value, 1)
        count = await TestModel.async_object.acount()
        self.assertEqual(count, 2)

    async def test_update_conflicts(self):
        await TestModel.async_object.abulk_create(
            [TestModel(name="Item1", value=1)]
        )
        await TestModel.async_object.abulk_create(
            [TestModel(name="Item1", value=99)],
            update_conflicts=True,
            update_fields=["value"],
            unique_fields=["name"],
        )
        item1 = await TestModel.async_object.aget(name="Item1")
        self.assertEqual(item1.value, 99)
        count = await TestModel.async_object.acount()
        self.assertEqual(count, 1)

    async def test_update_conflicts_pk_alias(self):
        # "pk" in unique_fields is mapped to the real primary-key field name
        # (opts.pk.name); the other update_conflicts test only covers "name".
        created = await TestModel.async_object.abulk_create(
            [TestModel(name="Item1", value=1)]
        )
        pk = created[0].pk
        await TestModel.async_object.abulk_create(
            [TestModel(pk=pk, name="Item1", value=123)],
            update_conflicts=True,
            update_fields=["value"],
            unique_fields=["pk"],
        )
        item1 = await TestModel.async_object.aget(pk=pk)
        self.assertEqual(item1.value, 123)
        count = await TestModel.async_object.acount()
        self.assertEqual(count, 1)


class TestABulkCreateRollback(AsyncioTransactionTestCase):
    # AsyncioTransactionTestCase commits for real (it does not wrap the test
    # in a rolled-back transaction), so the atomic block _batched_insert opens
    # is the outermost transaction and its rollback is observable.

    async def asyncTearDown(self):
        async with await async_connections[
            DEFAULT_DB_ALIAS
        ].cursor() as cursor:
            await cursor.execute("DELETE FROM test_model;")

    async def test_failed_batch_rolls_back_earlier_batches(self):
        # batch_size=1 over 3 objects forces _batched_insert to wrap the
        # inserts in an atomic block. The third object duplicates the first
        # object's unique name, so its insert raises -- the atomic must then
        # roll back the first two already-inserted rows.
        with self.assertRaises(IntegrityError):
            await TestModel.async_object.abulk_create(
                [
                    TestModel(name="rollback-A", value=1),
                    TestModel(name="rollback-B", value=2),
                    TestModel(name="rollback-A", value=3),
                ],
                batch_size=1,
            )
        count = await TestModel.async_object.acount()
        self.assertEqual(count, 0)
