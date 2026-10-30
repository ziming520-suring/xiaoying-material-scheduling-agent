"""Pure validation and stable serialization; this module never opens storage."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

OPERATIONS = {"system.initialize": "initial_time", "clock.advance": "target_time"}
OPERATIONS.update({op: None for op in ('schema.upgrade', 'fixture.load', 'task.create',
                                     'draft.save', 'draft.confirm', 'calendar.replace')})
ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")
TIME_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|[+-][0-9]{2}:[0-9]{2})")
Response = dict[str, Any]


class RequestError(Exception):
    def __init__(self, code: str, message: str, field: str | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.field, self.retryable = code, message, field, retryable
        self.request_id: str | None = None
        self.operation: str | None = None


@dataclass(frozen=True)
class ValidatedRequest:
    request_id: str
    operation: str
    time_utc: str | None = None
    payload: dict | None = None


@dataclass(frozen=True)
class CanonicalRequest:
    request: ValidatedRequest
    canonical_json: str
    digest: str


def serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def normalize_time(value: Any, field: str) -> str:
    if not isinstance(value, str) or not TIME_PATTERN.fullmatch(value):
        raise RequestError("INVALID_REQUEST", "Time must include date, seconds and timezone.", field)
    try:
        if not value.endswith("Z"):
            hours, minutes = int(value[-5:-3]), int(value[-2:])
            if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
                raise ValueError("invalid offset")
        instant = datetime.fromisoformat(value).astimezone(timezone.utc)
        return instant.isoformat(timespec="seconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise RequestError("INVALID_REQUEST", "Time is invalid or outside the supported range.", field) from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonstandard JSON number")


def parse_json_object(raw_json: str) -> dict:
    try:
        if not isinstance(raw_json, str):
            raise ValueError("text required")
        value = json.loads(raw_json, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise RequestError("INVALID_REQUEST", "Input must be one valid JSON object with unique keys.") from None
    if not isinstance(value, dict):
        raise RequestError("INVALID_REQUEST", "Input must be a JSON object.")
    return value


def parse_and_validate(raw_json: str) -> ValidatedRequest:
    value = parse_json_object(raw_json)
    rid = value.get("request_id")
    rid = rid if isinstance(rid, str) and ID_PATTERN.fullmatch(rid) else None
    op = value.get("operation")
    op = op if isinstance(op, str) and op in OPERATIONS else None
    try:
        if set(value) != {"schema_version", "request_id", "operation", "payload"}:
            raise RequestError("INVALID_REQUEST", "Request has missing or unknown fields.")
        if type(value["schema_version"]) is not int:
            raise RequestError("INVALID_REQUEST", "Version must be an integer.", "schema_version")
        if value["schema_version"] != 1:
            raise RequestError("UNSUPPORTED_VERSION", "Only schema version 1 is supported.", "schema_version")
        if rid is None:
            raise RequestError("INVALID_REQUEST", "Invalid request ID.", "request_id")
        if not isinstance(value["operation"], str):
            raise RequestError("INVALID_REQUEST", "Operation must be a string.", "operation")
        if op is None:
            raise RequestError("UNKNOWN_OPERATION", "Operation is not registered.", "operation")
        field = OPERATIONS[op]
        if field is None:
            from .business_validation import validate_payload
            return ValidatedRequest(rid, op, payload=validate_payload(op, value['payload']))
        if not isinstance(value["payload"], dict) or set(value["payload"]) != {field}:
            raise RequestError("INVALID_REQUEST", "Payload has missing or unknown fields.", "payload")
        return ValidatedRequest(rid, op, normalize_time(value["payload"][field], "payload." + field))
    except RequestError as exc:
        exc.request_id, exc.operation = rid, op
        raise


def canonicalize(request: ValidatedRequest) -> CanonicalRequest:
    content = serialize({"schema_version": 1, "operation": request.operation,
                         "payload": request_payload(request)})
    return CanonicalRequest(request, content, hashlib.sha256(content.encode("utf-8")).hexdigest())


def request_payload(request: ValidatedRequest) -> dict:
    field = OPERATIONS.get(request.operation)
    return {field: request.time_utc} if field else request.payload


def success_response(request_id: str | None, operation: str, data: dict) -> Response:
    return {"schema_version": 1, "request_id": request_id, "operation": operation,
            "status": "succeeded", "data": data, "error": None}


def error_response(error: RequestError, request: ValidatedRequest | None = None,
                   *, operation: str | None = None) -> Response:
    return {"schema_version": 1,
            "request_id": request.request_id if request else error.request_id,
            "operation": request.operation if request else (operation or error.operation),
            "status": "unknown" if error.code == "OUTCOME_UNKNOWN" else "failed",
            "data": None,
            "error": {"code": error.code, "message": error.message,
                      "field": error.field, "retryable": error.retryable}}


def validate_stored_success(raw_json: str, request: ValidatedRequest) -> Response:
    """Storage is a boundary too: reject malformed results instead of replaying them."""
    try:
        response = parse_json_object(raw_json)
        if (set(response) != {"schema_version", "request_id", "operation", "status", "data", "error"}
                or type(response["schema_version"]) is not int or response["schema_version"] != 1
                or response["request_id"] != request.request_id or response["operation"] != request.operation
                or response["status"] != "succeeded" or response["error"] is not None):
            raise ValueError("invalid envelope")
        data = response["data"]
        if OPERATIONS[request.operation] is None:
            from .business_validation import validate_result
            validate_result(request.operation, data, request.payload, request.request_id)
            return response
        fields = {"current_time", "clock_version"}
        if request.operation == "clock.advance":
            fields |= {"previous_time", "settled_count"}
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("invalid result fields")
        if (type(data["clock_version"]) is not int or data["clock_version"] < 1
                or normalize_time(data["current_time"], "current_time") != data["current_time"]
                or data["current_time"] != request.time_utc):
            raise ValueError("invalid time or version")
        if request.operation == "system.initialize":
            if data["clock_version"] != 1:
                raise ValueError("invalid initialization version")
        else:
            previous = data["previous_time"]
            if (normalize_time(previous, "previous_time") != previous or previous > request.time_utc
                    or type(data["settled_count"]) is not int or data["settled_count"] < 0
                    or (previous == request.time_utc and data["settled_count"] != 0)
                    or (previous < request.time_utc and data["clock_version"] < 2)):
                raise ValueError("inconsistent advancement")
        return response
    except (RequestError, ValueError, TypeError, KeyError):
        raise RequestError("STORAGE_ERROR", "Stored replay result is invalid.", retryable=True) from None
