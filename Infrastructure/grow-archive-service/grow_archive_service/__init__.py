"""Grow archive service: grow-aware lossless storage lifecycle.

The package owns the port-8006 FastAPI app (``grow_archive_service.main:app``),
its environment-backed configuration, the bounded heavy-work coordination used
by synchronous Parquet/ZIP jobs, and the publication-freshness guard that keeps
heavy work paused while the automation monitoring publications are stale.

Importing this package has no filesystem, network or database side effects;
every runtime resource is created by the FastAPI lifespan.
"""
