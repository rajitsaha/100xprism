"""Streaming JSONL checkpoints shared by the economics adapters.

Offsets are binary positions after complete newline-terminated records. Content
sequences are the starting byte positions, so backfill never rereads a prefix to
recover an ordinal. Checkpoint inputs are copied before any reducer runs.
"""
import copy
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

MAX_CONTENT_RECORD_BYTES = 1048576


def _invalid_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def restore(target, saved):
    """Hydrate nested counter factories from a JSON-serializable aggregate."""
    for key, value in saved.items():
        if isinstance(value, dict):
            child = target[key] if isinstance(target, defaultdict) else target.get(key, {})
            target[key] = restore(child, value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def observations(summary):
    return {key: copy.deepcopy(summary.get(key))
            for key in ("first_activity", "last_activity", "context_growth")}


def observe_activity(observed, record):
    timestamp = next((record[key] for key in ("timestamp", "time", "ts")
                      if record.get(key) is not None), None)
    if timestamp is None:
        message = record.get("message")
        timestamp = message.get("timestamp") if isinstance(message, dict) else None
    try:
        if isinstance(timestamp, (int, float)):
            stamp = datetime.fromtimestamp(timestamp / 1000 if timestamp > 1e12 else timestamp,
                                           timezone.utc)
        elif isinstance(timestamp, str):
            stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            stamp = stamp.astimezone(timezone.utc)
        else:
            return
    except (ValueError, OverflowError, OSError):
        return
    text = stamp.isoformat().replace("+00:00", "Z")
    for key, earlier in (("first_activity", True), ("last_activity", False)):
        previous = observed[key]
        if previous is None:
            observed[key] = text
        else:
            old_stamp = datetime.fromisoformat(previous.replace("Z", "+00:00"))
            if (earlier and stamp < old_stamp) or (not earlier and stamp > old_stamp):
                observed[key] = text


def observe_context(observed, tokens):
    if not isinstance(tokens, (int, float)) or isinstance(tokens, bool) or tokens <= 0:
        return
    growth = observed["context_growth"]
    if growth is None:
        growth = {"first_context_tokens": tokens, "max_context_tokens": tokens}
        observed["context_growth"] = growth
    growth["last_context_tokens"] = tokens
    growth["max_context_tokens"] = max(growth["max_context_tokens"], tokens)
    first = growth["first_context_tokens"]
    growth["delta_tokens"] = tokens - first
    growth["growth_ratio"] = tokens / first if first else None


def legacy_records(path):
    """Keep foreground parse_file's tolerant, unterminated-EOF behavior."""
    with open(path, errors="ignore") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(record, dict):
                yield record


class Records:
    def __init__(self, path, offset=0, limit_bytes=None, max_line_bytes=None, observer=None):
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a nonnegative byte position")
        if limit_bytes is not None and (not isinstance(limit_bytes, int) or limit_bytes < 0):
            raise ValueError("limit_bytes must be nonnegative")
        self.path = path
        self.offset = offset
        self.sequence = offset
        self.bytes_read = 0
        self.limit_bytes = limit_bytes
        self.max_line_bytes = max_line_bytes
        self.partial = b""
        self.observer = observer

    def __iter__(self):
        with open(self.path, "rb") as stream:
            if self.offset > os.fstat(stream.fileno()).st_size:
                raise ValueError("offset is beyond transcript EOF")
            stream.seek(self.offset)
            while self.limit_bytes is None or self.bytes_read < self.limit_bytes:
                remaining = -1 if self.limit_bytes is None else self.limit_bytes - self.bytes_read
                if self.max_line_bytes is not None:
                    remaining = self.max_line_bytes if remaining == -1 else min(remaining, self.max_line_bytes)
                raw = stream.readline(remaining)
                self.bytes_read += len(raw)
                if not raw or not raw.endswith(b"\n"):
                    self.partial = raw
                    return
                self.sequence = self.offset
                try:
                    record = json.loads(raw.decode("utf-8"), parse_constant=_invalid_constant)
                except (UnicodeError, ValueError) as exc:
                    raise ValueError(f"malformed JSONL record at byte {self.offset}") from exc
                if not isinstance(record, dict):
                    raise ValueError(f"JSONL record at byte {self.offset} must be an object")
                if self.observer is not None:
                    self.observer(raw)
                self.offset += len(raw)
                yield record


def parse(path, reducer, offset=0, state=None, summary=None, observer=None):
    if offset and (state is None or summary is None):
        raise ValueError("continuation requires state and summary")
    records = Records(path, offset, observer=observer)
    aggregate, continuation = reducer(records, copy.deepcopy(state), copy.deepcopy(summary))
    return {"summary": aggregate, "state": continuation, "offset": records.offset,
            "bytes_read": records.bytes_read}


def _text(content, kinds):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(block["text"] for block in content
                     if isinstance(block, dict) and block.get("type") in kinds
                     and isinstance(block.get("text"), str) and block["text"])


def _content(tool, record):
    if tool == "cursor":
        raw = record.get("message")
        message = ({**raw, "role": raw.get("role") or record.get("role")}
                   if isinstance(raw, dict) else {"role": record.get("role"), "content": raw})
        kinds = {"text"}
    elif tool == "codex":
        # response_item messages are authoritative; event_msg repeats them.
        if record.get("type") != "response_item":
            return None
        message = record.get("payload")
        if not isinstance(message, dict) or message.get("type") != "message":
            return None
        kinds = {"input_text", "output_text", "text"}
    elif tool in {"claude-code", "pi"}:
        message = record.get("message", record if tool == "pi" else None)
        kinds = {"text"}
    else:
        raise ValueError(f"unsupported transcript tool: {tool}")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if role not in {"user", "assistant"}:
        return None
    text = _text(message.get("content"), kinds)
    if not text:
        return None
    return {"role": role, "text": text,
            "timestamp": record.get("timestamp") or message.get("timestamp")
            or record.get("time") or record.get("ts")}


def extract_content(tool, path, offset=0, limit_bytes=1048576, state=None):
    """Extract prompts and prose within a hard logical byte-read budget.

    Records larger than 1 MiB are omitted from search. A tiny optional state
    drains such records over bounded calls without committing a partial line;
    persist it alongside the independent content offset. Economics still parses
    the entire record. No content is retained in continuation state.
    """
    if tool not in {"claude-code", "codex", "pi", "cursor"}:
        raise ValueError(f"unsupported transcript tool: {tool}")
    # Validate budgets even when a saved oversize-record drain is active.
    records = Records(path, offset, limit_bytes, MAX_CONTENT_RECORD_BYTES)
    state = copy.deepcopy(state or {})
    drained = 0
    if state:
        start, cursor = state.get("skip_start"), state.get("skip_offset")
        if start != offset or not isinstance(cursor, int) or cursor < offset:
            raise ValueError("invalid oversized content continuation")
        with open(path, "rb") as stream:
            if cursor > os.fstat(stream.fileno()).st_size:
                raise ValueError("content continuation is beyond transcript EOF")
            stream.seek(cursor)
            while drained < limit_bytes:
                raw = stream.readline(min(limit_bytes - drained, MAX_CONTENT_RECORD_BYTES))
                drained += len(raw)
                cursor += len(raw)
                if raw.endswith(b"\n"):
                    offset = cursor
                    state = {}
                    break
                if not raw:
                    break
        if state:
            return {"rows": [], "offset": offset, "bytes_read": drained,
                    "state": {"skip_start": offset, "skip_offset": cursor}}
        records = Records(path, offset, limit_bytes - drained, MAX_CONTENT_RECORD_BYTES)
    rows = []
    for record in records:
        row = _content(tool, record)
        if row:
            row["sequence"] = records.sequence
            rows.append(row)
    if len(records.partial) >= MAX_CONTENT_RECORD_BYTES:
        state = {"skip_start": records.offset,
                 "skip_offset": offset + records.bytes_read}
    return {"rows": rows, "offset": records.offset,
            "bytes_read": drained + records.bytes_read, "state": state}
