"""Слой I: státní instituce — реестр органов, ленты, министры остаются политиками."""

import datetime
import io
import json
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.actors.models import ANALYSIS_LAYERS, Actor, Layer, Source
from apps.ingestion.collectors.web import page_date, parse_listing
from apps.ingestion.extract import PRAGUE
from apps.institutions.models import Institution

pytestmark = pytest.mark.django_db

SOURCES = "apps/ingestion/sources/institutions.json"


def test_layers_are_media_parliament_institutions_and_all_analysed():
    assert list(Layer.values) == ["M", "P", "I"]
    assert Layer.INSTITUTIONS == "I"
    assert set(ANALYSIS_LAYERS) == {"M", "P", "I"}


def test_import_institution_sources_and_registry():
    deputy = Actor.objects.create(name="Lubomír Metnar", actor_type="politician", layer="P")
    out = io.StringIO()
    call_command("import_sources", SOURCES, stdout=out)
    call_command("import_institutions", stdout=out)
    call_command("import_institutions", stdout=out)  # idempotentně

    assert Source.objects.filter(layer="I").count() == 3
    assert Institution.objects.count() == 21
    assert Institution.objects.filter(group=Institution.Group.MINISTRY).count() == 14
    assert Institution.objects.filter(group=Institution.Group.GOVERNMENT_OFFICE).count() == 1

    bis = Institution.objects.get(abbreviation="BIS")
    assert bis.sources.get().url == "https://www.bis.cz/aktuality/"
    assert (bis.actor.layer, bis.actor.actor_type) == ("I", Actor.Type.INSTITUTION)
    mf = Institution.objects.get(abbreviation="MF")  # ministerstvo zatím bez kanálu
    assert not mf.sources.exists()
    assert (mf.actor.layer, mf.actor.actor_type) == ("I", Actor.Type.INSTITUTION)
    assert Institution.objects.get(abbreviation="VZ").officials.get().actor_type == "official"

    # Ministr zůstává politikem ve své vrstvě — žádný duplikát.
    deputy.refresh_from_db()
    assert deputy.layer == "P"
    assert Institution.objects.get(abbreviation="MV").minister == deputy
    zuna = Institution.objects.get(abbreviation="MO").minister
    assert (zuna.layer, zuna.actor_type) == ("", "politician")
    assert Actor.objects.filter(name="Lubomír Metnar").count() == 1
    assert Actor.objects.filter(layer="I", actor_type=Actor.Type.OFFICIAL).count() == 6
    assert all(a.inclusion_periods.count() == 1 for a in Actor.objects.filter(layer="I"))
    koudelka = bis.officials.get()
    assert koudelka.positions.get().kind == "official" and koudelka.role == "ředitel BIS"


def test_bis_date_from_text_pattern():
    sources = json.loads(Path(SOURCES).read_text())["sources"]
    pattern = sources[0]["collector_config"]["date_regex"]
    html = "<p><strong>Publikováno: 3.6.2025</strong></p>"
    assert page_date(html, pattern) == datetime.datetime(2025, 6, 3, tzinfo=PRAGUE)
    assert page_date("<p>bez data</p>", pattern) is None


def test_listing_by_xpath_without_common_prefix():
    html = """<nav><a href="/o-nas">O nás</a></nav>
    <ul><li><h3><a href="/resort-ma-nove-generaly">Generálové</a></h3></li>
    <li><h3><a href="/ministr-jednal">Jednání</a></h3></li></ul>""".encode()
    entries = parse_listing(html, "https://mv.example/zpravodajstvi", xpath="//li//h3/a/@href")
    assert [e.url for e in entries] == [
        "https://mv.example/resort-ma-nove-generaly",
        "https://mv.example/ministr-jednal",
    ]


def test_document_speaking_role():
    from apps.documents.models import Document

    assert Document.SpeakingRole.INSTITUTIONAL == "institutional"
