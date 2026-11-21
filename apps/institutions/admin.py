from django.contrib import admin

from apps.panel.audit import AuditedModelAdmin

from .models import Institution


@admin.register(Institution)
class InstitutionAdmin(AuditedModelAdmin):
    list_display = ["name", "abbreviation", "group", "actor", "minister", "source_count"]
    list_filter = ["group"]
    search_fields = ["name", "abbreviation"]
    filter_horizontal = ["sources", "officials"]
    raw_id_fields = ["minister"]

    @admin.display(description="kanálů")
    def source_count(self, obj):
        return obj.sources.count()
