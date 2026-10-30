"""Contract tests: no database is needed for pure validation."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from schedule_agent.contracts import parse_and_validate, canonicalize, RequestError
except ModuleNotFoundError:
    parse_and_validate = None


def request(time="2026-10-28T18:00:00+08:00", **changes):
    value = {"schema_version": 1, "request_id": "req-1", "operation": "clock.advance",
             "payload": {"target_time": time}}
    value.update(changes)
    return json.dumps(value, ensure_ascii=False)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(parse_and_validate, "request validation is not implemented")

    def reject(self, raw, code="INVALID_REQUEST"):
        with self.assertRaises(RequestError) as caught:
            parse_and_validate(raw)
        self.assertEqual(caught.exception.code, code)

    def test_equivalent_time_and_key_order_have_same_identity(self):
        first = canonicalize(parse_and_validate(request()))
        second = canonicalize(parse_and_validate(
            '{"payload":{"target_time":"2026-10-28T10:00:00Z"},'
            '"operation":"clock.advance","request_id":"req-1","schema_version":1}'))
        self.assertEqual(first.canonical_json, second.canonical_json)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(json.loads(first.canonical_json), {
            "schema_version": 1, "operation": "clock.advance",
            "payload": {"target_time": "2026-10-28T10:00:00Z"}})

    def test_identity_excludes_key_but_includes_parameters_and_operation(self):
        a = canonicalize(parse_and_validate(request()))
        b = canonicalize(parse_and_validate(request(request_id="other")))
        c = canonicalize(parse_and_validate(request(time="2026-10-28T11:00:00Z")))
        self.assertEqual(a.digest, b.digest)
        self.assertNotEqual(a.digest, c.digest)

    def test_invalid_envelopes(self):
        values = ["", "[]", "null", "true", "{}", "{", "{} {}",
                  request(extra=1), request(request_id=""), request(request_id="a b"),
                  request(request_id="中文"), request(request_id="a" * 129),
                  request(request_id=None), request(schema_version=True),
                  request(schema_version=1.0), request(payload=[]),
                  request(payload={"target_time": "2026-10-28T10:00:00Z", "extra": 1}),
                  request(payload={}), request(time=None), request(time=123)]
        for raw in values:
            with self.subTest(raw=raw):
                self.reject(raw)

    def test_rejects_duplicate_keys_at_any_depth(self):
        self.reject(request().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
        self.reject(request().replace('"target_time":', '"target_time":"2026-10-28T10:00:00Z", "target_time":'))

    def test_unknown_version_and_operation(self):
        self.reject(request(schema_version=2), "UNSUPPORTED_VERSION")
        self.reject(request(operation="publish"), "UNKNOWN_OPERATION")

    def test_invalid_times(self):
        times = ["18:00", "2026-10-28T10:00:00", "2026-10-28T10:00:00.1Z",
                 "2026-10-28 10:00:00Z", "2026-10-28T10:00:00z",
                 "2026-02-30T10:00:00Z", "2026-10-28T24:00:00Z",
                 "2026-10-28T10:00:60Z", "2026-10-28T10:00:00+14:01",
                 "2026-10-28T10:00:00+15:00", "2026-10-28T10:00:00+01:60",
                 "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00",
                 "2026-10-28T10:00:00Z\n", " 2026-10-28T10:00:00Z"]
        for value in times:
            with self.subTest(time=value):
                self.reject(request(time=value))

    def test_time_extremes_are_zero_padded_and_normalized(self):
        for value, expected in [
            ("0001-01-01T00:00:00Z", "0001-01-01T00:00:00Z"),
            ("9999-12-31T23:59:59Z", "9999-12-31T23:59:59Z"),
            ("2024-02-29T00:00:00+14:00", "2024-02-28T10:00:00Z"),
        ]:
            with self.subTest(time=value):
                self.assertEqual(parse_and_validate(request(time=value)).time_utc, expected)

    def test_nan_infinity_and_deep_json_return_validation_error(self):
        for raw in [request().replace('"schema_version": 1', '"schema_version": NaN'),
                    request().replace('"schema_version": 1', '"schema_version": Infinity'),
                    '[' * 2000 + '0' + ']' * 2000]:
            self.reject(raw)


if __name__ == "__main__":
    unittest.main()
