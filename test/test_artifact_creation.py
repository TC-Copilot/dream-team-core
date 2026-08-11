#!/usr/bin/env python3
"""Targeted tests for the document/deck creation workflow (app/app.py).

Covers three things:
1. looks_like_artifact_creation_request: detects a request to CREATE a new document/deck (e.g.
   "build a proposal deck for the Contoso renewal"), distinct from a document-backed draft request
   that references an EXISTING/prior document (looks_like_document_backed_draft_request). The two
   detectors must never both fire on the same message, since /api/chat checks artifact-creation
   first and only falls back to the document-backed-draft check with elif.
2. artifact_creation_next_hop: Major's active routing decision through Casey (conditional, only
   when Drew flagged a need for confirmed context) -> Drew (evidence + package + creation) -> Mina
   (pptx storyboard/speaker notes only) -> Riley (cover note) -> Quinn (validation) -> Major.
3. validate_artifact_creation_completion: the two-mode completion gate. A 'created' claim (the
   primary path: a real .docx/.pptx draft in the permitted workspace) must carry a non-empty file
   link. A 'copilot_prompt_fallback' claim (used when direct creation is unavailable) must carry a
   non-empty build prompt. Neither mode can silently claim 'completed' without that evidence -- the
   worker's claim is forced to 'blocked' instead, mirroring validate_document_backed_completion.

Run directly: `python test/test_artifact_creation.py`.
"""
from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        print(f"[ok] {name}")
        return True
    print(f"[FAIL] {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    ok = True

    # --- looks_like_artifact_creation_request -------------------------------------------------
    detect = appmod.looks_like_artifact_creation_request
    ok &= check(
        "detects a new deck creation request",
        detect("Can you build a proposal deck for the Contoso renewal?"),
        True,
    )
    ok &= check(
        "detects a new document creation request",
        detect("Please put together a one-pager document on Q3 results"),
        True,
    )
    ok &= check(
        "detects 'create' + presentation",
        detect("Create a presentation summarizing the roadmap"),
        True,
    )
    ok &= check(
        "does not fire on a plain create request with no artifact type",
        detect("Create a reminder for tomorrow"),
        False,
    )
    ok &= check(
        "does not fire on an artifact type with no creation intent",
        detect("Where is the document I looked at yesterday?"),
        False,
    )
    ok &= check("empty message does not fire", detect(""), False)
    # Overlap guard: a document-backed-draft phrase ("put the Cowork doc ... into a draft email")
    # must NOT be detected as an artifact-creation request, since 'draft' is deliberately excluded
    # from the creation-intent verbs (it only signals drafting around an EXISTING document here).
    ok &= check(
        "document-backed-draft phrasing is not mistaken for artifact creation",
        detect("Put the Cowork doc I made before the meeting with Heather into a draft email"),
        False,
    )
    # And the reverse should still hold: that same phrase is picked up by the OTHER detector.
    ok &= check(
        "the same phrase is still a document-backed-draft request",
        appmod.looks_like_document_backed_draft_request(
            "Put the Cowork doc I made before the meeting with Heather into a draft email"
        ),
        True,
    )

    # --- artifact_creation_next_hop ------------------------------------------------------------
    next_hop = appmod.artifact_creation_next_hop
    ok &= check("next_hop tolerates a None job (defensive)", next_hop(None), "Drew")

    # No creation mode yet -> Drew (default path, no context flagged).
    ok &= check(
        "no creationMode yet -> routes to Drew",
        next_hop({"artifact_needs_context": 0, "knowledge_links_json": "", "artifact_creation_mode": "",
                  "artifact_type": "", "narrative_reviewed": 0, "cover_note_composed": 0, "quality_verdict": ""}),
        "Drew",
    )

    # Drew flagged a need for confirmed context, Casey hasn't reported yet -> Casey.
    ok &= check(
        "artifactNeedsContext with no knowledgeLinks yet -> routes to Casey",
        next_hop({"artifact_needs_context": 1, "knowledge_links_json": "", "artifact_creation_mode": "",
                  "artifact_type": "", "narrative_reviewed": 0, "cover_note_composed": 0, "quality_verdict": ""}),
        "Casey",
    )

    # Casey has reported knowledge links -> back to Drew to finish the package.
    ok &= check(
        "artifactNeedsContext satisfied by knowledgeLinks -> back to Drew",
        next_hop({"artifact_needs_context": 1, "knowledge_links_json": '["person:1"]', "artifact_creation_mode": "",
                  "artifact_type": "", "narrative_reviewed": 0, "cover_note_composed": 0, "quality_verdict": ""}),
        "Drew",
    )

    # docx artifact, creationMode reported, cover note not yet composed -> Riley (Mina skipped).
    ok &= check(
        "docx artifact created, no cover note yet -> routes to Riley (Mina skipped)",
        next_hop({"artifact_needs_context": 0, "knowledge_links_json": "", "artifact_creation_mode": "created",
                  "artifact_type": "docx", "narrative_reviewed": 0, "cover_note_composed": 0, "quality_verdict": ""}),
        "Riley",
    )

    # pptx artifact, creationMode reported, narrative not yet reviewed -> Mina.
    ok &= check(
        "pptx artifact created, narrative not reviewed yet -> routes to Mina",
        next_hop({"artifact_needs_context": 0, "knowledge_links_json": "", "artifact_creation_mode": "created",
                  "artifact_type": "pptx", "narrative_reviewed": 0, "cover_note_composed": 0, "quality_verdict": ""}),
        "Mina",
    )

    # pptx artifact, narrative reviewed, cover note not composed -> Riley.
    ok &= check(
        "pptx artifact, narrative reviewed, no cover note yet -> routes to Riley",
        next_hop({"artifact_needs_context": 0, "knowledge_links_json": "", "artifact_creation_mode": "created",
                  "artifact_type": "pptx", "narrative_reviewed": 1, "cover_note_composed": 0, "quality_verdict": ""}),
        "Riley",
    )

    # Cover note composed, no quality verdict yet -> Quinn.
    ok &= check(
        "cover note composed, no quality verdict yet -> routes to Quinn",
        next_hop({"artifact_needs_context": 0, "knowledge_links_json": "", "artifact_creation_mode": "created",
                  "artifact_type": "docx", "narrative_reviewed": 0, "cover_note_composed": 1, "quality_verdict": ""}),
        "Quinn",
    )

    # Quality verdict in -> back to Major (done).
    ok &= check(
        "quality verdict in -> routes back to Major (done)",
        next_hop({"artifact_needs_context": 0, "knowledge_links_json": "", "artifact_creation_mode": "created",
                  "artifact_type": "docx", "narrative_reviewed": 0, "cover_note_composed": 1, "quality_verdict": "pass"}),
        "Major",
    )

    # Fallback creation mode counts as Drew's leg being done, same as 'created'.
    ok &= check(
        "copilot_prompt_fallback also satisfies Drew's leg -> routes to Riley",
        next_hop({"artifact_needs_context": 0, "knowledge_links_json": "", "artifact_creation_mode": "copilot_prompt_fallback",
                  "artifact_type": "docx", "narrative_reviewed": 0, "cover_note_composed": 0, "quality_verdict": ""}),
        "Riley",
    )

    # --- validate_artifact_creation_completion -------------------------------------------------
    fn = appmod.validate_artifact_creation_completion

    # created + real link -> stays completed, no override.
    ok &= check(
        "created with link is not overridden",
        fn({"creationMode": "created", "link": "/api/documents/roi-deck.pptx"}, "completed"),
        None,
    )

    # created but no link at all -> fabrication guard fires.
    result = fn({"creationMode": "created"}, "completed")
    ok &= check("created without link overrides status", result[0] if result else None, "blocked")
    ok &= check("created without link blocker mentions no file link",
                "no file link" in (result[1] if result else ""), True)

    # created but link is blank/whitespace -> still guarded.
    result = fn({"creationMode": "created", "link": "   "}, "completed")
    ok &= check("created with blank link overrides status", result[0] if result else None, "blocked")

    # copilot_prompt_fallback with a real prompt in artifactPackage -> not overridden.
    ok &= check(
        "copilot_prompt_fallback with a prompt is not overridden",
        fn(
            {
                "creationMode": "copilot_prompt_fallback",
                "artifactPackage": {"copilotPrompt": "Build a 10-slide deck using only these facts: ..."},
            },
            "completed",
        ),
        None,
    )

    # copilot_prompt_fallback with no prompt anywhere -> fabrication guard fires.
    result = fn({"creationMode": "copilot_prompt_fallback", "artifactPackage": {}}, "completed")
    ok &= check("fallback without prompt overrides status", result[0] if result else None, "blocked")
    ok &= check("fallback without prompt blocker mentions no build prompt",
                "no build prompt" in (result[1] if result else ""), True)

    # top-level copilotPrompt field also satisfies the fallback requirement.
    ok &= check(
        "fallback prompt accepted from top-level field too",
        fn({"creationMode": "copilot_prompt_fallback", "copilotPrompt": "Build a one-pager using only these facts: ..."}, "completed"),
        None,
    )

    # no creationMode at all -> not an artifact-creation completion, no override (email/Teams/
    # calendar/document-backed-draft/suggestions job types are completely unaffected).
    ok &= check(
        "no creationMode means no override (other job types unaffected)",
        fn({"resultSummary": "Sent the reply"}, "completed"),
        None,
    )

    # creationMode present but status isn't 'completed' yet -> no override (still in progress).
    ok &= check(
        "creationMode with non-completed status is not overridden",
        fn({"creationMode": "created"}, "in_progress"),
        None,
    )

    # unrecognised creationMode value -> ignored, no override.
    ok &= check(
        "unrecognised creationMode value is ignored",
        fn({"creationMode": "sent-to-printer", "link": ""}, "completed"),
        None,
    )

    if ok:
        print("\nAll document/deck creation workflow checks passed.")
        return 0
    print("\nSome document/deck creation workflow checks FAILED.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
