from django.apps import AppConfig


class BookingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bookings"

    def ready(self):
        from django.db import transaction

        from bookings import cancellations
        from markets.signals import occurrence_cancelled

        def on_occurrence_cancelled(sender, occurrence, actor, role, **kwargs):
            cancellations.record_occurrence_cancellation(occurrence, actor, role)
            # Right after the date's cancellation commits: cancel its bookings
            # and record their refunds (database only). Stripe work follows in
            # reconcile_payments or the organizer's "retry now".
            transaction.on_commit(
                lambda: cancellations.process_occurrence_cancellations(
                    limit=200, provider=False, run_id=occurrence.cancellation_run.pk
                )
            )

        # weak=False: the receiver is a local function; a weak reference
        # would be collected immediately and the signal silently dropped.
        occurrence_cancelled.connect(
            on_occurrence_cancelled, weak=False, dispatch_uid="bookings.occurrence_cancelled"
        )
