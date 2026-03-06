PYTHON = .venv/bin/python

.PHONY: queue publish publish-site viz-sync test

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

viz-sync:
	$(PYTHON) gen-podcast.py viz-sync

test:
	$(PYTHON) -m pytest tests/ -v
