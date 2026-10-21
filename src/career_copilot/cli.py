"""Profile, job checklist, and tailored resume CLI.

Interview prep is not part of this command.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from career_copilot import __version__
from career_copilot.docx_export import write_cover_letter, write_resume, write_source_resume
from career_copilot.errors import CareerCopilotError
from career_copilot.grounding import grounding_problems
from career_copilot.identity import choose_profile, decide_match, default_profiles_root, list_stored_profiles
from career_copilot.inputs import read_job_file, read_job_stream, require_as_of, require_job_text
from career_copilot.llm import apply_model_draft, read_job
from career_copilot.library import ui_launch_command
from career_copilot.models import (
    ConfirmationState,
    DocumentKind,
    DocumentRequest,
    EvidenceCategory,
    JobAnalysis,
    MasterProfile,
    ModelMode,
    ProfileAtom,
    RequirementKind,
    SentenceKind,
)
from career_copilot.parsers import parse_document
from career_copilot.extract import extract_atoms
from career_copilot.preferences import format_preferences, preferences_from_flags, prompt_preferences
from career_copilot.presentation import header_contact, is_academic, resume_header_name
from career_copilot.resume import build_resume
from career_copilot.source_cv import prefer_source, preview_sections
from career_copilot.validator import validate_resume
from career_copilot.profile import (
    UnresolvedConflictsError,
    confirm_all,
    confirm_atoms,
    default_profile_path,
    edit_atom,
    empty_profile,
    ingest_file,
    load_profile,
    merge_document,
    new_profile_id,
    resolve_conflict,
    save_profile,
    set_preferences,
    unconfirm_atom,
)
def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except UnresolvedConflictsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except CareerCopilotError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="Profile JSON path. Default: .career-copilot/profile.json in the current directory.",
    )
    parser = argparse.ArgumentParser(
        prog="career-copilot",
        description=(
            "Confirm a profile, checklist a job, and write a tailored resume. "
            "Does not write interview prep."
        ),
    )
    parser.add_argument("--version", action="version", version=f"career-copilot {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", parents=[common], help="Read a DOCX or single-column text PDF.")
    ingest.add_argument("path", type=Path)
    ingest.add_argument(
        "--kind",
        choices=[kind.value for kind in DocumentKind],
        default=DocumentKind.cv.value,
        help="cv, certificate, or portfolio_note. Default: cv.",
    )
    ingest.add_argument(
        "--profiles-dir",
        type=Path,
        help="Directory of separate profiles. Used when --profile is omitted.",
    )
    ingest.add_argument(
        "--choose",
        help="new, or a profile id. Answers the profile question without a prompt.",
    )
    ingest.set_defaults(handler=_ingest)

    status = commands.add_parser("status", parents=[common], help="Show profile counts and the data notice.")
    status.set_defaults(handler=_status)

    listing = commands.add_parser("list", parents=[common], help="List atoms without printing full documents.")
    listing.add_argument(
        "--confirmation",
        choices=["extracted", "confirmed", "all"],
        default="all",
    )
    listing.set_defaults(handler=_list)

    show = commands.add_parser("show", parents=[common], help="Show one atom and any open conflicts.")
    show.add_argument("evidence_id")
    show.set_defaults(handler=_show)

    conflicts = commands.add_parser("conflicts", parents=[common], help="Show cross-document conflicts.")
    conflicts.set_defaults(handler=_conflicts)

    confirm = commands.add_parser("confirm", parents=[common], help="Confirm one or more atoms.")
    confirm.add_argument("evidence_id", nargs="+")
    confirm.set_defaults(handler=_confirm)

    confirm_all_cmd = commands.add_parser(
        "confirm-all",
        parents=[common],
        help="Confirm every atom. Refuses while a conflict is unresolved.",
    )
    confirm_all_cmd.set_defaults(handler=_confirm_all)

    unconfirm = commands.add_parser("unconfirm", parents=[common], help="Mark an atom as extracted again.")
    unconfirm.add_argument("evidence_id")
    unconfirm.set_defaults(handler=_unconfirm)

    edit = commands.add_parser("edit", parents=[common], help="Edit an atom. The evidence id stays the same.")
    edit.add_argument("evidence_id")
    edit.add_argument("--statement")
    edit.add_argument("--category", choices=[item.value for item in EvidenceCategory])
    edit.add_argument("--employer")
    edit.add_argument("--clear-employer", action="store_true")
    edit.add_argument("--title")
    edit.add_argument("--clear-title", action="store_true")
    edit.add_argument("--start", dest="start_date")
    edit.add_argument("--clear-start", action="store_true")
    edit.add_argument("--end", dest="end_date")
    edit.add_argument("--clear-end", action="store_true")
    edit.add_argument("--skill", action="append", dest="skills")
    edit.add_argument("--clear-skills", action="store_true")
    edit.add_argument("--metric", action="append", dest="metrics")
    edit.add_argument("--clear-metrics", action="store_true")
    edit.add_argument("--ownership-verb")
    edit.add_argument("--clear-ownership-verb", action="store_true")
    edit.set_defaults(handler=_edit)

    resolve = commands.add_parser(
        "resolve",
        parents=[common],
        help="Record a note on a conflict. Does not merge or delete atoms.",
    )
    resolve.add_argument("conflict_id")
    resolve.add_argument("--note", required=True)
    resolve.set_defaults(handler=_resolve)

    check = commands.add_parser("check", parents=[common], help="Check that atoms still quote their documents.")
    check.set_defaults(handler=_check)

    review = commands.add_parser("review", parents=[common], help="Confirm or edit extracted atoms one by one.")
    review.set_defaults(handler=_review)

    preferences = commands.add_parser(
        "preferences",
        parents=[common],
        help="Save work preferences on one profile. Does not search for jobs.",
    )
    preferences.add_argument("--remote", choices=["high", "medium", "low", "only_if_close"])
    preferences.add_argument("--hybrid", choices=["high", "medium", "low", "only_if_close"])
    preferences.add_argument("--onsite", choices=["high", "medium", "low", "only_if_close"])
    preferences.add_argument("--location", help="Region where onsite work is acceptable.")
    preferences.add_argument("--sector", choices=["academia", "industry", "both"])
    preferences.add_argument("--availability", help="Optional time availability.")
    preferences.add_argument("--update", action="store_true", help="Replace preferences that are already saved.")
    preferences.set_defaults(handler=_preferences)

    job = commands.add_parser(
        "job",
        parents=[common],
        help="Checklist a pasted job description. Does not generate a resume.",
    )
    job.add_argument("path", nargs="?", type=Path, help="Text file with the job description.")
    job.add_argument("--text", help="Job description text. Use this or a file, not both.")
    job.add_argument("--as-of", dest="as_of", help="Date YYYY-MM-DD for open roles. Default: today.")
    job.add_argument("--out", type=Path, help="Write the checklist JSON to this path.")
    job.set_defaults(handler=_job)

    resume = commands.add_parser(
        "resume",
        parents=[common],
        help="Write a tailored resume from confirmed atoms. Optional cover letter. No interview prep.",
    )
    resume.add_argument("path", nargs="?", type=Path, help="Text file with the job description.")
    resume.add_argument("--text", help="Job description text. Use this or a file, not both.")
    resume.add_argument("--as-of", dest="as_of", help="Date YYYY-MM-DD for open roles. Default: today.")
    resume.add_argument("--out", type=Path, required=True, help="DOCX path for the resume.")
    resume.add_argument("--diff", type=Path, help="Markdown diff path. Default: next to the DOCX.")
    resume.add_argument("--cover-letter", type=Path, help="Optional cover letter DOCX.")
    resume.add_argument(
        "--confirm",
        help="Skip the questions. Comma-separated: resume, cover. Omit this to be asked.",
    )
    resume.set_defaults(handler=_resume)

    ui = commands.add_parser(
        "ui",
        help="Open the local interface on this computer. Nothing is uploaded.",
    )
    ui.add_argument("--port", type=int, default=8501, help="Loopback port. Default: 8501.")
    ui.set_defaults(handler=_ui)
    return parser


def _ingest(args: argparse.Namespace) -> int:
    if args.profile is not None and args.choose is not None:
        raise CareerCopilotError("Pass --profile or --choose, not both.")
    if args.profile is not None:
        path = args.profile
        profile = load_profile(path)
        updated, count, warnings = ingest_file(profile, args.path, DocumentKind(args.kind))
        save_profile(path, updated)
        _print_ingest(args.path.name, updated, count, warnings)
        return 0
    return _ingest_routed(args)


def _ingest_routed(args: argparse.Namespace) -> int:
    document = parse_document(args.path, DocumentKind(args.kind))
    atoms = extract_atoms(document)
    root = args.profiles_dir if args.profiles_dir is not None else default_profiles_root()
    stored = list_stored_profiles(root)
    decision = decide_match(atoms, [profile for _path, profile in stored])
    chosen = choose_profile(decision, choice=args.choose, stored=stored)
    if chosen is None:
        print("Stopped. No profile was changed.")
        return 0
    if chosen == "new":
        profile = empty_profile().model_copy(update={"profile_id": new_profile_id()})
        path = root / profile.profile_id / "profile.json"
        print(f"Created profile {profile.profile_id}")
    else:
        path, profile = chosen
        reason = "selected" if args.choose else decision.reason
        print(f"Attached to {profile.profile_id} ({reason}).")
    updated, count, warnings = merge_document(profile, document, atoms)
    save_profile(path, updated)
    print(f"Profile file: {path}")
    _print_ingest(args.path.name, updated, count, warnings)
    return 0


def _print_ingest(filename: str, updated: MasterProfile, count: int, warnings: list[str]) -> None:
    for warning in warnings:
        print(warning, file=sys.stderr)
    document = updated.documents[-1]
    unresolved = sum(1 for conflict in updated.conflicts if not conflict.resolved)
    print(f"Ingested {filename}")
    print(f"Document {document.document_id}")
    print(f"Atoms from this file: {count}")
    print(f"Unresolved conflicts: {unresolved}")


def _status(args: argparse.Namespace) -> int:
    profile = load_profile(default_profile_path(args.profile))
    counts = {state.value: 0 for state in ConfirmationState}
    for atom in profile.atoms:
        counts[atom.confirmation.value] = counts.get(atom.confirmation.value, 0) + 1
    unresolved = sum(1 for conflict in profile.conflicts if not conflict.resolved)
    print(f"Documents: {len(profile.documents)}")
    print(
        f"Atoms: {len(profile.atoms)} "
        f"({counts.get('confirmed', 0)} confirmed, {counts.get('extracted', 0)} extracted)"
    )
    print(f"Conflicts: {unresolved} unresolved of {len(profile.conflicts)}")
    print(f"Profile id: {profile.profile_id or 'unset'}")
    print("Preferences: saved" if profile.preferences is not None else "Preferences: not set")
    print(f"Purpose: {profile.data_notice.purpose}")
    print(f"Retention: {profile.data_notice.retention}")
    print(f"Deletion: {profile.data_notice.deletion}")
    return 0


def _list(args: argparse.Namespace) -> int:
    profile = load_profile(default_profile_path(args.profile))
    shown = 0
    for atom in profile.atoms:
        if args.confirmation != "all" and atom.confirmation.value != args.confirmation:
            continue
        print(
            f"{atom.evidence_id}  {atom.confirmation.value}  {atom.category.value}  {_short(atom.statement)}"
        )
        shown += 1
    if shown == 0:
        print("No atoms.")
    return 0


def _show(args: argparse.Namespace) -> int:
    profile = load_profile(default_profile_path(args.profile))
    atom = profile.atom(args.evidence_id)
    if atom is None:
        raise CareerCopilotError(f"No atom with evidence id {args.evidence_id}.")
    print(_card(profile, atom))
    return 0


def _conflicts(args: argparse.Namespace) -> int:
    profile = load_profile(default_profile_path(args.profile))
    if not profile.conflicts:
        print("No conflicts.")
        return 0
    for conflict in profile.conflicts:
        state = "resolved" if conflict.resolved else "unresolved"
        print(f"{conflict.conflict_id}  {state}  {conflict.kind.value}")
        print(f"  {conflict.summary}")
        if conflict.resolution_note:
            print(f"  Note: {conflict.resolution_note}")
    return 0


def _confirm(args: argparse.Namespace) -> int:
    path = default_profile_path(args.profile)
    profile, warnings = confirm_atoms(load_profile(path), list(args.evidence_id))
    save_profile(path, profile)
    for warning in warnings:
        print(warning, file=sys.stderr)
    print(f"Confirmed {len(args.evidence_id)} atom(s).")
    return 0


def _confirm_all(args: argparse.Namespace) -> int:
    path = default_profile_path(args.profile)
    profile = load_profile(path)
    before = sum(1 for atom in profile.atoms if atom.confirmation == ConfirmationState.extracted)
    updated = confirm_all(profile)
    save_profile(path, updated)
    print(f"Confirmed {before} atom(s).")
    return 0


def _unconfirm(args: argparse.Namespace) -> int:
    path = default_profile_path(args.profile)
    profile = unconfirm_atom(load_profile(path), args.evidence_id)
    save_profile(path, profile)
    print(f"{args.evidence_id} is extracted again.")
    return 0


def _edit(args: argparse.Namespace) -> int:
    changes = _edit_changes(args)
    path = default_profile_path(args.profile)
    profile = edit_atom(load_profile(path), args.evidence_id, **changes)
    save_profile(path, profile)
    print(f"Updated {args.evidence_id}. It needs confirmation again.")
    return 0


def _resolve(args: argparse.Namespace) -> int:
    path = default_profile_path(args.profile)
    profile = resolve_conflict(load_profile(path), args.conflict_id, args.note)
    save_profile(path, profile)
    print(f"Resolved {args.conflict_id}. Atoms were not merged.")
    return 0


def _check(args: argparse.Namespace) -> int:
    profile = load_profile(default_profile_path(args.profile))
    problems = grounding_problems(profile)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("Profile quotes its source documents.")
    return 0


def _job(args: argparse.Namespace) -> int:
    description = _job_text(args)
    as_of = _as_of(args.as_of)
    profile = load_profile(default_profile_path(args.profile))
    analysis = read_job(description, profile.atoms, as_of=as_of)
    print(_format_analysis(analysis))
    if args.out is not None:
        args.out.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
        print(f"Wrote {args.out}")
    return 0


def _job_text(args: argparse.Namespace) -> str:
    if args.path is not None and args.text is not None:
        raise CareerCopilotError("Pass a job file or --text, not both.")
    if args.text is not None:
        return require_job_text(args.text)
    if args.path is not None:
        return read_job_file(args.path)
    return read_job_stream(sys.stdin)


def _as_of(value: str | None) -> date:
    if value is None:
        return date.today()
    return require_as_of(value, invalid_message="--as-of must be YYYY-MM-DD.")


def _format_analysis(analysis: JobAnalysis) -> str:
    years = analysis.years
    shown = years.days / 365.25
    coverage = "dated roles cover the timeline" if years.complete else "some confirmed roles have no start date"
    lines = [
        analysis.model_note,
        f"Years of experience: {shown:.1f} ({years.days} days, as of {years.as_of}; {coverage})",
    ]
    tally = analysis.tally
    lines.append(
        f"Checklist: {tally.met} met, {tally.partial} partial, {tally.missing} missing, {tally.unknown} unknown"
    )
    if tally.percent is None:
        lines.append("Match: unknown. No requirement was met, partial, or missing.")
    else:
        lines.append(f"Match: {tally.percent:.1f}% of judged requirements. Unknown lines are excluded.")
    if years.intervals:
        lines.append("Intervals: " + "; ".join(years.intervals))
    lines.append("Hard constraints:")
    if not analysis.hard_constraints:
        lines.append("  none")
    for constraint in analysis.hard_constraints:
        detail = ""
        if constraint.required_years is not None:
            observed = "unknown" if constraint.observed_days is None else f"{constraint.observed_days} days"
            detail = f" (required {constraint.required_years} years, observed {observed})"
        lines.append(f"  {constraint.result.value}  {constraint.kind.value}  {constraint.text}{detail}")
    for kind, title in (
        (RequirementKind.mandatory, "Mandatory requirements"),
        (RequirementKind.preferred, "Preferred requirements"),
    ):
        lines.append(f"{title}:")
        items = [item for item in analysis.requirements if item.kind == kind]
        if not items:
            lines.append("  none")
        for item in items:
            lines.append(f"  {item.status.value}  {item.text}")
            if item.evidence_ids:
                lines.append(f"    evidence: {', '.join(item.evidence_ids)}")
                lines.append(f"    excerpt: {_short(item.excerpt)}")
    lines.append("Documents the posting asks for:")
    if not analysis.requested_documents:
        lines.append("  none")
    labels = {
        DocumentRequest.cover_letter: "cover letter — drafted only after you approve the resume and say yes",
        DocumentRequest.portfolio: "portfolio — named only, not drafted",
        DocumentRequest.writing_sample: "writing sample — named only, not drafted",
        DocumentRequest.transcript: "transcript — named only, not drafted",
    }
    for item in analysis.requested_documents:
        lines.append(f"  {labels[item]}")
    return "\n".join(lines)


def _resume(args: argparse.Namespace) -> int:
    description = _job_text(args)
    profile = load_profile(default_profile_path(args.profile))
    analysis = read_job(description, profile.atoms, as_of=_as_of(args.as_of))
    print(_format_analysis(analysis))
    print()
    try:
        write_resume_doc, write_cover = _confirmed_documents(args.confirm, analysis)
    except EOFError:
        print("Stopped. No documents were written.")
        return 0
    if not write_resume_doc and not write_cover:
        print("No documents were written.")
        return 0
    profile_path = default_profile_path(args.profile)
    bundle = build_resume(
        profile.atoms,
        analysis,
        cover_letter=write_cover,
        job_text=description,
        academic=is_academic(profile),
    )
    bundle, source = prefer_source(profile, analysis, bundle, profile_path=profile_path)
    replaceable = {SentenceKind.summary, SentenceKind.skill} if source is not None else None
    drafted = apply_model_draft(profile, analysis, bundle, description, replaceable=replaceable)
    if drafted is not None and source is not None:
        adopted = source.adopt(drafted.sentences)
        if adopted is None:
            drafted = None
        else:
            source = adopted
            bundle = drafted
            bundle.sentences = adopted.sentences
            bundle.publication_note = False
    elif drafted is not None:
        bundle = drafted
    if drafted is not None:
        if source is not None:
            print("The model drafted the summary and Technical Skills. The checker passed.")
        else:
            print("The model drafted the summary, skills, and bullets. The checker passed.")
    elif analysis.model_mode == ModelMode.model:
        print("The model draft was not used. Confirmed sentences were kept.")
    if write_cover and not bundle.cover:
        raise CareerCopilotError("No confirmed role sentence is available for a cover letter.")
    to_check = list(bundle.sentences) if write_resume_doc else []
    if write_cover:
        to_check.extend(bundle.cover)
    violations = validate_resume(to_check, profile, analysis)
    if violations:
        for item in violations:
            print(f"{item.code}: {item.detail}", file=sys.stderr)
        return 1
    if write_resume_doc:
        if source is not None:
            write_source_resume(args.out, preview_sections(source, source.summary_text, source.skills_text))
        else:
            write_resume(
                args.out,
                bundle.sentences,
                header_contact=header_contact(profile),
                header_name=resume_header_name(profile, bundle.sentences),
                publication_note=bundle.publication_note,
            )
        diff_path = args.diff if args.diff is not None else args.out.with_suffix(".diff.md")
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.write_text(bundle.diff_markdown, encoding="utf-8")
        print(bundle.diff_markdown, end="")
        print(f"Wrote {args.out}")
        print(f"Wrote {diff_path}")
    if write_cover:
        cover_path = args.cover_letter if args.cover_letter is not None else args.out.with_name(args.out.stem + "-cover.docx")
        name = next((sentence.text for sentence in bundle.sentences if sentence.kind == SentenceKind.name), "")
        if not name:
            name = resume_header_name(profile, bundle.sentences) or ""
        write_cover_letter(cover_path, bundle.cover, name)
        print(f"Wrote {cover_path}")
    return 0


def _ui(args: argparse.Namespace) -> int:
    if args.port < 1 or args.port > 65535:
        raise CareerCopilotError("--port must be between 1 and 65535.")
    if importlib.util.find_spec("streamlit") is None:
        raise CareerCopilotError(
            "Streamlit is not installed. Reinstall career-copilot to open the local interface."
        )
    env = os.environ.copy()
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    env["STREAMLIT_SERVER_ADDRESS"] = "127.0.0.1"
    env["STREAMLIT_SERVER_HEADLESS"] = "true"
    env["STREAMLIT_CLIENT_SHOW_ERROR_DETAILS"] = "none"
    env["STREAMLIT_CLIENT_SHOW_ERROR_LINKS"] = "false"
    print(f"Local interface at http://127.0.0.1:{args.port}")
    print("Nothing is uploaded. Stop this command to close it.")
    return int(subprocess.call(ui_launch_command(args.port), env=env))


def _confirmed_documents(confirm: str | None, analysis: JobAnalysis) -> tuple[bool, bool]:
    """Ask before writing. --confirm skips the questions.

    A cover letter is written only when the posting asks for one.
    """

    cover_allowed = DocumentRequest.cover_letter in analysis.requested_documents
    if confirm is not None:
        parts = {part.strip().lower() for part in confirm.split(",") if part.strip()}
        if not parts or not parts <= {"resume", "cover"}:
            raise CareerCopilotError("--confirm accepts resume, cover, or both.")
        if "cover" in parts and not cover_allowed:
            print("The posting does not ask for a cover letter. No cover letter was written.")
        return "resume" in parts, "cover" in parts and cover_allowed
    if not _yes_no("Continue with this job? [y/n]: "):
        return False, False
    resume_yes = _yes_no("Generate resume? [y/n]: ")
    cover_yes = False
    if cover_allowed:
        cover_yes = _yes_no("The posting asks for a cover letter. Generate it? [y/n]: ")
    return resume_yes, cover_yes


def _yes_no(prompt: str) -> bool:
    while True:
        answer = input(prompt).strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter y or n.")


def _preferences(args: argparse.Namespace) -> int:
    path = default_profile_path(args.profile)
    profile = load_profile(path)
    supplied = preferences_from_flags(
        remote=args.remote,
        hybrid=args.hybrid,
        onsite=args.onsite,
        location=args.location,
        sector=args.sector,
        availability=args.availability,
    )
    if profile.preferences is not None and supplied is None and not args.update:
        print(format_preferences(profile.preferences, profile.profile_id))
        return 0
    if profile.preferences is not None and supplied is not None and not args.update:
        raise CareerCopilotError("Preferences are already saved. Pass --update to replace them.")
    preferences = supplied if supplied is not None else prompt_preferences()
    updated = set_preferences(profile, preferences, update=args.update or profile.preferences is not None)
    stored = save_profile(path, updated)
    if stored.preferences is None:
        raise CareerCopilotError("Preferences were not saved.")
    print(format_preferences(stored.preferences, stored.profile_id))
    print(f"Saved {path}")
    return 0


def _review(args: argparse.Namespace) -> int:
    path = default_profile_path(args.profile)
    profile = load_profile(path)
    updated = review_profile(profile)
    save_profile(path, updated)
    return 0


def review_profile(
    profile: MasterProfile,
    *,
    input_fn=input,
    write=print,
) -> MasterProfile:
    pending = [atom.evidence_id for atom in profile.atoms if atom.confirmation == ConfirmationState.extracted]
    if not pending:
        write("Nothing left to confirm.")
        return profile
    index = 0
    while index < len(pending):
        atom = profile.atom(pending[index])
        if atom is None or atom.confirmation != ConfirmationState.extracted:
            index += 1
            continue
        write(_card(profile, atom, index + 1, len(pending)))
        try:
            choice = input_fn("[c]onfirm, [e]dit statement, [s]kip, [q]uit: ").strip().lower()
        except EOFError:
            write("Stopped.")
            break
        if choice in {"q", "quit"}:
            break
        if choice in {"", "s", "skip"}:
            index += 1
            continue
        if choice in {"c", "confirm"}:
            profile, warnings = confirm_atoms(profile, [atom.evidence_id])
            for warning in warnings:
                write(warning)
            write(f"Confirmed {atom.evidence_id}.")
            index += 1
            continue
        if choice in {"e", "edit"}:
            try:
                statement = input_fn("statement: ")
            except EOFError:
                write("Stopped.")
                break
            if not statement.strip():
                write("Statement unchanged.")
                continue
            try:
                profile = edit_atom(profile, atom.evidence_id, statement=statement)
            except CareerCopilotError as exc:
                write(str(exc))
                continue
            write(f"Updated {atom.evidence_id}. It needs confirmation again.")
            continue
        write("Use c, e, s, or q.")
    return profile


def _edit_changes(args: argparse.Namespace) -> dict[str, object]:
    _reject_pair(args.clear_employer, args.employer is not None, "--employer", "--clear-employer")
    _reject_pair(args.clear_title, args.title is not None, "--title", "--clear-title")
    _reject_pair(args.clear_start, args.start_date is not None, "--start", "--clear-start")
    _reject_pair(args.clear_end, args.end_date is not None, "--end", "--clear-end")
    _reject_pair(args.clear_skills, bool(args.skills), "--skill", "--clear-skills")
    _reject_pair(args.clear_metrics, bool(args.metrics), "--metric", "--clear-metrics")
    _reject_pair(
        args.clear_ownership_verb,
        args.ownership_verb is not None,
        "--ownership-verb",
        "--clear-ownership-verb",
    )
    changes: dict[str, object] = {}
    if args.statement is not None:
        changes["statement"] = args.statement
    if args.category is not None:
        changes["category"] = args.category
    if args.clear_employer:
        changes["employer"] = None
    elif args.employer is not None:
        changes["employer"] = args.employer
    if args.clear_title:
        changes["title"] = None
    elif args.title is not None:
        changes["title"] = args.title
    if args.clear_start:
        changes["start"] = None
    elif args.start_date is not None:
        changes["start"] = args.start_date
    if args.clear_end:
        changes["end"] = None
    elif args.end_date is not None:
        changes["end"] = args.end_date
    if args.clear_skills:
        changes["skills"] = []
    elif args.skills is not None:
        changes["skills"] = list(args.skills)
    if args.clear_metrics:
        changes["metrics"] = []
    elif args.metrics is not None:
        changes["metrics"] = list(args.metrics)
    if args.clear_ownership_verb:
        changes["ownership_verb"] = None
    elif args.ownership_verb is not None:
        changes["ownership_verb"] = args.ownership_verb
    return changes


def _reject_pair(cleared: bool, provided: bool, set_flag: str, clear_flag: str) -> None:
    if cleared and provided:
        raise CareerCopilotError(f"Use either {set_flag} or {clear_flag}.")


def _card(profile: MasterProfile, atom: ProfileAtom, number: int | None = None, total: int | None = None) -> str:
    if number is not None and total is not None:
        header = f"Atom {number}/{total}  {atom.evidence_id}  {atom.confirmation.value}  {atom.category.value}"
    else:
        header = f"{atom.evidence_id}  {atom.confirmation.value}  {atom.category.value}"
    lines = [
        header,
        f"Statement: {atom.statement}",
        f"Excerpt: {atom.source_excerpt}",
        f"Document: {atom.source_document_id}",
    ]
    if atom.title:
        lines.append(f"Title: {atom.title}")
    if atom.employer:
        lines.append(f"Employer: {atom.employer}")
    date_line = _dates(atom)
    if date_line:
        lines.append(f"Dates: {date_line}")
    if atom.skills:
        lines.append("Skills: " + ", ".join(atom.skills))
    if atom.metrics:
        lines.append("Metrics: " + ", ".join(atom.metrics))
    if atom.ownership_verb:
        lines.append(f"Ownership verb: {atom.ownership_verb}")
    for conflict in profile.conflicts:
        if atom.evidence_id in conflict.evidence_ids and not conflict.resolved:
            lines.append(f"Conflict {conflict.conflict_id}: {conflict.summary}")
    return "\n".join(lines)


def _dates(atom: ProfileAtom) -> str:
    if atom.start_date is None and atom.end_date is None:
        return ""
    start = atom.start_date.raw if atom.start_date else "?"
    if atom.end_date is not None:
        end = atom.end_date.raw
    elif atom.start_date is not None:
        end = "open"
    else:
        end = "?"
    return f"{start} – {end}"


def _short(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3] + "..."
