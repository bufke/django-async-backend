# This file was generated automatically. Do not modify it manually. (based on django 6.1)
from django.db.models.fields import (
    related_descriptors as django_related_descriptors,
)

from django_async_backend.db import async_connections
from django_async_backend.db.models.query import QuerySet
from django_async_backend.db.transaction import async_atomic

"""
Accessors for related objects.

When a field defines a relation between two models, each model class provides
an attribute to access related instances of the other model class (unless the
reverse accessor has been disabled with related_name='+').

Accessors are implemented as descriptors in order to customize access and
assignment. This module defines the descriptor classes.

Forward accessors follow foreign keys. Reverse accessors trace them back. For
example, with the following models::

    class Parent(Model):
        pass

    class Child(Model):
        parent = ForeignKey(Parent, related_name='children')

 ``child.parent`` is a forward many-to-one relation. ``parent.children`` is a
reverse many-to-one relation.

There are three types of relations (many-to-one, one-to-one, and many-to-many)
and two directions (forward and reverse) for a total of six combinations.

1. Related instance on the forward side of a many-to-one relation:
   ``ForwardManyToOneDescriptor``.

   Uniqueness of foreign key values is irrelevant to accessing the related
   instance, making the many-to-one and one-to-one cases identical as far as
   the descriptor is concerned. The constraint is checked upstream (unicity
   validation in forms) or downstream (unique indexes in the database).

2. Related instance on the forward side of a one-to-one
   relation: ``ForwardOneToOneDescriptor``.

   It avoids querying the database when accessing the parent link field in
   a multi-table inheritance scenario.

3. Related instance on the reverse side of a one-to-one relation:
   ``ReverseOneToOneDescriptor``.

   One-to-one relations are asymmetrical, despite the apparent symmetry of the
   name, because they're implemented in the database with a foreign key from
   one table to another. As a consequence ``ReverseOneToOneDescriptor`` is
   slightly different from ``ForwardManyToOneDescriptor``.

4. Related objects manager for related instances on the reverse side of a
   many-to-one relation: ``ReverseManyToOneDescriptor``.

   Unlike the previous two classes, this one provides access to a collection
   of objects. It returns a manager rather than an instance.

5. Related objects manager for related instances on the forward or reverse
   sides of a many-to-many relation: ``ManyToManyDescriptor``.

   Many-to-many relations are symmetrical. The syntax of Django models
   requires declaring them on one side but that's an implementation detail.
   They could be declared on the other side without any change in behavior.
   Therefore the forward and reverse descriptors can be the same.

   If you're looking for ``ForwardManyToManyDescriptor`` or
   ``ReverseManyToManyDescriptor``, use ``ManyToManyDescriptor`` instead.
"""

from django.core.exceptions import FieldError
from django.db import (
    DEFAULT_DB_ALIAS,
    NotSupportedError,
    router,
)
from django.db.models import (
    Manager,
    Q,
    Window,
    signals,
)
from django.db.models.expressions import ColPairs
from django.db.models.fields.tuple_lookups import TupleIn
from django.db.models.functions import RowNumber
from django.db.models.lookups import (
    GreaterThan,
    LessThanOrEqual,
)
from django.db.models.query import prefetch_related_objects
from django.db.models.query_utils import DeferredAttribute
from django.db.models.utils import (
    AltersData,
    resolve_callables,
)
from django.utils.functional import cached_property


def _traverse_ancestors(model, starting_instance):
    current_instance = starting_instance
    while current_instance is not None:
        ancestor_link = current_instance._meta.get_ancestor_link(model)
        if not ancestor_link:
            yield current_instance, None
            break
        ancestor = ancestor_link.get_cached_value(current_instance, None)
        yield current_instance, ancestor
        current_instance = ancestor


