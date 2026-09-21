#!/usr/bin/env python3
"""Offline data-evidence readiness gate (candidate implementation).

Pure Python standard library. Reads a preregister file (to obtain the
``required_evidence`` list with blocking flags) and a user-provided
evidence-declaration file, then reports whether every blocking item carries a
*formally complete* declaration.

Scope limits (per inputs/gate-spec.md):

* No network, no BRAIN field names, no platform access, no API guessing.
* The checker only validates the *form* of user-provided declarations. It
  cannot and does not verify that a declared source exists or is authentic.
* ``evidence_complete`` means "all blocking declarations are formally
  complete" -- it is NOT research readiness, NOT platform readiness, NOT a
  verified-return claim, and never permission to submit.
* delay=1 requires a trading calendar which is NOT implemented in this round.
  A ``verified`` declaration for E2_filing_availability_timestamps must cite
  calendar evidence under ``details.calendar``; without it the item stays
  ``unknown``. Calendar kinds that just add 24h or reuse the fiscal
  period-end as the publication date are rejected.
* A ``verified_at`` timestamp in the future is rejected: a future timestamp
  is not evidence that data was available.
* Fixtures used for testing must carry ``"synthetic": true``; the marker is
  propagated to the output so synthetic material can never be mistaken for
  real BRAIN readiness evidence.

Exit codes:
    0  verdict = evidence_complete
    1  verdict = blocked_unknown | blocked_unavailable (gate ran, gate fails)
    2  verdict = input_error (malformed input; no gate verdict produced)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.1.0"

VALID_STATUSES = ("unknown", "unavailable", "verified")

VERDICT_INPUT_ERROR = "input_error"
VERDICT_BLOCKED_UNKNOWN = "blocked_unknown"
VERDICT_BLOCKED_UNAVAILABLE = "blocked_unavailable"
VERDICT_EVIDENCE_COMPLETE = "evidence_complete"

EXIT_COMPLETE = 0
EXIT_BLOCKED = 1
EXIT_INPUT_ERROR = 2

# delay=1 needs a real trading calendar, which this round does not implement.
# A verified E2 declaration must therefore carry details.calendar, and the
# calendar kind may not be one of these placeholders.
FORBIDDEN_CALENDAR_KINDS = {
    "plus_24h",
    "period_end",
    "fiscal_period_end",
    "period_end_as_availability",
}

# Extra declaration fields required for a `verified` status on specific items.
EXTRA_REQUIREMENTS = {
    "E2_filing_availability_timestamps": ("calendar",),
}

BASE_CAVEATS = [
    "This checker validates only the formal completeness of user-provided "
    "evidence declarations; it cannot verify that a declared source exists "
    "or is authentic.",
    "evidence_complete means all blocking declarations are formally "
    "complete. It is NOT research readiness, NOT platform readiness, and "
    "NOT a verified-return claim.",
    "platform_ready and permission_granted are always false in this output.",
    "delay=1 trading-calendar logic is not implemented; a verified "
    "E2_filing_availability_timestamps declaration must cite calendar "
    "evidence under details.calendar or the item stays unknown. Future-dated "
    "verified_at is rejected.",
]

SYNTHETIC_CAVEAT = (
    "Input declared synthetic=true: this is test fixture material and is "
    "NOT real BRAIN readiness evidence."
)


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_timestamp(text):
    """Parse an ISO-8601 date or datetime; naive values are rejected.

    Returns a timezone-aware datetime, or None if unparseable.
    """
    if not _nonempty_str(text):
        return None
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def _check_calendar(decl):
    """Extra requirement for E2: details.calendar must cite real calendar evidence."""
    details = decl.get("details")
    cal = details.get("calendar") if isinstance(details, dict) else None
    if cal is None:
        return [
            "E2_filing_availability_timestamps requires details.calendar: "
            "delay=1 needs trading-calendar evidence (not implemented this "
            "round); without it the item remains unknown"
        ]
    if not isinstance(cal, dict):
        return ["details.calendar must be an object with kind and source"]
    problems = []
    kind = cal.get("kind")
    if not _nonempty_str(kind):
        problems.append("details.calendar.kind is required")
    elif kind.strip().lower() in FORBIDDEN_CALENDAR_KINDS:
        problems.append(
            "details.calendar.kind %r is not allowed: adding 24h or reusing "
            "the fiscal period-end is not a publication/availability date" % kind
        )
    if not _nonempty_str(cal.get("source")):
        problems.append("details.calendar.source is required")
    return problems


def validate_verified_declaration(decl, now):
    """Return a list of problems for a status=verified declaration (empty = ok)."""
    problems = []
    if not _nonempty_str(decl.get("source_ref")):
        problems.append(
            "verified status requires a non-empty source_ref (a locatable "
            "reference to the user-provided evidence)"
        )
    raw_ts = decl.get("verified_at")
    if not _nonempty_str(raw_ts):
        problems.append("verified status requires verified_at (ISO-8601)")
    else:
        parsed = parse_timestamp(raw_ts)
        if parsed is None:
            problems.append("verified_at %r is not a parseable ISO-8601 timestamp" % raw_ts)
        elif parsed > now:
            problems.append(
                "verified_at %r is in the future; a future timestamp is not "
                "evidence of data availability" % raw_ts
            )
    if not _nonempty_str(decl.get("note")):
        problems.append("verified status requires a non-empty note explaining the declaration")
    if "calendar" in EXTRA_REQUIREMENTS.get(decl.get("evidence_id"), ()):
        problems.extend(_check_calendar(decl))
    return problems


def _base_output(synthetic):
    caveats = list(BASE_CAVEATS)
    if synthetic:
        caveats.append(SYNTHETIC_CAVEAT)
    return {
        "tool": "evidence-gate-candidate",
        "version": VERSION,
        "synthetic": synthetic,
        # Hard-coded: this candidate never asserts platform readiness or any
        # permission, and cannot establish research readiness from
        # declarations alone.
        "platform_ready": False,
        "permission_granted": False,
        "research_ready": False,
        "caveats": caveats,
    }


def _input_error_output(errors, synthetic, now):
    out = _base_output(synthetic)
    out.update(
        {
            "format_ok": False,
            "verdict": VERDICT_INPUT_ERROR,
            "evaluated_at": now.isoformat(),
            "input_errors": errors,
        }
    )
    return out


def evaluate(spec_items, payload, now):
    """Evaluate a parsed declaration payload against required_evidence spec.

    Returns (output_dict, exit_code).
    """
    if (not isinstance(spec_items, list) or not spec_items
            or not all(isinstance(i, dict) and _nonempty_str(i.get('id'))
                       and isinstance(i.get('blocking'), bool) for i in spec_items)
            or len({i['id'] for i in spec_items}) != len(spec_items)
            or not any(i['blocking'] for i in spec_items)):
        return _input_error_output(['required_evidence 必须含不重复ID、布尔blocking及至少一个阻断项'], False, now), EXIT_INPUT_ERROR
    spec_ids = [item["id"] for item in spec_items]
    blocking_ids = [item["id"] for item in spec_items if item.get("blocking")]
    non_blocking_ids = [i for i in spec_ids if i not in blocking_ids]

    if not isinstance(payload, dict):
        return _input_error_output(['declarations must be an object'], False, now), EXIT_INPUT_ERROR
    synthetic_raw = payload.get("synthetic")
    if not isinstance(synthetic_raw, bool):
        return _input_error_output(
            ["'synthetic' must be a boolean when present"], False, now
        ), EXIT_INPUT_ERROR
    synthetic = synthetic_raw

    decls = payload.get("declarations")
    errors = []
    declared = {}
    if not isinstance(decls, list):
        errors.append("'declarations' must be a list")
    else:
        for i, d in enumerate(decls):
            if not isinstance(d, dict):
                errors.append("declarations[%d] is not an object" % i)
                continue
            eid = d.get("evidence_id")
            if not _nonempty_str(eid):
                errors.append("declarations[%d].evidence_id missing or not a string" % i)
                continue
            if eid not in spec_ids:
                errors.append("unknown evidence_id %r (not in required_evidence)" % eid)
                continue
            if eid in declared:
                errors.append("duplicate evidence_id %r" % eid)
                continue
            status = d.get("status")
            if status not in VALID_STATUSES:
                errors.append(
                    "declarations[%d] %r has invalid status %r; allowed: %s"
                    % (i, eid, status, "/".join(VALID_STATUSES))
                )
                continue
            declared[eid] = d

    if errors:
        return _input_error_output(errors, synthetic, now), EXIT_INPUT_ERROR

    items = []
    for eid in spec_ids:
        blocking = eid in blocking_ids
        d = declared.get(eid)
        report = {"evidence_id": eid, "blocking": blocking}
        if d is None:
            report.update(
                {
                    "declared_status": None,
                    "missing": True,
                    "effective_status": "unknown",
                    "valid": False,
                    "problems": ["no declaration provided for this item"],
                }
            )
        else:
            problems = []
            if d.get("status") in ("verified", "unavailable"):
                # “确实不可得”也必须有来源，否则只能算未知。
                check = d if d.get('status') == 'verified' else {**d, 'evidence_id': ''}
                problems = validate_verified_declaration(check, now)
            effective = d["status"] if not problems else "unknown"
            report.update(
                {
                    "declared_status": d["status"],
                    "missing": False,
                    "effective_status": effective,
                    "valid": not problems,
                    "problems": problems,
                }
            )
        items.append(report)

    blocking_reports = [r for r in items if r["blocking"]]
    non_blocking_reports = [r for r in items if not r["blocking"]]
    unknown = [r["evidence_id"] for r in blocking_reports if r["effective_status"] == "unknown"]
    unavailable = [
        r["evidence_id"] for r in blocking_reports if r["effective_status"] == "unavailable"
    ]
    verified = [r["evidence_id"] for r in blocking_reports if r["effective_status"] == "verified"]

    # Precedence: an explicit unavailable is a definitive block and dominates
    # unknown; otherwise any unknown/missing/invalid blocking item blocks as
    # unknown; only all-verified blocking yields evidence_complete.
    if unavailable:
        verdict = VERDICT_BLOCKED_UNAVAILABLE
        exit_code = EXIT_BLOCKED
    elif unknown:
        verdict = VERDICT_BLOCKED_UNKNOWN
        exit_code = EXIT_BLOCKED
    else:
        verdict = VERDICT_EVIDENCE_COMPLETE
        exit_code = EXIT_COMPLETE

    non_blocking_gaps = [
        {
            "evidence_id": r["evidence_id"],
            "declared_status": r["declared_status"],
            "effective_status": r["effective_status"],
            "problems": r["problems"],
        }
        for r in non_blocking_reports
        if r["effective_status"] != "verified"
    ]

    out = _base_output(synthetic)
    out.update(
        {
            "format_ok": True,
            "verdict": verdict,
            "evaluated_at": now.isoformat(),
            "blocking": {
                "required": blocking_ids,
                "verified": verified,
                "unknown": unknown,
                "unavailable": unavailable,
                "items": blocking_reports,
            },
            "non_blocking": {
                "required": non_blocking_ids,
                "gaps": non_blocking_gaps,
                "note": "Non-blocking gaps are reported separately; they do "
                "not change the verdict and are not counted as passed.",
                "items": non_blocking_reports,
            },
            "input_errors": [],
        }
    )
    return out, exit_code


def _load_json(path):
    """Return (parsed, error_string)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return None, "cannot read %s: %s" % (path, exc)
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "invalid JSON in %s: %s" % (path, exc)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline data-evidence readiness gate (candidate). "
        "Checks formal completeness of user-provided evidence declarations "
        "against preregister.required_evidence. Never outputs "
        "platform_ready=true or permission_granted."
    )
    parser.add_argument(
        "--preregister",
        required=True,
        help="Path to preregister JSON containing required_evidence.",
    )
    parser.add_argument(
        "--declarations",
        required=True,
        help="Path to evidence-declaration JSON (fixtures must use synthetic=true).",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-8601 reference time used to reject future verified_at "
        "timestamps (default: current UTC time).",
    )
    args = parser.parse_args(argv)

    if args.now is None:
        now = datetime.now(timezone.utc)
    else:
        now = parse_timestamp(args.now)
        if now is None:
            print("--now %r is not a parseable ISO-8601 timestamp" % args.now, file=sys.stderr)
            return EXIT_INPUT_ERROR

    preregister, err = _load_json(args.preregister)
    if err:
        print(json.dumps(_input_error_output([err], False, now), indent=2, ensure_ascii=False))
        return EXIT_INPUT_ERROR
    spec_items = preregister.get("required_evidence") if isinstance(preregister, dict) else None
    if not isinstance(spec_items, list) or not all(
        isinstance(it, dict) and _nonempty_str(it.get("id")) for it in spec_items
    ):
        print(
            json.dumps(
                _input_error_output(
                    ["preregister file lacks a valid required_evidence list"],
                    False,
                    now,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
        return EXIT_INPUT_ERROR

    payload, err = _load_json(args.declarations)
    if err:
        print(json.dumps(_input_error_output([err], False, now), indent=2, ensure_ascii=False))
        return EXIT_INPUT_ERROR
    if not isinstance(payload, dict):
        print(
            json.dumps(
                _input_error_output(["declarations file must be a JSON object"], False, now),
                indent=2,
                ensure_ascii=False,
            )
        )
        return EXIT_INPUT_ERROR

    out, code = evaluate(spec_items, payload, now)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
