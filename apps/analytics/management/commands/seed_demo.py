"""Демо-данные для разработки: акторы слоёв M, P, I, кодбук, документы, агрегаты, мониторинг.

Всё вымышленное: имена «Demo …», адреса в зоне .example. Агрегаты синтетические, но с
осмысленной формой: témata, která přinášejí politici, se nejdřív objeví ve Sněmovně a média je
převezmou se zpožděním; resortní témata startují tiskovou zprávou ministerstva; mediální
témata se do Sněmovny dostávají později. Хватает для режима сравнения на ~350 линий.

    python manage.py seed_demo                 # 350 акторов, 26 недель
    python manage.py seed_demo --reset         # удалить прежние демо-данные и создать заново
    python manage.py seed_demo --clear         # только удалить демо-данные

Демо-данные помечены и живут рядом с реальными, не трогая их: источники в зоне .demo.example,
акторы с основанием «Демо-данные», коды нарративов и тем с префиксом «demo-».
"""

import datetime
import math
import random
import statistics
import zlib
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.actors.models import Actor, ActorAccount, ActorInclusionPeriod, Layer, Platform, Source
from apps.analytics.models import WeeklyAggregate
from apps.documents.models import Document, DocumentQuality, DocumentVersion, ReuseLink
from apps.documents.utils import normalize_url, text_hash
from apps.ingestion.models import (
    GoldenPage,
    Level,
    SourceFeed,
    SourceHealthCheck,
    SourceIncident,
)
from apps.narratives.models import (
    Label,
    Narrative,
    NarrativeCandidate,
    Stance,
    Topic,
)
from apps.panel.defaults import ensure_default_settings
from apps.panel.models import Job, Notification

PARTIES = ["ANO", "ODS", "STAN", "Piráti", "SPD", "KDU-ČSL", "TOP 09", "Motoristé"]
ROLES = ["Poslanec PSP"] * 8 + ["Člen vlády", "Předseda strany", "Europoslanec"]

# Доля акторов по слоям; остаток — Sněmovna (P).
LAYER_SHARES = {
    Layer.MAINSTREAM: 0.04,
    Layer.INSTITUTIONS: 0.05,
}
# Среднее число документов актора в неделю (знаменатель).
WEEKLY_VOLUME = {
    Layer.MAINSTREAM: 70,
    Layer.PARLIAMENT: 3,
    Layer.INSTITUTIONS: 6,
}
# Склонность слоя к темам кодбука (множитель вероятности).
LAYER_AFFINITY = {
    Layer.MAINSTREAM: 0.8,
    Layer.PARLIAMENT: 1.0,
    Layer.INSTITUTIONS: 0.6,
}

TOPICS = [
    ("rozpocet", "Státní rozpočet a daně"),
    ("energetika", "Energetika"),
    ("zdravotnictvi", "Zdravotnictví"),
    ("bydleni", "Bydlení"),
]
# (код, тема, формулировка, kdo téma přináší: political / institutional / media, пик доли)
NARRATIVES = [
    ("N001", "rozpocet", "Konsolidace rozpočtu má stát na škrtech výdajů", "political", 0.15),
    ("N002", "rozpocet", "Konsolidace rozpočtu má stát na vyšších daních", "political", 0.10),
    ("N003", "energetika", "Stát má regulovat ceny energií pro domácnosti", "media", 0.12),
    ("N004", "zdravotnictvi", "Změna úhrad zdravotní péče v nemocnicích", "institutional", 0.10),
    ("N005", "bydleni", "Nový stavební zákon urychlí výstavbu bytů", "institutional", 0.12),
]
# Задержка появления по слоям в неделях от старта темы.
ONSET_LAG = {
    "political": {Layer.PARLIAMENT: 0, Layer.MAINSTREAM: 1, Layer.INSTITUTIONS: 2},
    "institutional": {Layer.INSTITUTIONS: 0, Layer.MAINSTREAM: 1, Layer.PARLIAMENT: 2},
    "media": {Layer.MAINSTREAM: 0, Layer.PARLIAMENT: 2, Layer.INSTITUTIONS: 3},
}
QUALITY_RULES_VERSION = "demo-1"
DEMO_HOST = "demo.example"
DEMO_CRITERION = "Demo data"
DEMO_PREFIX = "demo-"


