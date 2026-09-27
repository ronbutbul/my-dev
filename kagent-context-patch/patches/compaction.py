# Patched version of google/adk/apps/compaction.py
# Adds INFO-level logging at every decision point so operators can see
# exactly when compaction fires and why it was (or was not) triggered.
#
# Original: Copyright 2025 Google LLC, Apache-2.0

from __future__ import annotations

import logging

from google.adk.apps.app import App
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.session import Session

logger = logging.getLogger('google_adk.' + __name__)

# Also write to the kagent logger so these lines show up even if the
# google_adk hierarchy is filtered.
_kagent_logger = logging.getLogger('kagent.compaction')


def _log(msg, *args):
    """Log at INFO on both loggers."""
    logger.info(msg, *args)
    _kagent_logger.info(msg, *args)


async def _run_compaction_for_sliding_window(
    app: App, session: Session, session_service: BaseSessionService
):
    events = session.events

    cfg = app.events_compaction_config
    _log(
        '[compaction] check: session=%s  events=%d  '
        'compaction_interval=%d  overlap_size=%d',
        session.id,
        len(events) if events else 0,
        cfg.compaction_interval,
        cfg.overlap_size,
    )

    if not events:
        _log('[compaction] skip: no events in session')
        return None

    # Find the last compaction event and its range.
    last_compacted_end_timestamp = 0.0
    for event in reversed(events):
        if (
            event.actions
            and event.actions.compaction
            and event.actions.compaction.end_timestamp
        ):
            last_compacted_end_timestamp = event.actions.compaction.end_timestamp
            break

    if last_compacted_end_timestamp:
        _log('[compaction] last compaction end_timestamp=%.3f', last_compacted_end_timestamp)
    else:
        _log('[compaction] no previous compaction found in session')

    # Get unique invocation IDs and their latest timestamps.
    invocation_latest_timestamps = {}
    for event in events:
        if event.invocation_id and not (event.actions and event.actions.compaction):
            invocation_latest_timestamps[event.invocation_id] = max(
                invocation_latest_timestamps.get(event.invocation_id, 0.0),
                event.timestamp,
            )

    unique_invocation_ids = list(invocation_latest_timestamps.keys())

    # Determine which invocations are new since the last compaction.
    new_invocation_ids = [
        inv_id
        for inv_id in unique_invocation_ids
        if invocation_latest_timestamps[inv_id] > last_compacted_end_timestamp
    ]

    _log(
        '[compaction] total invocations=%d  new since last compaction=%d  need=%d to trigger',
        len(unique_invocation_ids),
        len(new_invocation_ids),
        cfg.compaction_interval,
    )

    if len(new_invocation_ids) < cfg.compaction_interval:
        _log(
            '[compaction] skip: only %d new invocation(s), need %d',
            len(new_invocation_ids),
            cfg.compaction_interval,
        )
        return None  # Not enough new invocations to trigger compaction.

    # Determine the range of invocations to compact.
    end_inv_id = new_invocation_ids[-1]

    first_new_inv_id = new_invocation_ids[0]
    first_new_inv_idx = unique_invocation_ids.index(first_new_inv_id)

    start_idx = max(0, first_new_inv_idx - cfg.overlap_size)
    start_inv_id = unique_invocation_ids[start_idx]

    # Find the index of the last event with end_inv_id.
    last_event_idx = -1
    for i in range(len(events) - 1, -1, -1):
        if events[i].invocation_id == end_inv_id:
            last_event_idx = i
            break

    events_to_compact = []
    if last_event_idx != -1:
        first_event_start_inv_idx = -1
        for i, event in enumerate(events):
            if event.invocation_id == start_inv_id:
                first_event_start_inv_idx = i
                break
        if first_event_start_inv_idx != -1:
            events_to_compact = events[first_event_start_inv_idx : last_event_idx + 1]
            events_to_compact = [
                e
                for e in events_to_compact
                if not (e.actions and e.actions.compaction)
            ]

    if not events_to_compact:
        _log('[compaction] skip: no compactable events found in range [%s..%s]', start_inv_id, end_inv_id)
        return None

    _log(
        '[compaction] TRIGGERED: compacting %d events across invocations %s -> %s',
        len(events_to_compact),
        start_inv_id,
        end_inv_id,
    )

    if not cfg.summarizer:
        canonical = app.root_agent.canonical_model
        _log('[compaction] creating LlmEventSummarizer with model: %s', getattr(canonical, 'model', str(canonical)))
        cfg.summarizer = _LoggingSummarizer(canonical)

    compaction_event = await cfg.summarizer.maybe_summarize_events(events=events_to_compact)

    if compaction_event:
        _log('[compaction] SUCCESS: CompactedEvent created and appended to session')
        await session_service.append_event(session=session, event=compaction_event)
    else:
        _log('[compaction] WARNING: summarizer returned None — no CompactedEvent produced')

    _log('[compaction] finished')


class _LoggingSummarizer:
    """Wraps LlmEventSummarizer and adds INFO-level logging."""

    def __init__(self, llm):
        self._inner = LlmEventSummarizer(llm=llm)
        _log('[summarizer] initialized  model=%s', getattr(llm, 'model', str(llm)))

    async def maybe_summarize_events(self, *, events):
        _log('[summarizer] summarizing %d events...', len(events))
        result = await self._inner.maybe_summarize_events(events=events)
        if result:
            _log('[summarizer] done — summary produced')
        else:
            _log('[summarizer] done — no summary produced (empty events?)')
        return result
