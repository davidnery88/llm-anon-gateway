"""Moteur de scan multi-DB. run_scan() = logique pure, dépendances injectées."""
from __future__ import annotations
import asyncio
from gateway.value_metadata import value_metadata
from gateway.logging_config import get as _log

_logger = _log("gateway.scanner")


async def run_scan(*, connector, classifier, labels, jobs, job_id: int,
                   dbs: list[str], include_views: bool, threshold: float,
                   sample_n: int = 5, config_store=None) -> None:
    try:
        refs = []
        for db in dbs:
            refs += await asyncio.to_thread(connector.list_objects, db, include_views)
        await jobs.update_job(job_id, total_cols=len(refs))
        scanned = found = 0
        for ref in refs:
            job = await jobs.get_job(job_id) if hasattr(jobs, "get_job") else None
            if job and job.get("status") == "cancelled":
                return
            scanned += 1
            if await labels.exists_active(ref.column, ref.db, ref.table):
                await jobs.update_job(job_id, scanned_cols=scanned,
                                      current_db=ref.db, current_table=ref.table)
                continue
            try:
                vals = await asyncio.to_thread(connector.sample_values, ref, sample_n)
            except Exception as e:
                _logger.warning("scan.sample_failed", extra={"table": ref.table, "err": str(e)})
                await jobs.update_job(job_id, scanned_cols=scanned)
                continue
            if not vals:
                await jobs.update_job(job_id, scanned_cols=scanned)
                continue
            meta = [value_metadata(v) for v in vals]
            label, conf, suggested_regex = await classifier.classify(
                table=ref.table, column=ref.column, sql_type=ref.sql_type, value_metadata=meta)
            if label:
                status = "active" if conf >= threshold else "pending"
                await labels.upsert_ctx(header=ref.column, label=label, source="qwen3",
                                        confidence=conf, status=status, db_name=ref.db,
                                        table_name=ref.table, sample_values=vals[:3])
                found += 1
            if suggested_regex and config_store is not None:
                pattern_name = f"qwen_{ref.db}_{ref.table}_{ref.column}"[:64]
                await config_store.upsert_pattern_pending(
                    name=pattern_name,
                    regex=suggested_regex,
                    entity_label=label or "POLICE",
                    score=round(conf, 2),
                )
            await jobs.update_job(job_id, scanned_cols=scanned, found_labels=found,
                                  current_db=ref.db, current_table=ref.table)
        await jobs.finish_job(job_id, status="done")
    except Exception as e:
        _logger.error("scan.failed", extra={"job": job_id, "err": str(e)})
        await jobs.finish_job(job_id, status="failed", error=str(e))
