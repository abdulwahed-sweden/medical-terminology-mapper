#!/usr/bin/env python
"""Compute and store embeddings for a loaded terminology version.

    python scripts/embed_terminology.py --system icd10se --version 2026 --provider fake

Embeddings are stored per `(system, version, provider, model)`, so several
vector spaces can coexist and a stored proposal can always name the one that
produced its candidates. Re-running for the same tuple replaces it.

The text embedded is the concept's names -- preferred term plus synonyms --
and, when INDEX_DESCRIPTIONS is on, the publisher's description too, so the two
retrieval stages see the same concept. Changing that setting therefore requires
re-running this script.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa

from app.config import get_settings
from app.db.models import ConceptEmbeddingRow, ConceptRow
from app.db.session import session_scope
from app.embeddings import build_embedding_provider
from app.logging_setup import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("--system", required=True, choices=["icd10se", "kva", "snomed"])
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--provider",
        choices=["fake", "openai_compat"],
        default=None,
        help="Overrides EMBEDDING_PROVIDER for this run.",
    )
    parser.add_argument("--model", default=None, help="Overrides EMBEDDING_MODEL.")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Proceed without confirming. Required when the provider is not "
            "`fake`, because that run costs money."
        ),
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    if args.provider:
        settings = settings.model_copy(update={"embedding_provider": args.provider})
    if args.model:
        settings = settings.model_copy(update={"embedding_model": args.model})

    provider = build_embedding_provider(settings)
    print(f"provider={provider.provider_id} model={provider.model_id} dim={provider.dim}")
    if provider.dim != settings.embedding_dim:
        print(
            f"ERROR: provider dimension {provider.dim} != EMBEDDING_DIM "
            f"{settings.embedding_dim}. The pgvector column is fixed-width; "
            f"changing dimension requires a migration.",
            file=sys.stderr,
        )
        return 2

    with session_scope() as session:
        concepts = session.execute(
            sa.select(ConceptRow.code, ConceptRow.search_text, ConceptRow.description_text)
            .where(ConceptRow.system == args.system, ConceptRow.version == args.version)
            .order_by(ConceptRow.code)
        ).all()

        if not concepts:
            print(
                f"no concepts loaded for ({args.system}, {args.version}); "
                f"run scripts/load_terminology.py first",
                file=sys.stderr,
            )
            return 1

        # A live provider charges per request. Embedding a full release is a
        # five-figure row count, and the mistake that gets made is embedding the
        # wrong (system, version) or re-embedding one that is already done. A
        # keystroke is cheaper than a bill.
        if provider.provider_id != "fake" and not args.yes:
            batches = math.ceil(len(concepts) / args.batch_size)
            print(
                f"\nThis will send {len(concepts)} concepts to "
                f"{provider.provider_id}/{provider.model_id} "
                f"in about {batches} request(s), and it will be charged to the "
                f"configured account.\n"
                f"  system  {args.system}\n"
                f"  version {args.version}\n"
                f"\nRe-run with --yes to proceed.",
                file=sys.stderr,
            )
            return 3

        session.execute(
            sa.delete(ConceptEmbeddingRow).where(
                ConceptEmbeddingRow.system == args.system,
                ConceptEmbeddingRow.version == args.version,
                ConceptEmbeddingRow.provider == provider.provider_id,
                ConceptEmbeddingRow.model == provider.model_id,
            )
        )

        written = 0
        for start in range(0, len(concepts), args.batch_size):
            batch = concepts[start : start + args.batch_size]
            vectors = provider.embed(
                [
                    f"{row.search_text} {row.description_text}".strip()
                    if settings.index_descriptions
                    else row.search_text
                    for row in batch
                ]
            )
            session.add_all(
                [
                    ConceptEmbeddingRow(
                        system=args.system,
                        version=args.version,
                        code=row.code,
                        provider=provider.provider_id,
                        model=provider.model_id,
                        dim=provider.dim,
                        embedding=vector,
                    )
                    for row, vector in zip(batch, vectors, strict=True)
                ]
            )
            written += len(batch)
            print(f"  embedded {written}/{len(concepts)}", end="\r", flush=True)

    print(f"\nwrote {written} embeddings for ({args.system}, {args.version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
