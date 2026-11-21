"""Коллекторы по платформам: веб (média a instituce) и stenoprotokoly PSP."""

from apps.actors.models import Platform, Source

from .psp import PspCollector
from .web import WebCollector

COLLECTORS = {
    Platform.WEB: WebCollector,
    Platform.PARLIAMENT: PspCollector,
}


def collector_for(source: Source):
    try:
        return COLLECTORS[source.platform]
    except KeyError:
        raise ValueError(f"Pro platformu neexistuje kolektor: {source.platform!r}") from None
