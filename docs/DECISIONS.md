# Decisions

## 2026-08-18: modular monolith before distributed workers

Use an in-process typed bus and background executor for M0-M8. This keeps the demo reproducible while preserving semantic topics and adapter boundaries. Redis/Celery becomes an evidence-driven deployment change, not a prerequisite.

## 2026-08-18: SQLite and filesystem

SQLite stores metadata; filesystem paths refer to media. PostgreSQL and object storage are deferred because setup would not improve the single-user vertical slice.

## 2026-08-18: Python 3.11/3.12 application runtime

The Fedora host has Python 3.14, while current video/ML wheels commonly trail it. Docker/native Python 3.12 provides a compatible baseline. The system interpreter remains untouched.

## 2026-08-18: deterministic degraded path plus optional Ultralytics

A simple image-based detector and IoU tracker make tests and degraded processing reproducible. The showcase prefers Ultralytics YOLO nano + ByteTrack when weights and a compatible runtime are available. This fallback is labeled; it is not presented as equivalent model accuracy.

## 2026-08-18: local-first GPU placement

The core path uses the local RTX 3050 if verified, then CPU. The remote RTX 3080 is for isolated model evaluation and optional heavy inference only; application runtime never depends on SSH.

## 2026-08-18: optional ML licensing boundary

Ultralytics and the published four-class YOLOv8x weight are AGPL-3.0 (or require an Enterprise license for incompatible deployment). Keep them in an optional `ml` extra and never bundle weights. The credential-free core uses OpenCV/IoU and describes its accuracy honestly.
