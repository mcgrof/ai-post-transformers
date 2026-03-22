"""Shared pytest fixtures for paper-feed tests."""

import re
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


class FakeSentenceTransformer:
    """Deterministic local embedding stub for offline tests."""

    KEYWORDS = {
        "public": [
            "llm", "language model", "transformer", "mixture of experts",
            "benchmark", "open-weights", "foundation model", "train",
            "inference", "architecture", "reasoning", "multimodal",
        ],
        "memory": [
            "kv cache", "cache", "memory", "bandwidth", "hbm", "dram",
            "cxl", "ssd", "nvme", "offload", "prefetch", "tier",
            "disaggregated", "vllm", "throughput", "latency",
            "serving", "scheduling", "data movement", "interconnect",
        ],
        "negative": [
            "image restoration", "super-resolution", "medical",
            "pathology", "molecular", "wind power", "dialogue",
            "legal", "asr", "cifar", "fashion-mnist", "radiology",
            "segmentation", "deblurring", "denoising",
        ],
    }

    def __init__(self, model_name):
        self.model_name = model_name

    def encode(self, texts, normalize_embeddings=True,
               show_progress_bar=False, batch_size=64):
        if isinstance(texts, str):
            texts = [texts]

        rows = [self._embed(text) for text in texts]
        arr = np.asarray(rows, dtype=float)
        if normalize_embeddings:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0.0] = 1.0
            arr = arr / norms
        return arr

    def _embed(self, text):
        lowered = text.lower()
        public = self._count_hits(lowered, self.KEYWORDS["public"])
        memory = self._count_hits(lowered, self.KEYWORDS["memory"])
        negative = self._count_hits(lowered, self.KEYWORDS["negative"])

        if not any((public, memory, negative)):
            public = 0.1

        return np.array([
            1.0 + public,
            1.0 + memory,
            1.0 + negative,
            1.0 + public * memory,
            1.0 + public * 0.5,
            1.0 + memory * 0.5,
        ], dtype=float)

    def _count_hits(self, text, keywords):
        hits = 0.0
        for kw in keywords:
            if re.search(re.escape(kw), text):
                hits += 1.0
        return hits


@pytest.fixture(scope="session", autouse=True)
def patch_sentence_transformer():
    import editorial_scorer

    editorial_scorer.SentenceTransformer = FakeSentenceTransformer


@pytest.fixture(scope="session")
def project_root():
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def config(project_root):
    with open(project_root / "config.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def regression_cases(project_root):
    with open(project_root / "tests" / "regression_cases.yaml") as f:
        return yaml.safe_load(f)["cases"]


@pytest.fixture(scope="session")
def editorial_scorer(config):
    """Shared EditorialScorer instance (expensive to init)."""
    from editorial_scorer import EditorialScorer
    return EditorialScorer(config, podcasted_ids=set())
