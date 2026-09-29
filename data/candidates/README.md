# Candidate pages

Used when `RECIPEBOT_SOURCE_MODE=candidates`. Keep one file per category key, named
`<category>.txt`, for example `high_protein.txt` or `noodles.txt`, with one recipe page URL per
line. Blank lines and lines starting with `#` are ignored.

Each run picks a handful of URLs at random (skipping any already posted), fetches them, pulls the
schema.org Recipe data out of the page head and pastes it into the brief, so the model only has
to choose and rewrite instead of searching. Pages without schema.org Recipe data are skipped.
When a category file is missing or every URL has been used, the run falls back to letting the
model search the web.

Only list exact recipe pages from sites you trust. Never guess URLs.
