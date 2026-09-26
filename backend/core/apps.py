from django.apps import AppConfig
from django.core.signals import request_finished


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from core import request_id

        request_finished.connect(request_id.clear, dispatch_uid="core.request_id.clear")
