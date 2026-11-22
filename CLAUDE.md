# NTrace — pravidla vývoje

## Jazyk

- **Celé rozhraní je v češtině**: administrace (verbose_name, choices, help_text, nadpisy),
  veřejná část a panel (šablony), upozornění v panelu a v Telegramu, zprávy a chyby, které
  uživatel uvidí (včetně textů ukládaných do DB, např. důvody zařazení), výstup management
  příkazů a demo data. `LANGUAGE_CODE = "cs"`.
- Rusky jsou pouze pokyny autora projektu (komunikace s asistentem). Ruské komentáře a
  docstringy v kódu a README zatím zůstávají; nové texty rozhraní nikdy nepsat rusky.
- Nové uživatelské texty psát rovnou česky se správnou diakritikou a českými uvozovkami „…“.
- Změna popisků modelů vytváří migraci (jen metadata, bez změny schématu) — přiložit ji.

## Další konvence

- NTrace je samostatný projekt, nezávislý na TrollTrace. Vývoj probíhá přímo ve větvi `main`,
  bez vedlejších větví a pull requestů.
- Průběh etap se zaznamenává na Trello nástěnce „NTrace — разработка (диплом)“.
- Před commitem: `ruff format . && ruff check .` a `pytest` (v kontejneru `web`).

## Sběr dat

- robots.txt se respektuje. Výjimka jen u konkrétního zdroje rozhodnutím autora projektu:
  `collector_config.ignore_robots` + `ignore_robots_reason` (bez důvodu neplatí); pauzy a
  otevřený User-Agent zůstávají.
- Antibotové ochrany, captcha, přihlášení a paywally se neobcházejí.
