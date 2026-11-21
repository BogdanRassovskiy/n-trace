"""Слой I: státní instituce — Úřad vlády, ministerstva, ústřední úřady, bezpečnostní složky.

Instituce je aktér vrstvy I: dokumenty z jejích kanálů (tiskové zprávy, aktuality) se
přiřazují instituci, ne ministrovi. Ministr zůstává politikem; propojení slouží k
porovnání resortní a politické komunikace k téže agendě.
"""

from django.db import models

from apps.actors.models import Actor, Layer, Source
from apps.core.models import TimeStampedModel


class Institution(TimeStampedModel):
    """Orgán vrstvy I a jeho kanály."""

    class Group(models.TextChoices):
        GOVERNMENT_OFFICE = "government_office", "Úřad vlády"
        MINISTRY = "ministry", "Ministerstvo"
        CENTRAL_AUTHORITY = "central_authority", "Ústřední správní úřad"
        INTELLIGENCE = "intelligence", "Zpravodajská služba"
        SECURITY_FORCE = "security_force", "Bezpečnostní sbor (policie, armáda)"

    name = models.CharField("název", max_length=200, unique=True)
    abbreviation = models.CharField("zkratka", max_length=30, blank=True)
    group = models.CharField("skupina", max_length=20, choices=Group.choices)
    url = models.URLField("web", max_length=500, blank=True)
    actor = models.OneToOneField(
        Actor,
        verbose_name="aktér",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="institution",
        limit_choices_to={"layer": Layer.INSTITUTIONS},
        help_text="Aktér vrstvy I, ke kterému se přiřazují dokumenty z kanálů orgánu.",
    )
    sources = models.ManyToManyField(
        Source,
        verbose_name="kanály a zdroje",
        blank=True,
        related_name="institutions",
        limit_choices_to={"layer": Layer.INSTITUTIONS},
    )
    officials = models.ManyToManyField(
        Actor,
        verbose_name="vedoucí představitelé",
        blank=True,
        related_name="led_institutions",
        limit_choices_to={"actor_type": Actor.Type.OFFICIAL},
        help_text="Ředitelé, náčelníci, předsedové úřadů (ne ministři).",
    )
    minister = models.ForeignKey(
        Actor,
        verbose_name="ministr",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ministries",
        limit_choices_to={"actor_type": Actor.Type.POLITICIAN},
        help_text="Člen vlády v čele resortu; zůstává politikem ve své vrstvě.",
    )
    notes = models.TextField("poznámky", blank=True)

    class Meta:
        verbose_name = "instituce"
        verbose_name_plural = "instituce"
        ordering = ["group", "name"]

    def __str__(self):
        return self.abbreviation or self.name
