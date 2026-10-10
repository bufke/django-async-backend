"""
Runtime patch that gives every Django model the async behavior defined on
``django_async_backend.db.models.base.AsyncModelMixin``.

It exists to add async support to models we can't declare the mixin on:
auto-created models such as m2m "through" models, as well as undecorated and
third-party models.

It also routes many-to-many related managers built on an ``AsyncManager``,
``instance.<m2m>(manager="async_objects")``, to the async manager class.
"""

from functools import cache

from django.db.models.base import Model
from django.db.models.fields import related_descriptors

from django_async_backend.db.models.base import AsyncModelMixin
from django_async_backend.db.models.fields import (
    related_descriptors as async_related_descriptors,
)
from django_async_backend.db.models.manager import AsyncManager

_ORIGINAL_MODEL_ATTRS = frozenset(dir(Model))
_ASYNC_MIXIN_MEMBERS = {
    name: value
    for name, value in vars(AsyncModelMixin).items()
    if not name.startswith("__")
}
_sync_create_forward_many_to_many_manager = (
    related_descriptors.create_forward_many_to_many_manager
)

_patched = False


@cache
def _create_async_forward_many_to_many_manager(superclass, rel, reverse):
    return async_related_descriptors.create_forward_many_to_many_manager(
        superclass, rel, reverse
    )


def _create_forward_many_to_many_manager(superclass, rel, reverse):
    if issubclass(superclass, AsyncManager):
        factory = _create_async_forward_many_to_many_manager
    else:
        factory = _sync_create_forward_many_to_many_manager
    return factory(superclass, rel, reverse)


def _patch_related_descriptors():
    related_descriptors.create_forward_many_to_many_manager = (
        _create_forward_many_to_many_manager
    )


def _patch_model():
    global _patched
    if _patched:  # pragma: no cover
        return
    for name, value in _ASYNC_MIXIN_MEMBERS.items():
        setattr(Model, name, value)
    _patch_related_descriptors()
    _patched = True
