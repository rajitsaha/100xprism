"""Continuation and privacy contracts for newline-delimited transcript adapters."""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters import claude_code, codex, pi


def line(record):
    return (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")


def claude(role, content, usage=None, **extra):
    message = {"role": role, "content": content, "model": "sonnet"}
    if usage:
        message["usage"] = {"input_tokens": usage, "output_tokens": 2}
    return {"type": role, "sessionId": "s1", "timestamp": "2026-10-11T00:00:00Z",
            "message": message, **extra}


def counter(inp, cached, out):
    return {"type": "event_msg", "timestamp": "2026-10-11T00:00:00Z",
            "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out}}}}


class TranscriptFixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "session.jsonl")

    def write(self, data, mode="wb"):
        with open(self.path, mode) as stream:
            stream.write(data)

    def resume(self, adapter, result):
        # SQLite round trips must not lose default factories or mutate a checkpoint.
        return adapter.parse_incremental(self.path, result["offset"],
                                         json.loads(json.dumps(result["state"])),
                                         json.loads(json.dumps(result["summary"])))


class IncrementalAdapters(TranscriptFixtures):
    def test_claude_activity_context_and_agent_identity_cross_checkpoint(self):
        first_record = claude("assistant", "first", 100, agentId="worker-1")
        first_record["message"]["usage"].update({"cache_read_input_tokens": 50})
        self.write(line(first_record))
        first = claude_code.parse_incremental(self.path)
        last_record = claude("assistant", "last", 300, ts="ignored")
        last_record["timestamp"] = "2026-10-12T00:00:00Z"
        self.write(line(last_record), "ab")
        result = self.resume(claude_code, first)
        self.assertEqual(result["summary"]["agent_id"], "worker-1")
        self.assertEqual(result["state"]["agent_id"], "worker-1")
        self.assertEqual(result["summary"]["first_activity"], "2026-10-11T00:00:00Z")
        self.assertEqual(result["summary"]["last_activity"], "2026-10-12T00:00:00Z")
        self.assertEqual(result["summary"]["context_growth"], {
            "first_context_tokens": 150, "last_context_tokens": 300,
            "max_context_tokens": 300, "delta_tokens": 150, "growth_ratio": 2.0})

    def test_codex_context_uses_last_usage_never_cumulative_total(self):
        record = counter(10000, 1000, 500)
        record["payload"]["info"]["last_token_usage"] = {"input_tokens": 100}
        self.write(line(record))
        first = codex.parse_incremental(self.path)
        record = counter(30000, 5000, 1500)
        record["payload"]["info"]["last_token_usage"] = {"input_tokens": 200}
        self.write(line(record), "ab")
        result = self.resume(codex, first)
        self.assertEqual(result["summary"]["context_growth"], {
            "first_context_tokens": 100, "last_context_tokens": 200,
            "max_context_tokens": 200, "delta_tokens": 100, "growth_ratio": 2.0})
        self.write(line(counter(40000, 5000, 1500)))
        self.assertIsNone(codex.parse_incremental(self.path)["summary"]["context_growth"])

    def test_pi_context_and_numeric_activity_are_preserved(self):
        self.write(line({"timestamp": 0, "usage": {"input": 10, "cacheRead": 5}}))
        first = pi.parse_incremental(self.path)
        self.write(line({"timestamp": 1000, "usage": {"input": 30}}), "ab")
        result = self.resume(pi, first)
        self.assertEqual(result["summary"]["first_activity"], "1970-01-01T00:00:00Z")
        self.assertEqual(result["summary"]["last_activity"], "1970-01-01T00:16:40Z")
        self.assertEqual(result["summary"]["context_growth"]["growth_ratio"], 2.0)

    def test_missing_input_counts_do_not_invent_context_size(self):
        record = claude("assistant", "answer")
        record["message"]["usage"] = {"output_tokens": 5}
        self.write(line(record))
        self.assertIsNone(claude_code.parse_incremental(self.path)["summary"]["context_growth"])
        self.write(line({"usage": {"output": 5}}))
        self.assertIsNone(pi.parse_incremental(self.path)["summary"]["context_growth"])

    def test_zero_usage_records_do_not_end_context_at_zero(self):
        self.write(line(claude("assistant", "real", 400)))
        synthetic = claude("assistant", "API error")
        synthetic["message"]["usage"] = {"input_tokens": 0, "output_tokens": 0}
        self.write(line(synthetic), "ab")
        growth = claude_code.parse_incremental(self.path)["summary"]["context_growth"]
        self.assertEqual(growth["last_context_tokens"], 400)

    def test_claude_attribution_and_pending_tools_cross_checkpoint(self):
        prefix = line(claude("user", "<command-name>/commit</command-name>"))
        prefix += line(claude("assistant", [{"type": "tool_use", "name": "Read",
                                               "id": "r1", "input": {"file": "x"}}], 10))
        self.write(prefix)
        first = claude_code.parse_incremental(self.path)
        saved = copy.deepcopy(first)
        suffix = line(claude("user", [{"type": "tool_result", "tool_use_id": "r1",
                                         "content": "file data"}]))
        suffix += line(claude("assistant", "exact", 20, attributionSkill="review"))
        suffix += line(claude("assistant", "resume", 5, isSidechain=True))
        self.write(suffix, "ab")
        result = self.resume(claude_code, first)
        summary = result["summary"]
        self.assertEqual(summary["totals"]["input"], 35)
        self.assertEqual(summary["by_skill"]["/commit"]["input"], 15)
        self.assertEqual(summary["by_skill"]["review"]["input"], 20)
        self.assertEqual(summary["skill_invocations"], {"/commit": 2, "review": 1})
        self.assertEqual(summary["comp"]["files_read"], 9)
        self.assertEqual(summary["subagent_tokens"]["input"], 5)
        self.assertEqual(summary, claude_code.parse_file(self.path))
        self.assertEqual(result["bytes_read"], len(suffix))
        self.assertEqual(first, saved)
        self.assertNotIn("file data", json.dumps(result["state"]))
        self.assertEqual(result["state"]["tool_names"], {})

    def test_claude_first_usage_after_empty_checkpoint_sets_fixed_context(self):
        self.write(line(claude("user", "prompt")))
        first = claude_code.parse_incremental(self.path)
        self.write(line(claude("assistant", "answer", 10)), "ab")
        result = self.resume(claude_code, first)
        self.assertEqual(result["summary"]["first_fixed"], 10)
        self.assertEqual(result["summary"]["first_fixed_day"], "2026-10-11")

    def test_codex_counters_reset_duplicate_and_model_cross_checkpoint(self):
        self.write(line({"type": "session_meta", "payload": {"id": "s1", "cwd": "/tmp"}})
                   + line({"type": "turn_context", "payload": {"model": "gpt"}})
                   + line(counter(1000, 200, 100)))
        first = codex.parse_incremental(self.path)
        suffix = line(counter(1000, 200, 100)) + line(counter(3000, 1200, 250))
        suffix += line({"type": "turn_context", "payload": {"model": "gpt-new"}})
        suffix += line(counter(500, 100, 50)) + line(counter(900, 300, 120))
        self.write(suffix, "ab")
        result = self.resume(codex, first)
        self.assertEqual(result["summary"]["totals"],
                         {"input": 2400, "cache_read": 1500, "output": 370, "cache_write": 0})
        self.assertEqual(result["summary"]["by_model"]["gpt-new"]["input"], 600)
        self.assertEqual(result["summary"]["msgs"], 4)
        self.assertEqual(result["summary"], codex.parse_file(self.path))
        self.assertEqual(result["bytes_read"], len(suffix))

    def test_pi_continuation_keeps_metadata_and_native_cache_usage(self):
        self.write(line({"type": "session", "id": "s1", "cwd": "/tmp"}) + line({
            "timestamp": "2026-10-11T00:00:00Z", "message": {"model": "pi-model",
            "usage": {"input": 10, "output": 2, "cacheRead": 3, "cacheWrite": 1}}}))
        first = pi.parse_incremental(self.path)
        suffix = line({"timestamp": "2026-10-12T00:00:00Z", "model": "pi-model",
                       "usage": {"input": 5, "output": 1}})
        self.write(suffix, "ab")
        result = self.resume(pi, first)
        self.assertEqual(result["summary"]["totals"],
                         {"input": 15, "output": 3, "cache_read": 3, "cache_write": 1})
        self.assertEqual(result["summary"]["session_id"], "s1")
        self.assertEqual(result["summary"], pi.parse_file(self.path))

    def test_partial_utf8_record_is_uncommitted_until_newline(self):
        for adapter in (claude_code, codex, pi):
            with self.subTest(tool=adapter.TOOL):
                complete = line({"sessionId": "s1"})
                partial = line(claude("user", "café"))
                split = partial.index("é".encode()) + 1
                self.write(complete + partial[:split])
                first = adapter.parse_incremental(self.path)
                self.assertEqual(first["offset"], len(complete))
                self.assertEqual(first["bytes_read"], len(complete) + split)
                self.write(partial[split:], "ab")
                result = self.resume(adapter, first)
                self.assertEqual(result["offset"], len(complete) + len(partial))
                self.assertEqual(result["bytes_read"], len(partial))
                unchanged = self.resume(adapter, result)
                self.assertEqual(unchanged["bytes_read"], 0)
                self.assertEqual(unchanged["summary"], result["summary"])

    def test_complete_malformed_records_raise_without_mutating_checkpoint(self):
        for adapter in (claude_code, codex, pi):
            for bad in (b"{broken}\n", b"[]\n", b"\xff\n", b'{"value": NaN}\n'):
                with self.subTest(tool=adapter.TOOL, bad=bad):
                    self.write(line({"sessionId": "s1"}))
                    first = adapter.parse_incremental(self.path)
                    saved = copy.deepcopy(first)
                    self.write(line(claude("assistant", "later", 10)) + bad, "ab")
                    with self.assertRaises(ValueError):
                        adapter.parse_incremental(self.path, first["offset"],
                                                  first["state"], first["summary"])
                    self.assertEqual(first, saved)

    def test_legacy_parser_keeps_tolerant_unterminated_line_behavior(self):
        self.write(b"{broken}\n" + line(claude("assistant", "ok", 10)).rstrip(b"\n"))
        self.assertEqual(claude_code.parse_file(self.path)["totals"]["input"], 10)


class ContentExtraction(TranscriptFixtures):
    def extract(self, tool, **kwargs):
        from adapters import _incremental
        return _incremental.extract_content(tool, self.path, **kwargs)

    def test_claude_excludes_tools_and_thinking(self):
        self.write(line(claude("user", "prompt café")) + line(claude("assistant", [
            {"type": "text", "text": "answer"}, {"type": "thinking", "thinking": "private"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "secret"}}]))
            + line(claude("user", [{"type": "tool_result", "content": "secret result"}])))
        result = self.extract("claude-code")
        self.assertEqual([(r["role"], r["text"]) for r in result["rows"]],
                         [("user", "prompt café"), ("assistant", "answer")])
        self.assertEqual(result["offset"], os.path.getsize(self.path))
        self.assertEqual(result["rows"][0]["timestamp"], "2026-10-11T00:00:00Z")

    def test_codex_response_items_skip_tools_and_duplicate_events(self):
        records = [
            {"type": "event_msg", "payload": {"type": "user_message", "message": "prompt"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "prompt"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "answer"}]}},
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "secret"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "answer"}},
        ]
        self.write(b"".join(line(r) for r in records))
        self.assertEqual([(r["role"], r["text"]) for r in self.extract("codex")["rows"]],
                         [("user", "prompt"), ("assistant", "answer")])

    def test_pi_native_messages_skip_tool_results(self):
        self.write(line({"type": "message", "timestamp": 123, "message": {
            "role": "user", "content": [{"type": "text", "text": "prompt"}]}})
            + line({"type": "message", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "answer"}, {"type": "toolCall", "arguments": "secret"}]}})
            + line({"type": "message", "message": {"role": "toolResult", "content": "secret"}}))
        self.assertEqual([(r["role"], r["text"]) for r in self.extract("pi")["rows"]],
                         [("user", "prompt"), ("assistant", "answer")])

    def test_content_budget_and_offset_preserve_sequence(self):
        first = line(claude("user", "prompt"))
        second = line(claude("assistant", "answer"))
        self.write(first + second)
        result = self.extract("claude-code", limit_bytes=len(first) + 10)
        self.assertEqual(result["offset"], len(first))
        self.assertEqual(result["bytes_read"], len(first) + 10)
        resumed = self.extract("claude-code", offset=result["offset"])
        full = self.extract("claude-code")
        self.assertEqual(result["rows"] + resumed["rows"], full["rows"])
        self.assertLess(full["rows"][0]["sequence"], full["rows"][1]["sequence"])

    def test_oversized_content_drains_with_bounded_checkpoint_state(self):
        oversized = line(claude("user", "x" * (2 * 1048576)))
        following = line(claude("assistant", "answer"))
        self.write(oversized + following)
        result = self.extract("claude-code")
        self.assertEqual(result["offset"], 0)
        self.assertEqual(result["bytes_read"], 1048576)
        self.assertEqual(result["rows"], [])
        self.assertLess(len(json.dumps(result["state"])), 100)
        drained = self.extract("claude-code", offset=result["offset"], state=result["state"])
        self.assertEqual(drained["bytes_read"], 1048576)
        self.assertEqual(drained["offset"], 0)
        final = self.extract("claude-code", offset=drained["offset"], state=drained["state"])
        self.assertEqual(final["offset"], len(oversized + following))
        self.assertEqual([(r["role"], r["text"]) for r in final["rows"]],
                         [("assistant", "answer")])
        self.assertEqual(final["state"], {})

    def test_content_partial_and_malformed_complete_records(self):
        complete = line(claude("user", "first"))
        pending = line(claude("assistant", "pending"))
        self.write(complete + pending[:-1])
        result = self.extract("claude-code")
        self.assertEqual(result["offset"], len(complete))
        self.assertEqual(result["state"], {})
        self.write(b"\n", "ab")
        resumed = self.extract("claude-code", offset=result["offset"])
        self.assertEqual(resumed["rows"][0]["text"], "pending")
        self.write(b"{broken}\n", "ab")
        with self.assertRaises(ValueError):
            self.extract("claude-code", offset=resumed["offset"])

    def test_invalid_continuation_offset_raises(self):
        self.write(b"{}\n")
        for adapter in (claude_code, codex, pi):
            with self.subTest(tool=adapter.TOOL):
                with self.assertRaises(ValueError):
                    adapter.parse_incremental(self.path, 10, {}, {})


if __name__ == "__main__":
    unittest.main()
