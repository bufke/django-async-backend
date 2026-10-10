from django.db.models import signals


async def names(manager):
    return [obj.name async for obj in manager.order_by("name")]


class RecordM2MChanged:
    """Record every m2m_changed signal sent for ``m2m_sender``."""

    m2m_sender = None

    def setUp(self):
        super().setUp()
        self.sent = []
        signals.m2m_changed.connect(self.receiver, sender=self.m2m_sender)
        self.addCleanup(
            signals.m2m_changed.disconnect,
            self.receiver,
            sender=self.m2m_sender,
        )

    def receiver(self, sender, action, instance, reverse, pk_set, **kwargs):
        self.sent.append(
            (action, instance.name, reverse, pk_set and sorted(pk_set))
        )
