"""Справочник státních institucí слоя I и их людей (README §2), идемпотентно.

    python manage.py import_sources apps/ingestion/sources/institutions.json
    python manage.py import_institutions

- Источники (ленты сайтов органов) подключаются import_sources; здесь орган связывается
  с ними по адресу, а его актором становится актор этих источников. У органа без ленты
  создаётся собственный актор слоя I — документы будет к кому отнести, как только
  подключится лента.
- Vedoucí představitelé (ředitelé, předsedové úřadů) — акторы слоя I (тип official).
- Ministři zůstávají politiky ve své vrstvě: найденный актор (poslanec PSP) только
  связывается с ministerstvem, новый создаётся без слоя; funkci «člen vlády» ведёт
  sync_politicians.
"""

import datetime
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.actors.models import Actor, ActorPosition, Layer, Source
from apps.actors.registry import find_politician
from apps.institutions.models import Institution
from apps.panel import audit

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "institutions.json"
FIELDS = ("abbreviation", "group", "url", "notes")
NOTE = "import_institutions"


def create_actor(**fields) -> Actor:
    criterion = fields.pop("criterion")
    actor = Actor.objects.create(inclusion_criterion=criterion, **fields)
    actor.include(criterion, on=datetime.date.today())
    audit.log_create(None, actor, note=NOTE)
    return actor


class Command(BaseCommand):
    help = "Vytvoří nebo aktualizuje státní instituce vrstvy I, jejich vedoucí a ministry."

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, nargs="?", default=DEFAULT_PATH)

    @transaction.atomic
    def handle(self, *args, path, **options):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"{path}: {exc}") from exc

        for spec in data["institutions"]:
            institution = Institution.objects.filter(name=spec["name"]).first()
            values = {key: spec.get(key, "") for key in FIELDS}
            if institution is None:
                institution = Institution.objects.create(name=spec["name"], **values)
                audit.log_create(None, institution, note=NOTE)
                verb = "vytvořena"
            else:
                before = audit.snapshot(institution)
                for key, value in values.items():
                    setattr(institution, key, value)
                institution.save()
                audit.log_change(None, institution, before, note=NOTE)
                verb = "aktualizována"

            missing = self._link_sources(institution, spec.get("sources", []))
            if institution.actor_id is None:
                institution.actor = create_actor(
                    name=spec["name"], actor_type=Actor.Type.INSTITUTION,
                    layer=Layer.INSTITUTIONS,
                    criterion="Státní instituce — vrstva I (README §2)",
                    inclusion_reference=spec.get("url", ""),
                )  # fmt: skip
                institution.save(update_fields=["actor", "updated_at"])
            for person in spec.get("officials", []):
                official = self._official(person)
                institution.officials.add(official)
                self._position(official, person, institution.name)
            if "minister" in spec:
                institution.minister = self._minister(spec["minister"])
                institution.save(update_fields=["minister", "updated_at"])

            line = (
                f"{institution}: {verb}, zdrojů {institution.sources.count()}, "
                f"vedoucích {institution.officials.count()}"
            )
            if institution.minister_id:
                line += f", ministr {institution.minister}"
            if missing:
                line += f" (chybí zdroj — nejdřív import_sources: {', '.join(missing)})"
            self.stdout.write(line)

    def _link_sources(self, institution, urls) -> list[str]:
        missing = []
        for url in urls:
            source = Source.objects.filter(url=url).first()
            if source is None:
                missing.append(url)
                continue
            institution.sources.add(source)
            account = source.accounts.filter(account_id="").first()
            if account is not None and institution.actor_id is None:
                institution.actor = account.actor
                institution.save(update_fields=["actor", "updated_at"])
        return missing

    def _official(self, person: dict) -> Actor:
        actor = Actor.objects.filter(
            name=person["name"], layer=Layer.INSTITUTIONS, actor_type=Actor.Type.OFFICIAL
        ).first()
        if actor is None:
            actor = create_actor(
                name=person["name"], actor_type=Actor.Type.OFFICIAL,
                layer=Layer.INSTITUTIONS, role=person["role"][:100],
                criterion=f"Vedoucí představitel ({person['role']}) — vrstva I "
                f"(README §2). {person.get('note', '')}".strip(),
                inclusion_reference=person.get("reference", ""),
            )  # fmt: skip
        return actor

    def _position(self, actor: Actor, person: dict, organization: str) -> None:
        position, created = ActorPosition.objects.get_or_create(
            actor=actor,
            kind=ActorPosition.Kind.OFFICIAL,
            title=person["role"],
            end_date=None,
            defaults={
                "organization": organization,
                "start_date": person.get("since") and datetime.date.fromisoformat(person["since"]),
                "source_url": person.get("reference", ""),
                "note": person.get("note", ""),
            },
        )
        if created:
            audit.log_create(None, position, note=NOTE)
        actor.refresh_role()

    def _minister(self, person: dict) -> Actor:
        """Министр — политик: существующий актор не переносится в слой I."""
        actor = find_politician(person["name"])
        if actor is None:
            # Слой не задаётся: vrstvu dokumentu určuje zdroj; funkci «člen vlády»
            # doplní sync_politicians.
            return create_actor(
                name=person["name"], actor_type=Actor.Type.POLITICIAN,
                role=person["role"][:100],
                criterion=f"Člen vlády ({person['role']}); resortní výstupy patří instituci "
                "(README §2).",
                inclusion_reference=person.get("reference", ""),
            )  # fmt: skip
        return actor
