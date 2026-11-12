"""Validate a SHA-bound, completed creature transition review for batch QA.

This is a completeness and source-binding check. It does not infer animation
semantics or grant art approval; visual judgments remain explicitly recorded.
"""
from __future__ import annotations

from typing import Any

SCHEMA = "h3.creature.motion-review.v1"
CHECKS = (
    "identity_continuity",
    "anatomy_continuity",
    "equipment_continuity",
    "color_material_continuity",
    "action_logic",
)
DECISIONS = {"PASS", "RISK", "FAIL", "NOT_MEASURED"}


def evaluate(review: dict[str, Any] | None, identity: str, inventory_sha256: str,
             native_slots: list[dict[str, Any]], selected_sources: dict[tuple, dict[str, Any]]) -> dict[str, Any]:
    """Compare a completed motion review to the current frozen slot/source set."""
    if review is None:
        return {"status": "NOT_PROVIDED", "groups": [], "summary": {"expected_groups": 0,
            "complete_groups": 0, "incomplete_groups": 0, "risk_groups": 0, "failed_groups": 0},
            "automatic_art_approval": False}

    errors: list[str] = []
    if review.get("schema") != SCHEMA:
        errors.append("MOTION_REVIEW_SCHEMA_INVALID")
    if review.get("identity") != identity:
        errors.append("MOTION_REVIEW_IDENTITY_MISMATCH")
    if review.get("native_inventory_sha256") != inventory_sha256:
        errors.append("MOTION_REVIEW_INVENTORY_MISMATCH")

    official_by_group: dict[int, list[tuple]] = {}
    for slot in native_slots:
        key = tuple(slot["key"])
        official_by_group.setdefault(key[2], []).append(key)
    current_by_group: dict[int, list[tuple]] = {}
    for key in official_by_group:
        present = [k for k in official_by_group[key] if selected_sources.get(k, {}).get("image") is not None]
        if len(present) >= 2:
            current_by_group[key] = sorted(present, key=lambda k: k[3])

    review_groups = review.get("groups")
    if not isinstance(review_groups, list):
        review_groups = []
        errors.append("MOTION_REVIEW_GROUPS_INVALID")
    seen: set[int] = set()
    results: list[dict[str, Any]] = []
    for group in review_groups:
        if not isinstance(group, dict) or type(group.get("group")) is not int:
            errors.append("MOTION_REVIEW_GROUP_INVALID")
            continue
        number = group["group"]
        if number in seen:
            errors.append(f"MOTION_REVIEW_DUPLICATE_GROUP:{number}")
            continue
        seen.add(number)
        rows = current_by_group.get(number, [])
        keys = group.get("ordered_keys")
        expected_keys = [list(k) for k in rows]
        group_errors: list[str] = []
        if not rows or keys != expected_keys:
            group_errors.append("OFFICIAL_SLOT_COVERAGE_MISMATCH")

        frame_rows = group.get("frames")
        expected_frames = []
        for key in rows:
            image = selected_sources[key]["image"]
            expected_frames.append({"key": list(key), "image_sha256": image["sha256"]})
        if frame_rows != expected_frames:
            group_errors.append("FRAME_SOURCE_SHA_MISMATCH")

        expected_edges = list(zip(rows, rows[1:]))
        transitions = group.get("transitions")
        if not isinstance(transitions, list) or len(transitions) != len(expected_edges):
            group_errors.append("TRANSITION_COVERAGE_MISMATCH")
            transitions = transitions if isinstance(transitions, list) else []
        transition_results = []
        for index, edge in enumerate(transitions):
            edge_errors: list[str] = []
            if not isinstance(edge, dict) or index >= len(expected_edges):
                edge_errors.append("UNEXPECTED_TRANSITION")
                checks = {}
            else:
                left, right = expected_edges[index]
                if edge.get("from_key") != list(left) or edge.get("to_key") != list(right):
                    edge_errors.append("TRANSITION_ORDER_MISMATCH")
                left_image = selected_sources.get(left, {}).get("image")
                right_image = selected_sources.get(right, {}).get("image")
                if (left_image is None or right_image is None
                        or edge.get("from_image_sha256") != left_image["sha256"]
                        or edge.get("to_image_sha256") != right_image["sha256"]):
                    edge_errors.append("TRANSITION_SOURCE_SHA_MISMATCH")
                checks = edge.get("checks") if isinstance(edge.get("checks"), dict) else {}
            missing_checks = [name for name in CHECKS if checks.get(name) not in DECISIONS - {"NOT_MEASURED"}]
            if missing_checks:
                edge_errors.append("CHECKS_INCOMPLETE:" + ",".join(missing_checks))
            notes = edge.get("notes", "") if isinstance(edge, dict) else ""
            if any(checks.get(name) in {"RISK", "FAIL"} for name in CHECKS) and not (isinstance(notes, str) and notes.strip()):
                edge_errors.append("RISK_OR_FAIL_REQUIRES_NOTES")
            transition_results.append({"index": index, "from_key": edge.get("from_key") if isinstance(edge, dict) else None,
                "to_key": edge.get("to_key") if isinstance(edge, dict) else None, "checks": checks,
                "status": "FAIL" if any(checks.get(name) == "FAIL" for name in CHECKS) else
                          "RISK" if any(checks.get(name) == "RISK" for name in CHECKS) else
                          "PASS" if not edge_errors else "INCOMPLETE",
                "errors": edge_errors})

        loop_mode = group.get("loop_mode")
        loop = group.get("loop_transition")
        loop_checks: dict[str, Any] = {}
        loop_errors: list[str] = []
        if loop_mode not in {"loop", "non_loop"}:
            group_errors.append("LOOP_MODE_NOT_CLASSIFIED")
            loop_result = {"status": "NOT_MEASURED", "reason": "LOOP_MODE_NOT_CLASSIFIED",
                           "input_present": isinstance(loop, dict)}
        elif loop_mode == "loop":
            if not isinstance(loop, dict):
                loop_errors.append("LOOP_TRANSITION_MISSING")
            elif rows:
                left, right = rows[-1], rows[0]
                if loop.get("from_key") != list(left) or loop.get("to_key") != list(right):
                    loop_errors.append("LOOP_TRANSITION_ORDER_MISMATCH")
                left_image = selected_sources.get(left, {}).get("image")
                right_image = selected_sources.get(right, {}).get("image")
                if (left_image is None or right_image is None
                        or loop.get("from_image_sha256") != left_image["sha256"]
                        or loop.get("to_image_sha256") != right_image["sha256"]):
                    loop_errors.append("LOOP_TRANSITION_SOURCE_SHA_MISMATCH")
                loop_checks = loop.get("checks") if isinstance(loop.get("checks"), dict) else {}
                missing_checks = [name for name in CHECKS if loop_checks.get(name) not in DECISIONS - {"NOT_MEASURED"}]
                if missing_checks:
                    loop_errors.append("LOOP_CHECKS_INCOMPLETE:" + ",".join(missing_checks))
                notes = loop.get("notes", "")
                if any(loop_checks.get(name) in {"RISK", "FAIL"} for name in CHECKS) and not (isinstance(notes, str) and notes.strip()):
                    loop_errors.append("LOOP_RISK_OR_FAIL_REQUIRES_NOTES")
            else:
                loop_errors.append("LOOP_SLOTS_MISSING")
            group_errors.extend(loop_errors)
            loop_result = {"status": "FAIL" if any(loop_checks.get(name) == "FAIL" for name in CHECKS) else
                           "RISK" if any(loop_checks.get(name) == "RISK" for name in CHECKS) else
                           "PASS" if not loop_errors else "INCOMPLETE",
                           "from_key": loop.get("from_key") if isinstance(loop, dict) else None,
                           "to_key": loop.get("to_key") if isinstance(loop, dict) else None,
                           "checks": loop_checks, "errors": loop_errors}
        else:
            # A non-loop action ends at its last frame; it has no last-to-first edge.
            # Keep any legacy/template edge visible for audit, but do not score it.
            loop_result = {"status": "NOT_APPLICABLE", "reason": "GROUP_MODE_NON_LOOP",
                           "input_present": isinstance(loop, dict)}
            if isinstance(loop, dict):
                loop_result["supplied_but_not_applicable"] = True
                loop_result["supplied_review"] = {"from_key": loop.get("from_key"),
                    "to_key": loop.get("to_key"), "from_image_sha256": loop.get("from_image_sha256"),
                    "to_image_sha256": loop.get("to_image_sha256"),
                    "checks": loop.get("checks") if isinstance(loop.get("checks"), dict) else {},
                    "notes": loop.get("notes", "")}
        author = review.get("review_author")
        if not isinstance(author, str) or not author.strip():
            group_errors.append("REVIEW_AUTHOR_MISSING")
        group_errors.extend(group_errors_from_edges(transition_results))
        all_checks = [edge["checks"] for edge in transition_results]
        if loop_mode == "loop" and loop_checks:
            all_checks.append(loop_checks)
        has_fail = any(c.get(name) == "FAIL" for c in all_checks for name in CHECKS)
        has_risk = any(c.get(name) == "RISK" for c in all_checks for name in CHECKS)
        source_errors = [e for e in group_errors if "SHA_MISMATCH" in e or "ORDER_MISMATCH" in e]
        status = "SOURCE_BINDING_INVALID" if source_errors else "INCOMPLETE" if group_errors else \
                 "REVIEWED_WITH_FAIL" if has_fail else "REVIEWED_WITH_RISK" if has_risk else "REVIEW_COMPLETE"
        results.append({"group": number, "status": status, "loop_mode": loop_mode,
            "review_author": author,
            "transition_count": len(expected_edges) + (1 if loop_mode == "loop" else 0),
            "transitions": transition_results, "loop_transition": loop_result,
            "errors": sorted(set(group_errors))})

    missing_groups = sorted(set(current_by_group) - seen)
    if missing_groups:
        errors.extend(f"MOTION_REVIEW_GROUP_MISSING:{g}" for g in missing_groups)
    if not current_by_group:
        errors.append("NO_REVIEWABLE_ACTION_GROUPS")
    if len(current_by_group) != len(official_by_group):
        # Single-frame/no-source groups do not contain a measurable transition.
        source_groups = {g for g, keys in official_by_group.items() if len(keys) >= 2}
        absent_source_groups = sorted(source_groups - set(current_by_group))
        errors.extend(f"SOURCE_COVERAGE_INCOMPLETE:{g}" for g in absent_source_groups)
    errors = sorted(set(errors))
    complete = sum(r["status"] == "REVIEW_COMPLETE" for r in results)
    risk = sum(r["status"] == "REVIEWED_WITH_RISK" for r in results)
    failed = sum(r["status"] == "REVIEWED_WITH_FAIL" for r in results)
    incomplete = sum(r["status"] == "INCOMPLETE" for r in results) + len(missing_groups)
    status = "SOURCE_BINDING_INVALID" if any("MISMATCH" in e or "INVALID" in e for e in errors) or \
             any(r["status"] == "SOURCE_BINDING_INVALID" for r in results) else \
             "INCOMPLETE" if incomplete or errors else "REVIEWED_WITH_FAIL" if failed else \
             "REVIEWED_WITH_RISK" if risk else "REVIEW_COMPLETE"
    return {"schema": "h3.creature.motion-postcheck.v1", "status": status,
        "groups": results, "errors": errors,
        "summary": {"expected_groups": len(current_by_group), "complete_groups": complete,
            "incomplete_groups": incomplete, "risk_groups": risk, "failed_groups": failed,
            "transitions": sum(r["transition_count"] for r in results)},
        "automatic_art_approval": False, "automatic_resize": False,
        "semantic_scope": "SHA-bound review completeness and recorded judgments; no visual AI inference"}


def group_errors_from_edges(edges: list[dict[str, Any]]) -> list[str]:
    return [f"TRANSITION_{edge['index']}_{error}" for edge in edges for error in edge["errors"]]
