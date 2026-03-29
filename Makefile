PYTHON = .venv/bin/python

.PHONY: queue publish publish-site gen-viz backfill-images test test-publish

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

test-publish:
	$(PYTHON) -m pytest \
		tests/test_publish_jobs.py \
		tests/test_publish_job_store.py \
		tests/test_publish_job_runner.py \
		tests/test_run_publish_worker.py \
		tests/test_publish_state_machine.py \
		tests/test_queue_store.py \
		tests/test_queue_bridge.py \
		tests/test_run_podcast_worker.py -v
	node --test admin/src/worker.test.js
	node --test admin/src/systemd.test.js
	$(PYTHON) -m pytest tests/test_systemd_units.py -v
