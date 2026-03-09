PYTHON = .venv/bin/python

.PHONY: queue publish publish-site gen-viz backfill-images test

queue:
	$(PYTHON) gen-podcast.py queue

# Usage: make publish DRAFT=drafts/2026/03/2026-03-04-episode-stem
publish:
ifndef DRAFT
	$(error publish requires DRAFT=<path>, e.g. make publish DRAFT=drafts/2026/03/episode-stem)
endif
	$(PYTHON) gen-podcast.py publish --draft $(DRAFT)

publish-site:
	$(PYTHON) gen-podcast.py publish-site

gen-viz:
	$(PYTHON) gen-podcast.py gen-viz

backfill-images:
	$(PYTHON) backfill_images.py

test:
	$(PYTHON) -m pytest tests/ -v