def create_forward_many_to_many_manager(superclass, rel, reverse):

    class ManyRelatedManager(superclass, AltersData):
        def __init__(self, instance=None):
            super().__init__()

            self.instance = instance

            if not reverse:
                self.model = rel.model
                self.query_field_name = rel.field.related_query_name()
                self.prefetch_cache_name = rel.field.name
                self.source_field_name = rel.field.m2m_field_name()
                self.target_field_name = rel.field.m2m_reverse_field_name()
                self.symmetrical = rel.symmetrical
            else:
                self.model = rel.related_model
                self.query_field_name = rel.field.name
                self.prefetch_cache_name = rel.field.related_query_name()
                self.source_field_name = rel.field.m2m_reverse_field_name()
                self.target_field_name = rel.field.m2m_field_name()
                self.symmetrical = False

            self.through = rel.through
            self.reverse = reverse

            self.source_field = self.through._meta.get_field(
                self.source_field_name
            )
            self.target_field = self.through._meta.get_field(
                self.target_field_name
            )

            self.core_filters = {}
            self.pk_field_names = {}
            for lh_field, rh_field in self.source_field.related_fields:
                core_filter_key = "%s__%s" % (
                    self.query_field_name,
                    rh_field.name,
                )
                self.core_filters[core_filter_key] = getattr(
                    instance, rh_field.attname
                )
                self.pk_field_names[lh_field.name] = rh_field.name

            self.related_val = self.source_field.get_foreign_related_value(
                instance
            )
            if None in self.related_val:
                raise ValueError(
                    '"%r" needs to have a value for field "%s" before '
                    "this many-to-many relationship can be used."
                    % (instance, self.pk_field_names[self.source_field_name])
                )
            # Even if this relation is not to pk, we require still pk value.
            # The wish is that the instance has been already saved to DB,
            # although having a pk value isn't a guarantee of that.
            if not instance._is_pk_set():
                raise ValueError(
                    "%r instance needs to have a primary key value before "
                    "a many-to-many relationship can be used."
                    % instance.__class__.__name__
                )

        def __call__(self, *, manager):
            manager = getattr(self.model, manager)
            manager_class = (
                django_related_descriptors.create_forward_many_to_many_manager(
                    manager.__class__, rel, reverse
                )
            )
            return manager_class(instance=self.instance)

        do_not_call_in_templates = True

        def _build_remove_filters(self, removed_vals):

            if isinstance(removed_vals, QuerySet):
                removed_vals = removed_vals.values(
                    self.target_field.target_field.attname
                )

            filters = Q.create([(self.source_field_name, self.related_val)])
            # No need to add a subquery condition if removed_vals is a QuerySet
            # without filters.
            removed_vals_filters = (
                not isinstance(removed_vals, QuerySet)
                or removed_vals._has_filters()
            )
            if removed_vals_filters:
                filters &= Q.create(
                    [(f"{self.target_field_name}__in", removed_vals)]
                )
            if self.symmetrical:
                symmetrical_filters = Q.create(
                    [(self.target_field_name, self.related_val)]
                )
                if removed_vals_filters:
                    symmetrical_filters &= Q.create(
                        [(f"{self.source_field_name}__in", removed_vals)]
                    )
                filters |= symmetrical_filters
            return filters

        def _apply_rel_filters(self, queryset):
            with queryset._avoid_cloning():
                queryset._add_hints(instance=self.instance)
                if self._db:
                    queryset = queryset.using(self._db)
                queryset._fetch_mode = self.instance._state.fetch_mode
                queryset._defer_next_filter = True
                return queryset._next_is_sticky().filter(**self.core_filters)

        def get_prefetch_cache(self):
            # Walk up the ancestor-chain (if cached) to try and find a prefetch
            # in an ancestor.
            for instance, _ in _traverse_ancestors(
                rel.field.model, self.instance
            ):
                try:
                    return instance._prefetched_objects_cache[
                        self.prefetch_cache_name
                    ]
                except (AttributeError, KeyError):
                    pass
            return None

        def _remove_prefetched_objects(self):
            # Walk up the ancestor-chain (if cached) to try and find a prefetch
            # in an ancestor.
            for instance, _ in _traverse_ancestors(
                rel.field.model, self.instance
            ):
                try:
                    instance._prefetched_objects_cache.pop(
                        self.prefetch_cache_name
                    )
                except (AttributeError, KeyError):
                    pass  # nothing to clear from cache

        def get_queryset(self):
            if (cache := self.get_prefetch_cache()) is not None:
                return cache
            else:
                queryset = super().get_queryset()
                return self._apply_rel_filters(queryset)

        async def _add_base(
            self, *objs, through_defaults=None, using=None, raw=False
        ):
            db = using or router.db_for_write(
                self.through, instance=self.instance
            )
            async with async_atomic(using=db, savepoint=False):
                await self._add_items(
                    self.source_field_name,
                    self.target_field_name,
                    *objs,
                    through_defaults=through_defaults,
                    using=db,
                    raw=raw,
                )
                # If this is a symmetrical m2m relation to self, add the mirror
                # entry in the m2m table.
                if self.symmetrical:
                    await self._add_items(
                        self.target_field_name,
                        self.source_field_name,
                        *objs,
                        through_defaults=through_defaults,
                        using=db,
                        raw=raw,
                    )

        async def aadd(self, *objs, through_defaults=None):
            self._remove_prefetched_objects()
            db = router.db_for_write(self.through, instance=self.instance)
            await self._add_base(
                *objs, through_defaults=through_defaults, using=db
            )

        aadd.alters_data = True

        async def _remove_base(self, *objs, using=None, raw=False):
            db = using or router.db_for_write(
                self.through, instance=self.instance
            )
            await self._remove_items(
                self.source_field_name,
                self.target_field_name,
                *objs,
                using=db,
                raw=raw,
            )

        async def aremove(self, *objs):
            self._remove_prefetched_objects()
            db = router.db_for_write(self.through, instance=self.instance)
            await self._remove_base(*objs, using=db)

        aremove.alters_data = True

        async def _clear_base(self, using=None, raw=False):
            db = using or router.db_for_write(
                self.through, instance=self.instance
            )
            async with async_atomic(using=db, savepoint=False):
                await signals.m2m_changed.asend(
                    sender=self.through,
                    action="pre_clear",
                    instance=self.instance,
                    reverse=self.reverse,
                    model=self.model,
                    pk_set=None,
                    using=db,
                    raw=raw,
                )
                filters = self._build_remove_filters(
                    super().get_queryset().using(db)
                )
                await self.through._async_base_manager.using(db).filter(
                    filters
                ).adelete()

                await signals.m2m_changed.asend(
                    sender=self.through,
                    action="post_clear",
                    instance=self.instance,
                    reverse=self.reverse,
                    model=self.model,
                    pk_set=None,
                    using=db,
                    raw=raw,
                )

        async def aclear(self):
            self._remove_prefetched_objects()
            db = router.db_for_write(self.through, instance=self.instance)
            await self._clear_base(using=db)

        aclear.alters_data = True

        async def set_base(
            self, objs, *, clear=False, through_defaults=None, raw=False
        ):
            # Force evaluation of `objs` in case it's a queryset whose value
            # could be affected by `manager.clear()`. Refs #19816.
            objs = (
                [obj async for obj in objs]
                if isinstance(objs, QuerySet)
                else tuple(objs)
            )

            db = router.db_for_write(self.through, instance=self.instance)
            async with async_atomic(using=db, savepoint=False):
                self._remove_prefetched_objects()
                if clear:
                    await self._clear_base(using=db, raw=raw)
                    await self._add_base(
                        *objs,
                        through_defaults=through_defaults,
                        using=db,
                        raw=raw,
                    )
                else:
                    old_ids = {
                        val
                        async for val in self.using(db).values_list(
                            self.target_field.target_field.attname, flat=True
                        )
                    }

                    new_objs = []
                    for obj in objs:
                        fk_val = (
                            self.target_field.get_foreign_related_value(obj)[0]
                            if isinstance(obj, self.model)
                            else self.target_field.get_prep_value(obj)
                        )
                        if fk_val in old_ids:
                            old_ids.remove(fk_val)
                        else:
                            new_objs.append(obj)

                    await self._remove_base(*old_ids, using=db, raw=raw)
                    await self._add_base(
                        *new_objs,
                        through_defaults=through_defaults,
                        using=db,
                        raw=raw,
                    )

        async def aset(self, objs, *, clear=False, through_defaults=None):
            await self.set_base(
                objs, clear=clear, through_defaults=through_defaults
            )

        aset.alters_data = True

        async def acreate(self, *, through_defaults=None, **kwargs):
            db = router.db_for_write(
                self.instance.__class__, instance=self.instance
            )
            new_obj = await super(
                ManyRelatedManager, self.db_manager(db)
            ).acreate(**kwargs)
            await self.aadd(new_obj, through_defaults=through_defaults)
            return new_obj

        acreate.alters_data = True

        async def aget_or_create(self, *, through_defaults=None, **kwargs):
            db = router.db_for_write(
                self.instance.__class__, instance=self.instance
            )
            obj, created = await super(
                ManyRelatedManager, self.db_manager(db)
            ).aget_or_create(**kwargs)
            # We only need to add() if created because if we got an object back
            # from get() then the relationship already exists.
            if created:
                await self.aadd(obj, through_defaults=through_defaults)
            return obj, created

        aget_or_create.alters_data = True

        async def aupdate_or_create(self, *, through_defaults=None, **kwargs):
            db = router.db_for_write(
                self.instance.__class__, instance=self.instance
            )
            obj, created = await super(
                ManyRelatedManager, self.db_manager(db)
            ).aupdate_or_create(**kwargs)
            # We only need to add() if created because if we got an object back
            # from get() then the relationship already exists.
            if created:
                await self.aadd(obj, through_defaults=through_defaults)
            return obj, created

        aupdate_or_create.alters_data = True

        def _get_target_ids(self, target_field_name, objs):
            from django.db.models import Model

            target_ids = set()
            target_field = self.through._meta.get_field(target_field_name)
            for obj in objs:
                if isinstance(obj, self.model):
                    if not router.allow_relation(obj, self.instance):
                        raise ValueError(
                            'Cannot add "%r": instance is on database "%s", '
                            'value is on database "%s"'
                            % (obj, self.instance._state.db, obj._state.db)
                        )
                    target_id = target_field.get_foreign_related_value(obj)[0]
                    if target_id is None:
                        raise ValueError(
                            'Cannot add "%r": the value for field "%s" is None'
                            % (obj, target_field_name)
                        )
                    target_ids.add(target_id)
                elif isinstance(obj, Model):
                    raise TypeError(
                        "'%s' instance expected, got %r"
                        % (self.model._meta.object_name, obj)
                    )
                else:
                    target_ids.add(target_field.get_prep_value(obj))
            return target_ids

        async def _get_missing_target_ids(
            self, source_field_name, target_field_name, db, target_ids
        ):
            vals = (
                self.through._async_base_manager.using(db)
                .values_list(target_field_name, flat=True)
                .filter(
                    **{
                        source_field_name: self.related_val[0],
                        "%s__in" % target_field_name: target_ids,
                    }
                )
            )
            return target_ids.difference([val async for val in vals])

        def _get_add_plan(self, db, source_field_name):
            # Conflicts can be ignored when the intermediary model is
            # auto-created as the only possible collision is on the
            # (source_id, target_id) tuple. The same assertion doesn't hold for
            # user-defined intermediary models as they could have other fields
            # causing conflicts which must be surfaced.
            can_ignore_conflicts = (
                self.through._meta.auto_created is not False
                and async_connections[db].features.supports_ignore_conflicts
            )
            # Don't send the signal when inserting duplicate data row
            # for symmetrical reverse entries.
            must_send_signals = (
                self.reverse or source_field_name == self.source_field_name
            ) and (signals.m2m_changed.has_listeners(self.through))
            # Fast addition through bulk insertion can only be performed
            # if no m2m_changed listeners are connected for self.through
            # as they require the added set of ids to be provided via
            # pk_set.
            return (
                can_ignore_conflicts,
                must_send_signals,
                (can_ignore_conflicts and not must_send_signals),
            )

        async def _add_items(
            self,
            source_field_name,
            target_field_name,
            *objs,
            through_defaults=None,
            using=None,
            raw=False,
        ):
            # source_field_name: the PK fieldname in join table for the source
            # object target_field_name: the PK fieldname in join table for the
            # target object *objs - objects to add. Either object instances, or
            # primary keys of object instances.
            if not objs:
                return

            through_defaults = dict(resolve_callables(through_defaults or {}))
            target_ids = self._get_target_ids(target_field_name, objs)
            db = using or router.db_for_write(
                self.through, instance=self.instance
            )
            can_ignore_conflicts, must_send_signals, can_fast_add = (
                self._get_add_plan(db, source_field_name)
            )
            if can_fast_add:
                await self.through._async_base_manager.using(db).abulk_create(
                    [
                        self.through(
                            **{
                                "%s_id"
                                % source_field_name: self.related_val[0],
                                "%s_id" % target_field_name: target_id,
                            }
                        )
                        for target_id in target_ids
                    ],
                    ignore_conflicts=True,
                )
                return

            missing_target_ids = await self._get_missing_target_ids(
                source_field_name, target_field_name, db, target_ids
            )
            async with async_atomic(using=db, savepoint=False):
                if must_send_signals:
                    await signals.m2m_changed.asend(
                        sender=self.through,
                        action="pre_add",
                        instance=self.instance,
                        reverse=self.reverse,
                        model=self.model,
                        pk_set=missing_target_ids,
                        using=db,
                        raw=raw,
                    )
                # Add the ones that aren't there already.
                await self.through._async_base_manager.using(db).abulk_create(
                    [
                        self.through(
                            **through_defaults,
                            **{
                                "%s_id"
                                % source_field_name: self.related_val[0],
                                "%s_id" % target_field_name: target_id,
                            },
                        )
                        for target_id in missing_target_ids
                    ],
                    ignore_conflicts=can_ignore_conflicts,
                )

                if must_send_signals:
                    await signals.m2m_changed.asend(
                        sender=self.through,
                        action="post_add",
                        instance=self.instance,
                        reverse=self.reverse,
                        model=self.model,
                        pk_set=missing_target_ids,
                        using=db,
                        raw=raw,
                    )

        async def _remove_items(
            self,
            source_field_name,
            target_field_name,
            *objs,
            using=None,
            raw=False,
        ):
            # source_field_name: the PK colname in join table for the source
            # object target_field_name: the PK colname in join table for the
            # target object *objs - objects to remove. Either object instances,
            # or primary keys of object instances.
            if not objs:
                return

            # Check that all the objects are of the right type
            old_ids = set()
            for obj in objs:
                if isinstance(obj, self.model):
                    fk_val = self.target_field.get_foreign_related_value(obj)[
                        0
                    ]
                    old_ids.add(fk_val)
                else:
                    old_ids.add(obj)

            db = using or router.db_for_write(
                self.through, instance=self.instance
            )
            async with async_atomic(using=db, savepoint=False):
                # Send a signal to the other end if need be.
                await signals.m2m_changed.asend(
                    sender=self.through,
                    action="pre_remove",
                    instance=self.instance,
                    reverse=self.reverse,
                    model=self.model,
                    pk_set=old_ids,
                    using=db,
                    raw=raw,
                )
                target_model_qs = super().get_queryset()
                if target_model_qs._has_filters():
                    old_vals = target_model_qs.using(db).filter(
                        **{
                            "%s__in"
                            % self.target_field.target_field.attname: old_ids
                        }
                    )
                else:
                    old_vals = old_ids
                filters = self._build_remove_filters(old_vals)
                await self.through._async_base_manager.using(db).filter(
                    filters
                ).adelete()

                await signals.m2m_changed.asend(
                    sender=self.through,
                    action="post_remove",
                    instance=self.instance,
                    reverse=self.reverse,
                    model=self.model,
                    pk_set=old_ids,
                    using=db,
                    raw=raw,
                )

    return ManyRelatedManager