class Command(BaseCommand):
    help = "Naplní DB demo daty pro vývoj (smyšlení aktéři a syntetické agregáty)."

    def add_arguments(self, parser):
        parser.add_argument("--actors", type=int, default=350, help="Počet aktérů (výchozí 350).")
        parser.add_argument("--weeks", type=int, default=26, help="Počet týdnů (výchozí 26).")
        parser.add_argument(
            "--seed", type=int, default=42, help="Semínko generátoru náhodných čísel."
        )
        parser.add_argument(
            "--reset", action="store_true", help="Smazat dosavadní demo data a vytvořit je znovu."
        )
        parser.add_argument(
            "--clear", action="store_true", help="Pouze smazat demo data (skutečná zůstanou)."
        )

    def handle(self, *args, actors, weeks, seed, reset, clear, **options):
        if clear:
            with transaction.atomic():
                self._reset()
            self.stdout.write(self.style.SUCCESS("Demo data smazána."))
            return
        if actors < 10 or weeks < 8:
            raise CommandError("Je potřeba alespoň 10 aktérů a 8 týdnů.")
        if Actor.objects.filter(inclusion_criterion=DEMO_CRITERION).exists() and not reset:
            raise CommandError(
                "Demo data už existují. Spusťte s --reset pro jejich znovuvytvoření."
            )

        self.rng = random.Random(seed)
        today = timezone.localdate()
        last_monday = today - datetime.timedelta(days=today.weekday())
        self.weeks = [last_monday - datetime.timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]

        with transaction.atomic():
            if reset:
                self._reset()
            ensure_default_settings()
            sources = self._create_sources(actors)
            actor_list = self._create_actors(actors, sources)
            narratives = self._create_codebook()
            rows = self._create_aggregates(actor_list, narratives)
            documents = self._create_documents(actor_list, narratives, rows)
            self._create_monitoring(sources)
            self._create_panel_data(narratives, documents)

        self.stdout.write(
            self.style.SUCCESS(
                f"Hotovo: {len(actor_list)} aktérů, {Source.objects.count()} zdrojů, "
                f"{len(narratives)} narativů, {len(documents)} dokumentů, "
                f"{WeeklyAggregate.objects.count()} řádků agregátů za {weeks} týdnů."
            )
        )

    # --- Очистка -------------------------------------------------------------------------

    def _reset(self):
        """Удаляет только помеченные демо-данные (см. docstring модуля)."""
        sources = Source.objects.filter(url__contains=DEMO_HOST)
        actors = Actor.objects.filter(
            Q(inclusion_criterion=DEMO_CRITERION) | Q(accounts__source__in=sources)
        ).distinct()
        narratives = Narrative.objects.filter(code__startswith=DEMO_PREFIX)
        documents = Document.objects.filter(Q(source__in=sources) | Q(actor__in=actors))
        WeeklyAggregate.objects.filter(Q(actor__in=actors) | Q(narrative__in=narratives)).delete()
        Label.objects.filter(Q(document__in=documents) | Q(narrative__in=narratives)).delete()
        NarrativeCandidate.objects.filter(description__startswith="Demo").delete()
        documents.delete()  # версии, оценки качества и заимствования — каскадом
        Actor.objects.filter(pk__in=list(actors.values_list("pk", flat=True))).delete()
        sources.delete()  # ленты, запуски, проверки, инциденты — каскадом
        narratives.delete()
        Topic.objects.filter(code__startswith=DEMO_PREFIX).delete()
        Notification.objects.filter(text__startswith="Demo").delete()
        Job.objects.filter(message__startswith="Demo").delete()

    # --- Источники и акторы --------------------------------------------------------------

    def _layer_counts(self, total: int) -> dict[str, int]:
        counts = {layer: max(1, round(total * share)) for layer, share in LAYER_SHARES.items()}
        counts[Layer.PARLIAMENT] = total - sum(counts.values())
        return counts

    def _create_sources(self, total_actors: int) -> dict:
        counts = self._layer_counts(total_actors)
        sources = {"psp": None, "sites": defaultdict(list)}
        sites = [
            (Layer.MAINSTREAM, "Demo Zprávy", "zpravy", 48),
            (Layer.INSTITUTIONS, "Demo ministerstvo", "ministerstvo", 24 * 14),
        ]
        for layer, title, slug, stale_after in sites:
            for i in range(1, counts[layer] + 1):
                source = Source.objects.create(
                    name=f"{title} {i}",
                    layer=layer,
                    platform=Platform.WEB,
                    url=f"https://{slug}-{i}.demo.example/",
                    stale_after_hours=stale_after,
                )
                SourceFeed.objects.create(source=source, url=f"{source.url}rss")
                sources["sites"][layer].append(source)
        sources["psp"] = Source.objects.create(
            name="Stenoprotokoly Poslanecké sněmovny (demo)",
            layer=Layer.PARLIAMENT,
            platform=Platform.PARLIAMENT,
            url="https://psp.demo.example/",
            stale_after_hours=24 * 14,
        )
        return sources

    def _create_actors(self, total: int, sources: dict) -> list[Actor]:
        counts = self._layer_counts(total)
        rng = self.rng
        actors: list[Actor] = []

        def add(actor: Actor, source: Source, account_id: str, url: str = ""):
            actor.inclusion_criterion = DEMO_CRITERION
            actor.save()
            ActorAccount.objects.create(actor=actor, source=source, account_id=account_id, url=url)
            actors.append(actor)
            return actor

        for layer, actor_type in [
            (Layer.MAINSTREAM, Actor.Type.MEDIA),
            (Layer.INSTITUTIONS, Actor.Type.INSTITUTION),
        ]:
            for source in sources["sites"][layer]:
                add(Actor(name=source.name, actor_type=actor_type, layer=layer), source, "")

        for i in range(1, counts[Layer.PARLIAMENT] + 1):
            add(
                Actor(
                    name=f"Demo poslanec {i:03d}",
                    actor_type=Actor.Type.POLITICIAN,
                    layer=Layer.PARLIAMENT,
                    party=rng.choice(PARTIES),
                    role=rng.choice(ROLES),
                ),
                sources["psp"],
                f"poslanec-{i:03d}",
            )

        # Периоды включения: все с первой недели; часть исключена по ходу, часть возвращена.
        start = self.weeks[0]
        for actor in actors:
            actor.include("Demo: počáteční výběr", on=start)
            if rng.random() < 0.03:
                out_week = rng.randrange(len(self.weeks) // 3, len(self.weeks) - 2)
                actor.exclude("Demo: vyřazen z výběru", on=self.weeks[out_week])
                if rng.random() < 0.35:
                    actor.include("Demo: opětovné zařazení", on=self.weeks[out_week + 2])
        return actors

    # --- Кодбук --------------------------------------------------------------------------

    def _create_codebook(self) -> list[dict]:
        topics = {
            code: Topic.objects.create(code=DEMO_PREFIX + code, name=name) for code, name in TOPICS
        }
        narratives = []
        for code, topic, name, kind, peak in NARRATIVES:
            narrative = Narrative(
                code=DEMO_PREFIX + code.lower(),
                topic=topics[topic],
                name=name,
                definition=f"Demo definice: {name.lower()}. Postoj se zaznamenává zvlášť.",
                examples=[f"Příklad formulace pro {code}"],
            )
            narrative.save(change_note="Demo: počáteční verze kódové knihy")
            onset = self.rng.randrange(2, max(3, len(self.weeks) // 3))
            narratives.append({"obj": narrative, "kind": kind, "peak": peak, "onset": onset})

        # Пример правки кодбука: у N003 будет две версии.
        n003 = narratives[2]["obj"]
        n003.definition += " Upřesněno: včetně zastropování cen a cílených příspěvků."
        n003.save(change_note="Demo: upřesnění definice")
        return narratives

    # --- Агрегаты ------------------------------------------------------------------------

    def _intensity(self, narrative: dict, layer: str, week_index: int) -> float:
        """Ожидаемая доля документов о нарративе в слое на неделе."""
        lag = ONSET_LAG[narrative["kind"]][layer]
        dt = week_index - narrative["onset"] - lag
        if narrative["kind"] != "political":  # událostní průběh: nástup, vrchol, útlum
            if dt < 0:
                return 0.002
            return narrative["peak"] * (dt / 2) * math.exp(1 - dt / 2) + 0.01  # пик через 2 недели
        base = narrative["peak"] * (0.6 + 0.4 * math.sin(week_index / 3))
        return base if dt >= 0 else base * 0.3

    def _poisson(self, lam: float) -> int:
        if lam > 30:
            return max(0, round(self.rng.gauss(lam, math.sqrt(lam))))
        threshold, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= threshold:
                return k
            k += 1

    def _binomial(self, n: int, p: float) -> int:
        if n > 40:
            return min(n, max(0, round(self.rng.gauss(n * p, math.sqrt(n * p * (1 - p))))))
        return sum(self.rng.random() < p for _ in range(n))

    def _create_aggregates(
        self, actors: list[Actor], narratives: list[dict]
    ) -> list[WeeklyAggregate]:
        rng = self.rng
        periods = defaultdict(list)
        for period in ActorInclusionPeriod.objects.all():
            periods[period.actor_id].append(period)

        def included(actor: Actor, week: datetime.date) -> bool:
            return any(
                p.start_date <= week and (p.end_date is None or week <= p.end_date)
                for p in periods[actor.pk]
            )

        rows: list[WeeklyAggregate] = []
        totals_by_actor: dict[int, int] = defaultdict(int)
        for actor in actors:
            activity = rng.lognormvariate(0, 0.6)
            affinity = LAYER_AFFINITY[actor.layer] * rng.lognormvariate(0, 0.5)
            for wi, week in enumerate(self.weeks):
                if not included(actor, week):
                    continue
                n_total = self._poisson(WEEKLY_VOLUME[actor.layer] * activity)
                if n_total == 0:
                    continue
                totals_by_actor[actor.pk] += n_total
                for narrative in narratives:
                    p = min(0.9, self._intensity(narrative, actor.layer, wi) * affinity)
                    n = self._binomial(n_total, p)
                    rows.append(
                        WeeklyAggregate(
                            actor=actor,
                            narrative=narrative["obj"],
                            week=week,
                            n_documents=n,
                            n_total=n_total,
                            share=n / n_total,
                            engagement={"views": n * rng.randint(50, 2000)},
                        )
                    )

        self._assign_cohorts(actors, totals_by_actor)

        # Группы: слой и партия — сумма числителей и знаменателей по участникам.
        groups: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
        actor_by_id = {a.pk: a for a in actors}
        for row in rows:
            actor = actor_by_id[row.actor_id]
            keys = [f"layer:{actor.layer}"] + ([f"party:{actor.party}"] if actor.party else [])
            for key in keys:
                acc = groups[(key, row.narrative_id, row.week)]
                acc[0] += row.n_documents
                acc[1] += row.n_total
        group_rows = [
            WeeklyAggregate(
                group_key=key,
                narrative_id=narrative_id,
                week=week,
                n_documents=n,
                n_total=total,
                share=n / total,
            )
            for (key, narrative_id, week), (n, total) in groups.items()
        ]

        self._fill_z_scores(rows, lambda r: (r.actor_id, r.narrative_id))
        self._fill_z_scores(group_rows, lambda r: (r.group_key, r.narrative_id))
        WeeklyAggregate.objects.bulk_create(rows, batch_size=5000)
        WeeklyAggregate.objects.bulk_create(group_rows, batch_size=5000)
        return rows

    @staticmethod
    def _fill_z_scores(rows: list[WeeklyAggregate], key):
        """z-оценка доли относительно собственной истории актора/группы (README §5)."""
        series = defaultdict(list)
        for row in rows:
            series[key(row)].append(row)
        for items in series.values():
            shares = [r.share for r in items]
            if len(shares) < 2:
                continue
            mean, stdev = statistics.fmean(shares), statistics.pstdev(shares)
            for r in items:
                r.z_score = round((r.share - mean) / stdev, 3) if stdev > 0 else 0.0

    @staticmethod
    def _assign_cohorts(actors: list[Actor], totals: dict[int, int]):
        """Терцили активности внутри слоя (README §2: переменная стратификации, не отбора)."""
        by_layer = defaultdict(list)
        for actor in actors:
            by_layer[actor.layer].append(actor)
        cohorts = [Actor.ActivityCohort.LOW, Actor.ActivityCohort.MEDIUM, Actor.ActivityCohort.HIGH]
        for members in by_layer.values():
            members.sort(key=lambda a: totals.get(a.pk, 0))
            for i, actor in enumerate(members):
                actor.activity_cohort = cohorts[min(2, i * 3 // len(members))]
        Actor.objects.bulk_update(actors, ["activity_cohort"])

    # --- Документы -----------------------------------------------------------------------

    def _create_documents(self, actors, narratives, rows) -> list[Document]:
        """По несколько документов на актора: с метками, версиями и оценкой качества."""
        rng = self.rng
        tz = timezone.get_current_timezone()
        accounts = {a.actor_id: a for a in ActorAccount.objects.select_related("source")}
        hits = defaultdict(list)
        for row in rows:
            if row.n_documents:
                hits[row.actor_id].append(row)
        narrative_by_id = {n["obj"].pk: n["obj"] for n in narratives}

        documents, picks = [], []
        now = timezone.now()
        for actor in actors:
            account = accounts[actor.pk]
            for row in rng.sample(hits[actor.pk], k=min(3, len(hits[actor.pk]))):
                narrative = narrative_by_id[row.narrative_id]
                published = datetime.datetime.combine(
                    row.week + datetime.timedelta(days=rng.randrange(7)),
                    datetime.time(rng.randrange(6, 23), rng.randrange(60)),
                    tzinfo=tz,
                )
                n = len(documents) + 1
                url = f"{account.source.url}clanek/{n}?utm_source=rss"
                text = f"[DEMO] {narrative.name}. Ukázkový text dokumentu pro vývoj. " * 8
                documents.append(
                    Document(
                        actor=actor,
                        source=account.source,
                        url=url,
                        url_normalized=normalize_url(url),
                        title=f"[DEMO] {narrative.name}",
                        text=text,
                        text_hash=text_hash(text),
                        language="cs",
                        published_at=min(published, now),
                        date_source=rng.choice(
                            [Document.DateSource.PAGE] * 4 + [Document.DateSource.FEED]
                        ),
                        collected_at=min(
                            published + datetime.timedelta(minutes=rng.randrange(5, 240)), now
                        ),
                        is_relevant=True,
                        engagement={"views": rng.randint(10, 50_000)},
                    )
                )
                picks.append(narrative)
        Document.objects.bulk_create(documents, batch_size=2000)

        current_version = {n.pk: n.latest_version.pk for n in narrative_by_id.values()}
        versions, qualities, labels = [], [], []
        for doc, narrative in zip(documents, picks, strict=True):
            raw = zlib.compress(
                f"<html><body><h1>{doc.title}</h1><p>{doc.text}</p></body></html>".encode()
            )
            versions.append(
                DocumentVersion(
                    document=doc,
                    version=1,
                    title=doc.title,
                    text=doc.text,
                    text_hash=doc.text_hash,
                    raw_html_compressed=raw,
                )
            )
            qualities.append(self._quality(doc))
            labels.append(
                Label(
                    document=doc,
                    narrative=narrative,
                    narrative_version_id=current_version[narrative.pk],
                    stance=rng.choice([Stance.PRO, Stance.CONTRA, Stance.NEUTRAL, Stance.NEUTRAL]),
                    origin=Label.Origin.MODEL,
                    confidence=round(rng.uniform(0.55, 0.99), 3),
                    model_name="demo-classifier",
                )
            )
        DocumentVersion.objects.bulk_create(versions, batch_size=2000)
        DocumentQuality.objects.bulk_create(qualities, batch_size=2000)
        Label.objects.bulk_create(labels, batch_size=2000)

        # Несколько правленых статей: вторая версия текста.
        for doc in rng.sample(documents, k=max(1, len(documents) // 40)):
            doc.record_version(text=doc.text + " Aktualizace: doplněno vyjádření.", title=doc.title)

        self._create_reuse_links(documents, picks)
        return documents

    def _quality(self, doc: Document) -> DocumentQuality:
        """Флаги → оценка; распределение примерно как у здорового сбора."""
        roll = self.rng.random()
        flags = dict(
            has_title=True,
            has_published_at=True,
            date_plausible=True,
            text_ok=True,
            text_short=False,
            text_empty=False,
            lang_ok=True,
            boilerplate_dup=False,
            blocked_page=False,
            selectors_ok=None,
            extraction_ratio_ok=True,
        )
        if doc.date_source != Document.DateSource.PAGE:
            score = 4
        elif roll < 0.82:
            score = 5
        elif roll < 0.92:
            score, flags["text_ok"], flags["text_short"] = 3, False, True
        elif roll < 0.97:
            score, flags["lang_ok"], flags["extraction_ratio_ok"] = 2, False, False
        else:
            score, flags["boilerplate_dup"] = 1, True
        return DocumentQuality(
            document=doc, score=score, quality_rules_version=QUALITY_RULES_VERSION, **flags
        )

    def _create_reuse_links(self, documents, picks):
        """Převzetí: dřívější dokument jiného aktéra k témuž narativu → pozdější (přetisk ČTK,
        tiskové zprávy, citace projevu)."""
        by_narrative = defaultdict(list)
        for doc, narrative in zip(documents, picks, strict=True):
            by_narrative[narrative.pk].append(doc)
        links = []
        for doc, narrative in zip(documents, picks, strict=True):
            if doc.source.layer != Layer.MAINSTREAM or self.rng.random() > 0.3:
                continue
            earlier = [
                o
                for o in by_narrative[narrative.pk]
                if o.published_at < doc.published_at and o.actor_id != doc.actor_id
            ]
            if not earlier:
                continue
            verbatim = self.rng.random() < 0.4
            links.append(
                ReuseLink(
                    source_document=self.rng.choice(earlier),
                    target_document=doc,
                    link_type=ReuseLink.Type.VERBATIM if verbatim else ReuseLink.Type.PARAPHRASE,
                    method=ReuseLink.Method.MINHASH if verbatim else ReuseLink.Method.KNN,
                    similarity=round(
                        self.rng.uniform(0.7, 0.98) if verbatim else self.rng.uniform(0.85, 0.95), 3
                    ),
                )
            )
        ReuseLink.objects.bulk_create(links, ignore_conflicts=True)

    # --- Мониторинг и панель -------------------------------------------------------------

    def _create_monitoring(self, sources: dict):
        today = timezone.localdate()
        all_sources = list(Source.objects.all())
        checks = [
            SourceHealthCheck(
                source=source,
                date=today - datetime.timedelta(days=d),
                check_type=SourceHealthCheck.CheckType.VOLUME,
                status=Level.OK,
                metrics={"n": self.rng.randint(5, 120)},
            )
            for source in all_sources
            for d in range(14)
        ]
        SourceHealthCheck.objects.bulk_create(checks)

        broken = sources["sites"][Layer.INSTITUTIONS][0]
        SourceIncident.objects.create(
            source=broken,
            incident_type=SourceIncident.Type.STALE_FEED,
            level=Level.WARNING,
            started_at=timezone.now() - datetime.timedelta(days=2),
            description="Demo: výpis tiskových zpráv se 14 dní neaktualizoval.",
        )
        recovered = sources["sites"][Layer.MAINSTREAM][0]
        SourceIncident.objects.create(
            source=recovered,
            incident_type=SourceIncident.Type.SELECTOR_MISSING,
            level=Level.CRITICAL,
            started_at=timezone.now() - datetime.timedelta(days=20),
            ended_at=timezone.now() - datetime.timedelta(days=18),
            description="Demo: změna rozvržení, selektor data se dva dny nenacházel.",
        )
        for i in (1, 2):
            GoldenPage.objects.create(
                source=recovered,
                url=f"{recovered.url}clanek/golden-{i}",
                expected_title=f"Kontrolní článek {i}",
                expected_text_prefix="Ukázkový začátek textu kontrolního článku…",
            )

    def _create_panel_data(self, narratives, documents):
        Notification.objects.create(
            notification_type=Notification.Type.COLLECTION,
            level=Notification.Level.WARNING,
            title="Zdroj se neaktualizoval 48 h",
            text="Demo upozornění na incident sběru.",
        )
        Notification.objects.create(
            notification_type=Notification.Type.SPIKE,
            title=f"Výkyv u {narratives[0]['obj'].code}",
            text="Demo upozornění na výkyv u aktéra.",
        )
        Job.objects.create(
            job_type=Job.Type.AGGREGATE,
            status=Job.Status.SUCCEEDED,
            progress=1,
            message="Demo: přepočet agregátů",
            started_at=timezone.now() - datetime.timedelta(minutes=3),
            finished_at=timezone.now(),
        )
        window_end = self.weeks[-1]
        candidate = NarrativeCandidate.objects.create(
            description="Demo shluk: „Zkrácení pracovního týdne na čtyři dny“",
            keywords=["pracovní týden", "čtyřdenní", "zaměstnavatelé"],
            n_documents=27,
            n_authors=9,
            window_start=window_end - datetime.timedelta(days=13),
            window_end=window_end,
            layers=[Layer.MAINSTREAM, Layer.PARLIAMENT],
        )
        candidate.example_documents.set(documents[:3])
        candidate.nearest_narratives.set([narratives[1]["obj"]])
