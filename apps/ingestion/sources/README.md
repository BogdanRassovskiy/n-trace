# Списки источников

Источники подключаются командой (идемпотентно — повторный запуск обновляет, не дублирует):

```bash
docker compose exec web python manage.py import_sources apps/ingestion/sources/mainstream.json
docker compose exec web python manage.py import_sources apps/ingestion/sources/institutions.json && docker compose exec web python manage.py import_institutions
docker compose exec web python manage.py import_psp_deputies   # слой P: депутаты + источник стенограмм
```

| Файл | Слой | Статус |
|---|---|---|
| `mainstream.json` | M | ČT24, iROZHLAS, Seznam Zprávy, iDNES, Novinky, Blesk, ČTK (ленты проверены 2026-11-02, ČTK — 2026-11-03) |
| `institutions.json` | I | BIS (výpis), NÚKIB (RSS), MV (výpis) — проверено 2026-11-03. Úřad vlády, MF, MMR, MPSV, MZ, MŽP (RSS); MŠMT, MSp, MZV, MK, MO (výpis) — проверено 2026-11-21. Не собираются: MPO (robots.txt разрешает только поисковикам), MD и MZe (антибот). Реестр органов — `import_institutions` |
| — | P | источник стенограмм создаёт `import_psp_deputies` |

## Формат

```json
{
  "sources": [
    {
      "name": "Название издания или органа",
      "url": "https://example.cz/",
      "layer": "M",
      "platform": "web",
      "collect_interval_minutes": 60,
      "request_delay_seconds": 3,
      "feeds": [
        {"url": "https://example.cz/rss", "rubric": "Domácí"},
        {"url": "https://example.cz/sitemap-news.xml", "kind": "sitemap"}
      ],
      "actor": {"name": "Название издания", "type": "media", "criterion": "Основание включения"}
    }
  ]
}
```

- **Слои:** `M` — zpravodajská média, `I` — státní instituce (`"actor": {"type": "institution", …}`);
  слой `P` (stenoprotokoly) подключает `import_psp_deputies`.
- Ленты (`feeds[].kind`): `rss` (по умолчанию), `sitemap` (в т. ч. индекс — читаются
  `max_child_sitemaps` свежих), `listing` — HTML-страница со списком статей для сайтов без
  пригодной ленты; ссылки отбираются по `collector_config.listing_link_pattern` (regex пути).
- `collector_config`:
  - `rubrics_in_scope` / `rubrics_out_of_scope` — начала рубрик (без регистра и диакритики)
    для `Document.in_scope`; без списков в рамках всё;
  - `max_articles` — новых статей за запуск (100);
  - `listing_link_xpath` — XPath к href ссылок на статьи на странице-списке;
  - `date_regex` — дата из текста страницы (группы `day`, `month`, `year`), если htmldate ошибается;
  - `max_date_lag_days` — допустимое отставание даты от сбора (30; у органов 730);
  - `origin_xpath` — XPath к адресу первоисточника на странице → `meta.origin_urls`;
  - `listing_strip_query` — отбросить параметры у ссылок со страницы-списка (Liferay, MSp);
  - `prefer_feed_date` — дата из ленты, а не со страницы (`date_source = feed`), если
    htmldate на сайте ошибается (Úřad vlády, MPSV, MŽP);
  - `title_xpath` — XPath к заголовку, если trafilatura берёт название рубрики (MK).
- `Crawl-delay` из robots.txt сборщик не читает: для таких сайтов задайте
  `request_delay_seconds` вручную (MŽP — 10, MZV — 20).
- `expected_language: ""` — язык не проверяется (сайт на нескольких языках).
- `actor` — сайт целиком как один актор (издание или орган).
