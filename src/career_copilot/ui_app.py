"""Local visual interface. It binds to loopback and does not upload a profile."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic import ValidationError

from career_copilot.applications import load_applications, remember_cv, set_applied
from career_copilot.cli import _format_analysis
from career_copilot.errors import CareerCopilotError
from career_copilot.inputs import (
    AS_OF_MAX,
    AS_OF_MIN,
    MAX_DOWNLOAD_BYTES,
    MAX_JOB_TEXT,
    MAX_SENTENCE_TEXT,
    allowed_profile,
    directory_inside,
    path_inside,
    require_as_of,
    require_job_text,
)
from career_copilot.job import analyze_job
from career_copilot.job_documents import (
    apply_cv_note,
    document_folder,
    generate_cover_letter,
    generate_job_cv,
    propose_cover_letter,
    propose_cv_sections,
)
from career_copilot.job_list import (
    ListedJob,
    cleaned_posting,
    consider_posting,
    drop_job,
    EMPTY_REASON,
    FALLBACK_REASON,
    format_deadline,
    keep_job,
    job_key,
    load_log,
    notes_for_display,
    record_answer,
    revise_job,
    apply_saved_traits,
    posting_place,
    posting_workplace,
    deadline_on,
    real_search,
    requested_documents,
    score_posting,
    short_line,
    visible_jobs,
)
from career_copilot.llm import read_job
from career_copilot.spend import DAILY_LIMIT_NOTE
from career_copilot.library import (
    Draft,
    commit_profile,
    discover_profile_paths,
    draft_documents,
    generated_root,
    legacy_docx,
    list_generations,
    profile_key,
    record_analysis,
    save_generation,
    set_record_status,
    with_texts,
)
from career_copilot.models import (
    MAX_PREFERENCE_CHARS,
    DocumentKind,
    DocumentRequest,
    RequirementStatus,
    Residence,
)
from career_copilot.preferences import preferences_from_choices
from career_copilot.presentation import (
    extracted_contacts,
    fact_groups,
    fit_feedback,
    is_academic,
    job_name,
    person_name,
    polish,
    selected_areas,
    selected_modes,
    selected_schedules,
)
from career_copilot.profile import (
    empty_profile,
    ingest_bytes,
    load_profile,
    new_profile_id,
    set_display_name,
    set_preferences,
    set_residence,
)
from career_copilot.resume import build_resume
from career_copilot.roles import (
    WorkRole,
    load_cv,
    load_roles,
    load_skills,
    roles_path,
    save_cv,
    save_roles,
    save_skills,
    text_from_upload,
    understand_text,
)
from career_copilot.search_intent import (
    EMPLOYMENT,
    LEVELS,
    WORKPLACES,
    clean_conditions,
    load_search,
    save_search,
)
from career_copilot.source_cv import assemble_source, preview_sections, sentences_from_edits

_JOB_LABELS = ["Remote", "Hybrid", "Onsite"]
_AREA_LABELS = ["Academia", "Industry", "Other"]
_TIME_LABELS = ["Part-time", "Full-time", "Other"]
_JOB_VALUES = {"Remote": "remote", "Hybrid": "hybrid", "Onsite": "onsite"}
_AREA_VALUES = {"Academia": "academia", "Industry": "industry", "Other": "other"}
_TIME_VALUES = {"Part-time": "part_time", "Full-time": "full_time", "Other": "other"}
_NOTES_LIMIT = 1000
_KIND_LABELS = {
    "CV": DocumentKind.cv,
    "Certificate": DocumentKind.certificate,
    "Thesis, paper, or project": DocumentKind.portfolio_note,
}
_PAGES = ["Profile", "Job", "Generate", "Documents"]
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_REQUEST_COPY = {
    DocumentRequest.cover_letter: "Cover letter. It can be drafted after you approve the resume.",
    DocumentRequest.portfolio: "Portfolio. Named only. This app does not draft it.",
    DocumentRequest.writing_sample: "Writing sample. Named only. This app does not draft it.",
    DocumentRequest.transcript: "Transcript. Named only. This app does not draft it.",
}
_UNEXPECTED = "Something went wrong. Nothing was uploaded."
_GAP_LABELS = {
    RequirementStatus.partial: "Partial",
    RequirementStatus.missing: "Missing",
    RequirementStatus.unknown: "Not judged",
}


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Career Copilot", layout="centered")
    if not _is_loopback(st.get_option("server.address")):
        st.error("This page only runs on this computer.")
        st.stop()
    try:
        _main(st)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
    except Exception:
        st.error(_UNEXPECTED)


def _main(st) -> None:
    if st.session_state.pop("open_search", False):
        st.session_state["page"] = "Search"
    if st.session_state.pop("open_jobs", False):
        st.session_state["page"] = "Jobs"
    if st.session_state.get("flash"):
        st.success(st.session_state.pop("flash"))
    _prepare_session(st)
    page = st.sidebar.radio("Page", ["User", "Search", "Jobs", "Documents"], key="page")
    if page == "Search":
        _page_search(st)
    elif page == "Jobs":
        _page_jobs(st)
    elif page == "Documents":
        _page_written(st)
    else:
        _page_user(st)


def _page_written(st) -> None:
    st.header("Documents")
    records = load_applications()
    if not records:
        st.caption("No CV has been written yet.")
        return
    for record in records:
        _written_row(st, record)


def _written_row(st, record) -> None:
    import hashlib

    token = hashlib.sha256(record.key.encode("utf-8")).hexdigest()[:12]
    folder = document_folder(record.key)
    written = format_deadline(date.fromisoformat(record.written_on))
    status = "Applied" if record.applied == "yes" else "Not yet"
    st.subheader(record.title)
    st.caption(f"{record.company} · {written} · {status}")
    show_key = f"docs-show-{token}"
    cv_pdf = folder / "cv.pdf"
    cover_pdf = folder / "cover.pdf"
    if cv_pdf.is_file():
        if st.button("CV.pdf", key=f"docs-open-cv-{token}"):
            st.session_state[show_key] = "cv"
            st.rerun()
        st.download_button(
            "Download CV",
            data=cv_pdf.read_bytes(),
            file_name="CV.pdf",
            mime="application/pdf",
            key=f"docs-download-cv-{token}",
        )
        if st.session_state.get(show_key) == "cv" and (folder / "cv-preview.txt").is_file():
            st.text((folder / "cv-preview.txt").read_text(encoding="utf-8"))
    if cover_pdf.is_file():
        if st.button("Cover-letter.pdf", key=f"docs-open-letter-{token}"):
            st.session_state[show_key] = "cover"
            st.rerun()
        st.download_button(
            "Download cover letter",
            data=cover_pdf.read_bytes(),
            file_name="Cover-letter.pdf",
            mime="application/pdf",
            key=f"docs-download-letter-{token}",
        )
        if st.session_state.get(show_key) == "cover" and (folder / "cover-preview.txt").is_file():
            st.text((folder / "cover-preview.txt").read_text(encoding="utf-8"))
    if st.button("Yes", key=f"docs-applied-yes-{token}"):
        _store_written(st, record, "yes")
    if st.button("Not yet", key=f"docs-applied-not-yet-{token}"):
        _store_written(st, record, "not_yet")


def _store_written(st, record, applied: str) -> None:
    try:
        set_applied(record.key, applied, title=record.title, company=record.company)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    st.rerun()


def _page_user(st) -> None:
    st.header("User")
    st.caption("Upload a CV. The page names the kinds of work this experience can support.")
    upload = st.file_uploader("CV", type=["pdf", "docx"])
    if st.button("Read the CV"):
        _read_cv(st, upload)
    roles = st.session_state.get("roles")
    if not roles:
        stored = _stored_roles()
        if stored:
            st.session_state["roles"] = [role.model_dump() for role in stored]
            st.session_state["role_generation"] = st.session_state.get("role_generation", 0)
            roles = st.session_state["roles"]
    if not roles:
        return
    generation = st.session_state.get("role_generation", 0)
    st.subheader("Possible roles")
    for index, role in enumerate(roles):
        key = f"role-kept-{generation}-{index}"
        if key not in st.session_state:
            st.session_state[key] = bool(role.get("kept", True))
        st.checkbox(str(role.get("name", "")), key=key)
        reason = str(role.get("reason", "")).strip()
        if reason:
            st.caption(reason)
    st.text_input("Add a role", key="add-role", max_chars=60)
    if st.button("Save"):
        _save_chosen_roles(st, roles, generation)
    if _stored_roles():
        st.divider()
        if st.button("Continue"):
            st.session_state["open_search"] = True
            st.rerun()


def _read_cv(st, upload: object) -> None:
    try:
        if upload is None:
            raise CareerCopilotError("Choose a PDF or DOCX file.")
        filename, payload = _read_upload(upload)
        text = text_from_upload(filename, payload)
        st.session_state["cv_text"] = text
        with st.spinner("Reading the CV."):
            reading = understand_text(text)
        save_cv(text)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    generation = int(st.session_state.get("role_generation", 0)) + 1
    st.session_state["role_generation"] = generation
    st.session_state["roles"] = [role.model_dump() for role in reading.roles]
    save_skills(reading.skills)
    st.session_state["profile-skills"] = "\n".join(reading.skills)
    _flash(st, "These are the roles this experience can support.")
    st.rerun()


def _save_chosen_roles(st, roles: list[dict[str, object]], generation: int) -> None:
    try:
        chosen: list[WorkRole] = []
        for index, role in enumerate(roles):
            kept = bool(st.session_state.get(f"role-kept-{generation}-{index}"))
            item = WorkRole.model_validate({**role, "kept": kept})
            if item.kept:
                chosen.append(item)
        extra = " ".join(str(st.session_state.get("add-role", "")).split())
        if extra:
            chosen.append(WorkRole(name=extra, reason="", kept=True, added=True))
        if not chosen:
            raise CareerCopilotError("Keep at least one role, or add one.")
        save_roles(roles_path(), chosen)
    except (CareerCopilotError, ValidationError, ValueError) as exc:
        st.error(_public_error(exc))
        return
    st.session_state["role_generation"] = generation + 1
    st.session_state["roles"] = [role.model_dump() for role in chosen]
    _flash(st, "Saved the roles on this computer.")
    st.rerun()


def _page_search(st) -> None:
    st.header("Search")
    if not _stored_roles():
        st.caption("Save the roles first.")
        return
    saved = _stored_search(st)
    _init_multi(st, "search-levels", saved.levels if saved else [])
    _init_multi(st, "search-employment", saved.employment if saved else [])
    _init_multi(st, "search-workplaces", saved.workplaces if saved else [])
    _init_text(st, "search-country", "" if saved is None else saved.country)
    _init_text(st, "search-city", "" if saved is None else saved.city)
    _init_text(st, "search-languages", "" if saved is None else saved.languages)
    _init_notes(st, "search-notes", "" if saved is None else saved.notes)
    st.caption(
        "Leave a blank field out of the search. A country without a city covers the whole country. No country means worldwide."
    )
    st.multiselect("Experience level", list(LEVELS), key="search-levels")
    st.multiselect("Employment type", list(EMPLOYMENT), key="search-employment")
    st.multiselect("Workplace", list(WORKPLACES), key="search-workplaces")
    st.text_input("Country of residence", key="search-country", max_chars=80)
    st.text_input("City of residence", key="search-city", max_chars=80)
    st.text_input("Languages", key="search-languages", max_chars=120)
    st.text_area("Other notes", key="search-notes", height=140, max_chars=_NOTES_LIMIT)
    st.caption("Optional. These lines guide the search. They do not remove a job by themselves.")
    if st.button("Save search"):
        _save_search_form(st)
    if _stored_search_file():
        st.divider()
        if st.button("Continue"):
            st.session_state["open_jobs"] = True
            st.rerun()


def _save_search_form(st) -> None:
    try:
        conditions = clean_conditions(
            list(st.session_state.get("search-levels") or []),
            list(st.session_state.get("search-employment") or []),
            list(st.session_state.get("search-workplaces") or []),
            str(st.session_state.get("search-country", "")),
            str(st.session_state.get("search-city", "")),
            str(st.session_state.get("search-languages", "")),
            notes=str(st.session_state.get("search-notes", "")),
        )
        save_search(conditions)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    st.session_state["search-record"] = conditions
    st.session_state["search-loaded"] = True
    st.session_state["pending-notes"] = conditions.notes
    _flash(st, "Saved the search conditions on this computer.")
    st.rerun()


def _stored_search(st):
    if "search-loaded" in st.session_state:
        return st.session_state.get("search-record")
    try:
        record = load_search()
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        record = None
    st.session_state["search-loaded"] = True
    st.session_state["search-record"] = record
    return record


def _init_multi(st, key: str, value: list[str]) -> None:
    if key not in st.session_state:
        st.session_state[key] = list(value)


def _init_notes(st, key: str, value: str) -> None:
    pending = st.session_state.pop("pending-notes", None)
    if isinstance(pending, str):
        st.session_state[key] = pending
    elif key not in st.session_state:
        st.session_state[key] = str(value)[:_NOTES_LIMIT]


def _init_skills(st) -> None:
    pending = st.session_state.pop("pending-skills", None)
    if isinstance(pending, str):
        st.session_state["profile-skills"] = pending
    elif "profile-skills" not in st.session_state:
        st.session_state["profile-skills"] = "\n".join(_stored_skills())


def _stored_skills() -> list[str]:
    try:
        return load_skills()
    except CareerCopilotError:
        return []


def _page_jobs(st) -> None:
    st.header("Jobs")
    roles = _stored_roles()
    conditions = _stored_search_file()
    if not roles or conditions is None:
        st.caption("Save the roles and the search conditions first.")
        return
    cv_text = _stored_cv(st)
    if not cv_text:
        st.caption("Read the CV on the User page first.")
    _init_notes(st, "search-notes", conditions.notes)
    _init_skills(st)
    _profile_card(st, roles, conditions)
    apply_saved_traits()
    log = load_log()
    _refresh_paste(st, log)
    with st.container(border=True):
        if st.button("Automatic search"):
            _run_real_search(st, cv_text, roles, conditions)
    with st.container(border=True):
        if st.button("Manual search"):
            st.session_state["show_paste"] = True
        if st.session_state.get("show_paste"):
            st.caption("Paste the posting, or one https link.")
            st.text_area("Job posting or link", key="paste-text", max_chars=MAX_JOB_TEXT, height=160)
            if st.button("Match analysis"):
                _run_paste(st, cv_text, roles, conditions)
            result = st.session_state.get("paste-result")
            if isinstance(result, ListedJob):
                _job_result(st, result, prefix="paste")
    jobs = visible_jobs(log)
    if jobs:
        st.table(
            {
                "Title": [job.title for job in jobs],
                "Company": [job.company for job in jobs],
                "Found at": [job.source for job in jobs],
                "Match": [f"{job.match_percent}%" for job in jobs],
            },
            alt="Job matches",
        )
    if log.reason in {FALLBACK_REASON, EMPTY_REASON}:
        st.caption(log.reason)
    elif not jobs:
        hidden = sum(1 for job in log.latest if job.match_percent < 50)
        if log.reason:
            st.caption(log.reason)
        elif log.below_count:
            st.caption(short_line(log.below_count))
        elif hidden:
            st.caption(short_line(hidden))
        else:
            st.caption("No matching job is on the list yet.")
    elif log.below_count:
        st.caption(short_line(log.below_count))
    elif log.found_count == 0 and log.reason:
        st.caption(log.reason)
    if jobs:
        labels = [f"{job.title} — {job.company}" for job in jobs]
        if st.session_state.get("open-job") not in labels:
            st.session_state["open-job"] = labels[0]
        choice = st.radio("Open a job", labels, key="open-job")
        selected = jobs[labels.index(choice)] if choice in labels else jobs[0]
        _job_detail(st, selected)


def _stored_cv(st) -> str:
    try:
        saved = load_cv()
    except CareerCopilotError:
        saved = ""
    if saved:
        return saved
    return _session_cv(st)


def _session_cv(st) -> str:
    text = st.session_state.get("cv_text", "")
    if not isinstance(text, str):
        return ""
    return text.strip()


def _refresh_paste(st, log) -> None:
    result = st.session_state.get("paste-result")
    if not isinstance(result, ListedJob):
        return
    for job in [*log.kept, *log.latest]:
        if job_key(job) == job_key(result):
            st.session_state["paste-result"] = job
            return


def _store_answer(st, ask: str, basis: str, vote: str | None) -> None:
    record_answer(ask, basis, vote)
    result = st.session_state.get("paste-result")
    if isinstance(result, ListedJob):
        revised = revise_job(result, ask, basis, vote)
        log = load_log()
        for job in [*log.kept, *log.latest]:
            if job_key(job) == job_key(revised):
                revised = job
                break
        st.session_state["paste-result"] = revised
    st.rerun()


def _possible_rows(job: ListedJob) -> list[tuple[str, str]]:
    rows = [(line, "") for line in job.partial]
    rows.extend((item.ask, item.basis) for item in job.assumed)
    return rows


def _possible_block(st, job: ListedJob, prefix: str) -> None:
    import hashlib

    rows = _possible_rows(job)
    if not rows:
        return
    st.subheader("Possible matches")
    for ask, basis in rows:
        if basis and basis.casefold() != ask.casefold():
            st.markdown(f"- {ask} — {basis}")
        else:
            st.markdown(f"- {ask}")
        yes_col, no_col = st.columns(2)
        token = hashlib.sha256(f"{prefix}\n{job_key(job)}\n{ask}".encode("utf-8")).hexdigest()[:16]
        if yes_col.button("Yes", key=f"trait-up-{token}"):
            _store_answer(st, ask, basis, "up")
        if no_col.button("No", key=f"trait-down-{token}"):
            _store_answer(st, ask, basis, "down")


def _job_result(st, job: ListedJob, *, actions: bool = False, prefix: str = "open") -> None:
    st.subheader(job.title)
    if actions:
        keep_col, drop_col = st.columns(2)
        if keep_col.button("Keep this job", key="keep-open"):
            keep_job(job)
            st.rerun()
        if drop_col.button("Remove this job", key="drop-open"):
            drop_job(job)
            st.rerun()
    place = posting_place(job.description)
    workplace = posting_workplace(job.description)
    details = [part for part in (job.company, place, workplace, job.source) if part]
    if details:
        st.caption(" · ".join(details))
    found = deadline_on(job.description)
    if found is not None:
        st.caption(f"Deadline: {format_deadline(found)}")
    if job.url.startswith("https://"):
        st.markdown(f"[Open the original listing]({job.url})")
    posting = cleaned_posting(job.description, job.title)
    if posting:
        st.markdown(posting)
    st.subheader(f"Match: {job.match_percent}%")
    st.subheader("What matches")
    if job.covered:
        for line in job.covered:
            st.markdown(f"- {line}")
    else:
        st.caption("No judged requirement matches.")
    _possible_block(st, job, prefix)
    st.subheader("What is missing")
    if job.missing:
        for line in job.missing:
            st.markdown(f"- {line}")
    else:
        st.caption("Nothing in the judged requirements is missing.")
    notes = notes_for_display(job.notes, job.description)
    if notes:
        st.subheader("Notes")
        for line in notes:
            st.markdown(f"- {line}")
    _documents_block(st, job, prefix)


def _documents_block(st, job: ListedJob, prefix: str) -> None:
    import hashlib

    token = hashlib.sha256(job_key(job).encode("utf-8")).hexdigest()[:12]
    folder = document_folder(job_key(job))
    documents = requested_documents(job.description)
    st.subheader("Documents")
    for name in documents:
        if name == "CV":
            _cv_documents(st, job, prefix, token, folder)
        elif name == "Cover letter":
            _letter_documents(st, job, prefix, token, folder)
        else:
            st.markdown(f"- {name}")
            st.caption("Bring your own file.")
    if documents == ["CV"]:
        st.caption("Only the CV is required.")


def _cv_documents(st, job: ListedJob, prefix: str, token: str, folder: Path) -> None:
    pdf_path = folder / "cv.pdf"
    preview_path = folder / "cv-preview.txt"
    show_key = f"preview-{prefix}-{token}"
    if st.button("Generate CV for this job", key=f"{prefix}-generate-cv-{token}"):
        _write_cv(st, job, show_key)
    notice_path = folder / "cv-notice.txt"
    if pdf_path.is_file() and notice_path.is_file():
        notice = notice_path.read_text(encoding="utf-8").strip()
        if notice:
            st.caption(notice)
    if pdf_path.is_file():
        name_col, note_col = st.columns([1, 2])
        if name_col.button("CV.pdf", key=f"{prefix}-open-cv-{token}"):
            st.session_state[show_key] = "cv"
            st.rerun()
        note_col.text_area("Notes", key=f"{prefix}-cv-note-{token}", height=80, max_chars=1000)
        if st.button("Apply", key=f"{prefix}-apply-cv-{token}"):
            _apply_cv_note(st, job, str(st.session_state.get(f"{prefix}-cv-note-{token}", "")), show_key)
        st.download_button(
            "Download CV",
            data=pdf_path.read_bytes(),
            file_name="CV.pdf",
            mime="application/pdf",
            key=f"{prefix}-download-cv-{token}",
        )
        if st.session_state.get(show_key) == "cv" and preview_path.is_file():
            st.text(preview_path.read_text(encoding="utf-8"))
        _applied_question(st, job, prefix, token)


def _letter_documents(st, job: ListedJob, prefix: str, token: str, folder: Path) -> None:
    pdf_path = folder / "cover.pdf"
    preview_path = folder / "cover-preview.txt"
    show_key = f"preview-{prefix}-{token}"
    if st.button("Generate cover letter for this job", key=f"{prefix}-generate-letter-{token}"):
        _write_letter(st, job, show_key)
    if pdf_path.is_file():
        if st.button("Cover-letter.pdf", key=f"{prefix}-open-letter-{token}"):
            st.session_state[show_key] = "cover"
            st.rerun()
        st.download_button(
            "Download cover letter",
            data=pdf_path.read_bytes(),
            file_name="Cover-letter.pdf",
            mime="application/pdf",
            key=f"{prefix}-download-letter-{token}",
        )
        if st.session_state.get(show_key) == "cover" and preview_path.is_file():
            st.text(preview_path.read_text(encoding="utf-8"))


def _applied_question(st, job: ListedJob, prefix: str, token: str) -> None:
    st.caption("Did you apply?")
    answer = _applied_answer(job_key(job))
    yes_col, later_col = st.columns(2)
    if yes_col.button("Yes", key=f"{prefix}-applied-yes-{token}"):
        _store_applied(st, job, "yes")
    if later_col.button("Not yet", key=f"{prefix}-applied-not-yet-{token}"):
        _store_applied(st, job, "not_yet")
    st.caption("You applied." if answer == "yes" else "Not yet.")


def _applied_answer(key: str) -> str:
    current = next((item for item in load_applications() if item.key == key), None)
    return current.applied if current is not None else "not_yet"


def _store_applied(st, job: ListedJob, applied: str) -> None:
    try:
        set_applied(job_key(job), applied, title=job.title, company=job.company)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    st.rerun()


def _mark_written(job: ListedJob) -> None:
    remember_cv(job_key(job), job.title, job.company)


def _write_cv(st, job: ListedJob, show_key: str) -> None:
    try:
        cv_text = load_cv()
        with st.spinner("Writing the CV."):
            generate_job_cv(cv_text, job.description, job_key(job), propose=propose_cv_sections)
        _mark_written(job)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    st.session_state[show_key] = "cv"
    st.rerun()


def _apply_cv_note(st, job: ListedJob, note: str, show_key: str) -> None:
    try:
        cv_text = load_cv()
        with st.spinner("Checking the note."):
            apply_cv_note(cv_text, job.description, job_key(job), note, propose=propose_cv_sections)
        _mark_written(job)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    st.session_state[show_key] = "cv"
    st.rerun()


def _write_letter(st, job: ListedJob, show_key: str) -> None:
    try:
        cv_text = load_cv()
        with st.spinner("Writing the cover letter."):
            generate_cover_letter(cv_text, job.description, job_key(job), propose=propose_cover_letter)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    st.session_state[show_key] = "cover"
    st.rerun()


def _job_detail(st, job: ListedJob) -> None:
    _job_result(st, job, actions=True, prefix="open")


def _run_real_search(st, cv_text: str, roles: list[WorkRole], conditions) -> None:
    try:
        conditions = _save_profile(st, conditions)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    error: CareerCopilotError | None = None
    outcome = None
    try:
        with st.status("Searching.", expanded=True) as status:
            def progress(message: str) -> None:
                status.update(label=message)

            try:
                outcome = real_search(
                    cv_text,
                    roles,
                    conditions,
                    score=score_posting,
                    skills=load_skills(),
                    progress=progress,
                )
            except CareerCopilotError as exc:
                status.update(label="The search stopped.", state="error")
                error = exc
            else:
                status.update(label="Search finished.", state="complete")
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    if error is not None or outcome is None:
        st.error(_public_error(error or CareerCopilotError("The search stopped.")))
        return
    if not outcome.found_count:
        _flash(st, outcome.reason or "The search finished.")
    elif outcome.below_count:
        _flash(st, short_line(outcome.below_count))
    st.rerun()


def _run_paste(st, cv_text: str, roles: list[WorkRole], conditions) -> None:
    try:
        conditions = _save_profile(st, conditions)
        with st.spinner("Checking this posting."):
            outcome = consider_posting(
                cv_text,
                str(st.session_state.get("paste-text", "")),
                roles,
                conditions,
                score=score_posting,
            )
    except CareerCopilotError as exc:
        st.session_state.pop("paste-result", None)
        st.error(_public_error(exc))
        return
    if outcome.job is None:
        st.session_state.pop("paste-result", None)
        _flash(st, outcome.note or "This posting could not be scored.")
        st.rerun()
        return
    st.session_state["paste-result"] = outcome.job
    if outcome.note:
        _flash(st, outcome.note)
    st.rerun()


def _stored_search_file():
    try:
        return load_search()
    except CareerCopilotError:
        return None


def _profile_card(st, roles: list[WorkRole], conditions) -> None:
    with st.container(border=True):
        st.subheader("Profile")
        st.caption("The search uses this profile. Change the place and the languages on the Search page.")
        st.markdown("**Roles**")
        for role in roles:
            st.markdown(f"- {role.name}")
            if role.reason.strip():
                st.caption(role.reason)
        st.text_area("Skills", key="profile-skills", height=120)
        st.markdown("**Place**")
        st.markdown(_place_summary(conditions))
        st.markdown("**Languages**")
        st.markdown(conditions.languages.strip() or "Not set")
        st.text_area("Other notes", key="search-notes", height=140, max_chars=_NOTES_LIMIT)
        if st.button("Save profile"):
            try:
                _save_profile(st, conditions)
            except CareerCopilotError as exc:
                st.error(_public_error(exc))
                return
            _flash(st, "Saved the profile on this computer.")
            st.rerun()


def _place_summary(conditions) -> str:
    work = ", ".join(conditions.workplaces) if conditions.workplaces else "Any workplace"
    if conditions.city and conditions.country:
        where = f"{conditions.city}, {conditions.country}"
    elif conditions.country:
        where = f"{conditions.country}. The whole country."
    elif conditions.city:
        where = conditions.city
    else:
        where = "Worldwide"
    return f"{work}. {where}."


def _save_profile(st, conditions):
    notes = str(st.session_state.get("search-notes", ""))
    updated = clean_conditions(
        list(conditions.levels),
        list(conditions.employment),
        list(conditions.workplaces),
        conditions.country,
        conditions.city,
        conditions.languages,
        conditions.programming,
        conditions.outside_academia,
        notes,
    )
    save_skills(str(st.session_state.get("profile-skills", "")).splitlines())
    save_search(updated)
    st.session_state["search-record"] = updated
    st.session_state["search-loaded"] = True
    st.session_state["pending-notes"] = updated.notes
    st.session_state["pending-skills"] = "\n".join(load_skills())
    return updated


def _stored_roles() -> list[WorkRole]:
    path = roles_path()
    if not path.is_file():
        return []
    try:
        return load_roles(path)
    except CareerCopilotError:
        return []


def _page_profile(st, path: Path, profile) -> None:
    st.header("User")
    name = person_name(profile)
    if name and name != "Profile":
        st.subheader(name)
    else:
        safe = _safe_key(path, profile)
        _init_text(st, f"empty-name-{safe}", "")
        st.text_input("Name", key=f"empty-name-{safe}", disabled=True, max_chars=MAX_PREFERENCE_CHARS)
    _personal_info(st, path, profile)
    _conflicts(st, profile)


def _create_profile(st) -> None:
    st.header("New profile")
    st.caption("The profile stays on this computer. A profile id is assigned when you create it.")
    name = st.text_input("Name", key="create-name", max_chars=MAX_PREFERENCE_CHARS)
    kind = st.selectbox("Document type", list(_KIND_LABELS), key="create-kind")
    uploads = st.file_uploader(
        "Background documents",
        type=["pdf", "docx"],
        accept_multiple_files=True,
    )
    if st.button("Create profile"):
        _store_new_profile(st, name, kind, uploads)


def _store_new_profile(st, name: object, kind: object, uploads: object) -> None:
    try:
        cleaned = _required_name(name)
        document_kind = _document_kind(kind)
        files = _upload_list(uploads)
        profile_id = new_profile_id()
        folder = directory_inside(Path.cwd() / ".career-copilot" / "profiles", profile_id)
        path = folder / "profile.json"
        profile = set_display_name(
            empty_profile().model_copy(update={"profile_id": profile_id}),
            cleaned,
        )
        profile, warnings = _ingest_uploads(profile, files, document_kind)
        commit_profile(path, profile, profile, root=_root())
    except (CareerCopilotError, ValidationError) as exc:
        st.error(_public_error(exc))
        return
    _show_warnings(st, warnings)
    _flash(st, "Saved the profile on this computer.")
    st.rerun()


def _personal_info(st, path: Path, profile) -> None:
    contacts = extracted_contacts(profile.atoms)
    if contacts["emails"]:
        st.text(", ".join(contacts["emails"]))
    safe = _safe_key(path, profile)
    residence = profile.residence
    _init_text(st, f"residence-contact-{safe}", "" if residence is None or not residence.contact else residence.contact)
    _init_text(st, f"residence-city-{safe}", "" if residence is None or not residence.city else residence.city)
    _init_text(st, f"residence-country-{safe}", "" if residence is None or not residence.country else residence.country)
    st.text_input("Contact", key=f"residence-contact-{safe}", max_chars=MAX_PREFERENCE_CHARS)
    st.text_input("City", key=f"residence-city-{safe}", max_chars=MAX_PREFERENCE_CHARS)
    st.text_input("Country of residence", key=f"residence-country-{safe}", max_chars=MAX_PREFERENCE_CHARS)
    if st.button("Save personal information"):
        _save_residence(st, path, profile)


def _save_residence(st, path: Path, profile) -> None:
    safe = _safe_key(path, profile)
    try:
        contact = _preference_text(st.session_state.get(f"residence-contact-{safe}"), required=False)
        city = _preference_text(st.session_state.get(f"residence-city-{safe}"), required=False)
        country = _preference_text(st.session_state.get(f"residence-country-{safe}"), required=False)
        residence = None
        if contact or city or country:
            residence = Residence(contact=contact, city=city, country=country)
        updated = set_residence(profile, residence)
        commit_profile(path, profile, updated, root=_root())
    except (CareerCopilotError, ValidationError, ValueError) as exc:
        st.error(_public_error(exc))
        return
    _flash(st, "Saved the personal information on this computer.")
    st.rerun()


def _upload_form(st, path: Path, profile) -> None:
    st.subheader("Documents")
    safe = _safe_key(path, profile)
    kind = st.selectbox("Document type", list(_KIND_LABELS), key=f"upload-kind-{safe}")
    uploads = st.file_uploader(
        "Add background documents",
        type=["pdf", "docx"],
        accept_multiple_files=True,
    )
    if st.button("Add documents"):
        _add_documents(st, path, profile, kind, uploads)


def _add_documents(st, path: Path, profile, kind: object, uploads: object) -> None:
    try:
        document_kind = _document_kind(kind)
        files = _upload_list(uploads)
        if not files:
            raise CareerCopilotError("Choose a PDF or DOCX file.")
        updated, warnings = _ingest_uploads(profile, files, document_kind)
        commit_profile(path, profile, updated, root=_root())
    except (CareerCopilotError, ValidationError) as exc:
        st.error(_public_error(exc))
        return
    _show_warnings(st, warnings)
    _flash(st, "Saved the documents on this computer.")
    st.rerun()


def _preferences_form(st, path: Path, profile) -> None:
    st.subheader("Preferences")
    st.caption("Saved on this profile. They do not search for jobs.")
    current = profile.preferences
    safe = _safe_key(path, profile)
    _init_choice(st, f"pref-job-{safe}", _labels_for(selected_modes(current), _JOB_VALUES, _JOB_LABELS), _JOB_LABELS)
    _init_choice(st, f"pref-area-{safe}", _labels_for(selected_areas(current), _AREA_VALUES, ["Industry"]), _AREA_LABELS)
    _init_choice(st, f"pref-time-{safe}", _labels_for(selected_schedules(current), _TIME_VALUES, ["Full-time"]), _TIME_LABELS)
    location = "" if current is None else current.onsite_location
    _init_text(st, f"pref-location-{safe}", location)
    st.pills("Job type", _JOB_LABELS, selection_mode="multi", key=f"pref-job-{safe}")
    st.pills("Area", _AREA_LABELS, selection_mode="multi", key=f"pref-area-{safe}")
    st.pills("Time", _TIME_LABELS, selection_mode="multi", key=f"pref-time-{safe}")
    st.caption(
        "Onsite location is the city or region where you can work on site. "
        "It is required when Onsite is selected. Type none if you have no preferred region."
    )
    st.text_input(
        "Onsite location",
        key=f"pref-location-{safe}",
        max_chars=MAX_PREFERENCE_CHARS,
        help="City or region for onsite work. Type none if you have no preferred region.",
    )
    if not st.button("Save preferences"):
        return
    try:
        preferences = preferences_from_choices(
            job_types=[_JOB_VALUES[item] for item in _chosen_labels(st.session_state.get(f"pref-job-{safe}"), _JOB_LABELS)],
            areas=[_AREA_VALUES[item] for item in _chosen_labels(st.session_state.get(f"pref-area-{safe}"), _AREA_LABELS)],
            schedules=[
                _TIME_VALUES[item] for item in _chosen_labels(st.session_state.get(f"pref-time-{safe}"), _TIME_LABELS)
            ],
            location=st.session_state.get(f"pref-location-{safe}"),
        )
        updated = set_preferences(profile, preferences, update=current is not None)
        commit_profile(path, profile, updated, root=_root())
    except (CareerCopilotError, ValidationError, ValueError) as exc:
        st.error(_public_error(exc))
        return
    _flash(st, "Preferences saved on this profile.")
    st.rerun()


def _conflicts(st, profile) -> None:
    open_conflicts = [conflict for conflict in profile.conflicts if not conflict.resolved]
    if not open_conflicts:
        return
    st.subheader("Conflicts")
    st.caption(
        "A conflict means two documents contradict each other. Both versions stay. "
        "This page does not merge them. Resolve one from the command line."
    )
    for conflict in open_conflicts:
        summary = conflict.summary if isinstance(conflict.summary, str) else ""
        summary = summary.replace("\x00", "")
        if len(summary) > 240:
            summary = summary[:240] + "…"
        st.text(f"{conflict.kind.value}: {summary}")


def _fact_table(st, profile) -> None:
    st.subheader("Extracted facts")
    groups = fact_groups(profile.atoms)
    if not groups:
        st.caption("This profile has no extracted facts yet.")
        return
    for label, rows in groups:
        st.markdown(f"**{label}**")
        st.dataframe(
            [{"Fact": polish(row.statement) or row.statement, "Status": row.confirmation.value} for row in rows],
            hide_index=True,
            alt=f"{label} facts extracted from the documents",
        )


def _page_job(st, path: Path, profile) -> None:
    st.header("Job")
    st.text_area("Job description", key="job_text", height=280, max_chars=MAX_JOB_TEXT)
    if st.button("Analyze"):
        _analyze_job(st, path, profile)
    if st.session_state.get("show_analysis") is not True:
        st.caption("The checklist appears when you analyze. The profile is not changed.")
        return
    try:
        text = require_job_text(st.session_state.get("job_text", ""))
        chosen = _as_of(st)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    cached = st.session_state.get("analysis_cache")
    key = (text, chosen.isoformat())
    if not isinstance(cached, tuple) or cached[0] != key:
        st.caption("Analyze the job again. The profile is not changed.")
        return
    analysis = cached[1]
    _show_analysis(st, analysis)
    _write_choices(st, analysis)


def _write_choices(st, analysis) -> None:
    st.subheader("Documents the posting asks for")
    if not analysis.requested_documents:
        st.caption("None.")
    else:
        for item in analysis.requested_documents:
            st.text(_REQUEST_COPY[item])
    accepted = (
        st.session_state.get("job_accepted") is True
        and st.session_state.get("job_accepted_text") == st.session_state.get("job_text")
    )
    if not accepted:
        st.button("Continue with this job", on_click=_accept_job)
        st.caption("No resume is written until you continue.")
        return
    st.caption("While the model is off, the resume uses confirmed sentences. Nothing is uploaded.")
    st.button("Prepare the resume", on_click=_request_resume)


def _accept_job() -> None:
    import streamlit as st

    st.session_state["job_accepted"] = True
    st.session_state["job_accepted_text"] = st.session_state.get("job_text", "")
    st.session_state["chosen_cover"] = False


def _request_resume() -> None:
    import streamlit as st

    st.session_state["chosen_resume"] = True
    st.session_state["chosen_cover"] = False
    st.session_state["generate_notice"] = ""
    st.session_state["generate_requested"] = True
    st.session_state["page"] = "Generate"


def _request_cover() -> None:
    import streamlit as st

    st.session_state["chosen_resume"] = True
    st.session_state["chosen_cover"] = True
    st.session_state["generate_notice"] = ""
    st.session_state["generate_requested"] = True


def _analyze_job(st, path: Path, profile) -> None:
    try:
        text = require_job_text(st.session_state.get("job_text", ""))
        chosen = _as_of(st)
        analysis = read_job(text, profile.atoms, as_of=chosen)
        previous = st.session_state.get("job_record_id")
        same = st.session_state.get("job_record_text") == text and isinstance(previous, str)
        stored = record_analysis(
            _root(),
            profile=profile,
            profile_key=profile_key(path, profile),
            job_text=text,
            as_of=chosen,
            analysis=analysis,
            record_id=previous if same else None,
        )
    except CareerCopilotError as exc:
        st.session_state["show_analysis"] = False
        st.error(_public_error(exc))
        return
    st.session_state["job_record_id"] = stored.record_id
    st.session_state["job_record_text"] = text
    st.session_state["analysis_cache"] = ((text, chosen.isoformat()), analysis)
    st.session_state["job_accepted"] = False
    st.session_state["show_analysis"] = True
    st.session_state["generate_notice"] = ""


def _page_generate(st, path: Path, profile) -> None:
    st.header("Generate")
    st.caption("Nothing is written until you review the text and save.")
    if st.session_state.get("generate_requested") is True:
        st.session_state["generate_requested"] = False
        raw = st.session_state.get("job_text", "")
        try:
            text = require_job_text(raw)
            as_of = require_as_of(st.session_state.get("as_of"))
        except CareerCopilotError as exc:
            st.error(_public_error(exc))
            return
        _prepare(st, path, profile, text, as_of)
    draft = st.session_state.get("draft")
    if not isinstance(draft, Draft):
        violations = st.session_state.get("draft_violations") or []
        if not violations:
            st.info("Continue with a job on the Job page, then prepare the resume.")
        for violation in violations:
            st.error(_violation_line(violation))
        return
    try:
        text = require_job_text(st.session_state.get("job_text", ""))
        as_of = require_as_of(st.session_state.get("as_of"))
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    _seed_draft_editors(st, draft)
    if draft.violations:
        st.error("No file was written.")
        for violation in draft.violations:
            st.error(_violation_line(violation))
        return
    resume_on = st.session_state.get("draft_resume") is True
    cover_on = st.session_state.get("draft_cover") is True
    if resume_on and draft.source is not None:
        st.caption("Only the summary and Technical Skills can change. A save still checks them against the original CV.")
        _seed_source_editors(st, draft)
        summary = _session_text(st, "cv-summary")
        skills = _session_text(st, "cv-skills")
        _show_cv(st, draft.source, summary, skills)
        st.text_area("Summary", key="cv-summary", height=140, max_chars=MAX_SENTENCE_TEXT)
        st.text_area("Technical Skills", key="cv-skills", height=160, max_chars=MAX_SENTENCE_TEXT)
    elif resume_on:
        st.caption("An edit is saved only when it still matches a confirmed atom. Otherwise no file is written.")
        st.subheader("Resume text")
        _sentence_editors(st, draft.sentences, "resume-sentence")
    if cover_on:
        st.subheader("Cover letter text")
        _sentence_editors(st, draft.cover, "cover-sentence")
    elif resume_on and DocumentRequest.cover_letter in draft.analysis.requested_documents:
        st.info("The posting asks for a cover letter.")
        st.button("Prepare the cover letter", on_click=_request_cover)
    if st.button("Save documents"):
        _save_draft(st, path, profile, draft, text, as_of, resume_on, cover_on)


def _prepare(st, path: Path, profile, text: str, as_of: date) -> None:
    if st.session_state.get("job_accepted") is not True:
        st.session_state.pop("draft", None)
        st.info("Continue with a job before a resume is prepared.")
        return
    resume_on = st.session_state.get("chosen_resume") is True
    cover_on = st.session_state.get("chosen_cover") is True
    if not resume_on and not cover_on:
        st.session_state.pop("draft", None)
        st.session_state["draft_resume"] = False
        st.session_state["draft_cover"] = False
        st.info("Prepare the resume from the Job page.")
        return
    cached = st.session_state.get("analysis_cache")
    key = (text, as_of.isoformat())
    analysis = cached[1] if isinstance(cached, tuple) and cached[0] == key else None
    if analysis is not None and cover_on and DocumentRequest.cover_letter not in analysis.requested_documents:
        cover_on = False
        st.session_state["chosen_cover"] = False
    try:
        draft = draft_documents(
            profile,
            text,
            as_of,
            resume=resume_on,
            cover=cover_on,
            analysis=analysis,
            profile_path=path,
        )
    except CareerCopilotError as exc:
        st.session_state.pop("draft", None)
        st.session_state["draft_resume"] = False
        st.session_state["draft_cover"] = False
        st.session_state["draft_violations"] = []
        st.error(_public_error(exc))
        return
    st.session_state["draft_nonce"] = int(st.session_state.get("draft_nonce", 0)) + 1
    st.session_state["draft_violations"] = list(draft.violations)
    if draft.violations:
        st.session_state.pop("draft", None)
        st.session_state["draft_resume"] = False
        st.session_state["draft_cover"] = False
        return
    st.session_state["draft"] = draft
    st.session_state["draft_resume"] = resume_on
    st.session_state["draft_cover"] = cover_on


def _save_draft(st, path: Path, profile, draft: Draft, text: str, as_of: date, resume_on: bool, cover_on: bool) -> None:
    try:
        sentences = draft.sentences
        cover = draft.cover
        sections = None
        summary_text = ""
        skills_text = ""
        if resume_on and draft.source is not None:
            summary_text = _session_text(st, "cv-summary")
            skills_text = _session_text(st, "cv-skills")
            edited, problems = sentences_from_edits(draft.source, summary_text, skills_text)
            if problems or edited is None:
                st.error("No file was written.")
                for violation in problems:
                    st.error(_violation_line(violation))
                return
            sentences = edited
            sections = preview_sections(draft.source, summary_text, skills_text)
        elif resume_on:
            sentences = with_texts(draft.sentences, _texts_for_save(st, "resume-sentence", draft.sentences))
        if cover_on:
            cover = with_texts(draft.cover, _texts_for_save(st, "cover-sentence", draft.cover))
        result = save_generation(
            _root(),
            profile=profile,
            profile_key=profile_key(path, profile),
            job_text=text,
            as_of=as_of,
            analysis=draft.analysis,
            base_sentences=draft.sentences,
            base_cover=draft.cover,
            base_diff=draft.diff_markdown,
            sentences=sentences,
            cover=cover,
            write_resume_doc=resume_on,
            write_cover=cover_on,
            record_id=_active_record_id(st, text),
            publication_note=draft.publication_note,
            job_label=job_name(text),
            source_sections=sections,
            source_cv=draft.source is not None and resume_on,
            summary_text=summary_text,
            skills_text=skills_text,
        )
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    if not result.written:
        st.error("No file was written.")
        for violation in result.violations:
            st.error(_violation_line(violation))
        return
    _flash(st, "Saved the documents on this computer.")
    st.rerun()


def _page_documents(st, path: Path, profile) -> None:
    st.header("Documents")
    key = profile_key(path, profile)
    records = [record for record in list_generations(_root(), key) if record.status != "discarded"]
    if not records:
        st.caption("No documents generated from this profile yet.")
    else:
        ids = [record.record_id for record in records]
        labels = {record.record_id: _record_label(record) for record in records}
        choice_key = f"record-choice-{key}"
        if st.session_state.get(choice_key) not in ids:
            st.session_state[choice_key] = ids[0]
        st.caption("Choosing a saved document stays on this page.")
        record_id = st.selectbox(
            "Saved document",
            ids,
            format_func=lambda item: labels.get(item, "Saved document"),
            key=choice_key,
        )
        record = next((item for item in records if item.record_id == record_id), None)
        if record is None:
            st.error("That document is not available.")
        else:
            _edit_record(st, path, profile, record)
    _legacy(st)


def _edit_record(st, path: Path, profile, record) -> None:
    stamp = f"{record.record_id}:{record.updated_at.isoformat()}"
    if st.session_state.get("open_record") != stamp:
        if record.source_cv:
            st.session_state[f"doc-cv-summary-{record.record_id}"] = _box_text(
                record.summary_text, MAX_SENTENCE_TEXT, ""
            )
            st.session_state[f"doc-cv-skills-{record.record_id}"] = _box_text(
                record.skills_text, MAX_SENTENCE_TEXT, ""
            )
        else:
            for index, sentence in enumerate(record.sentences):
                st.session_state[f"doc-resume-{record.record_id}-{index}"] = _box_text(
                    _display_sentence(sentence.text), MAX_SENTENCE_TEXT, ""
                )
        for index, sentence in enumerate(record.cover):
            st.session_state[f"doc-cover-{record.record_id}-{index}"] = _box_text(
                _display_sentence(sentence.text), MAX_SENTENCE_TEXT, ""
            )
        st.session_state["open_record"] = stamp
    match = "unknown" if record.tally_percent is None else f"{record.tally_percent:.1f}%"
    title = record.job_name or "Job"
    st.subheader(title)
    st.caption(f"{record.created_at.date().isoformat()} · {record.status} · Match {match} · as of {record.as_of.isoformat()}")
    _record_actions(st, path, profile, record)
    st.caption("An edit is saved only when it still matches a confirmed atom. Otherwise the saved files stay as they are.")
    if record.source_cv and record.wrote_resume:
        st.caption("Only the summary and Technical Skills can change.")
        summary_key = f"doc-cv-summary-{record.record_id}"
        skills_key = f"doc-cv-skills-{record.record_id}"
        source = _saved_source(st, path, profile, record)
        if source is not None:
            _show_cv(st, source, _session_text(st, summary_key), _session_text(st, skills_key))
        st.text_area("Summary", key=summary_key, height=140, max_chars=MAX_SENTENCE_TEXT)
        st.text_area("Technical Skills", key=skills_key, height=160, max_chars=MAX_SENTENCE_TEXT)
        with st.expander("What changed"):
            st.text(polish(record.diff_markdown) if isinstance(record.diff_markdown, str) else "")
    elif record.wrote_resume:
        st.text(polish(record.diff_markdown) if isinstance(record.diff_markdown, str) else "")
        st.subheader("Resume text")
        _sentence_editors(st, record.sentences, f"doc-resume-{record.record_id}")
    if record.wrote_cover:
        st.subheader("Cover letter text")
        _sentence_editors(st, record.cover, f"doc-cover-{record.record_id}")
    try:
        folder = directory_inside(_root(), profile_key(path, profile), record.record_id)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    _download(st, folder / "resume.docx", _root(), "Download resume")
    _download(st, folder / "cover.docx", _root(), "Download cover letter")
    if st.button("Save edited text"):
        _resave(st, path, profile, record)


def _resave(st, path: Path, profile, record) -> None:
    try:
        if record.source_cv and record.wrote_resume:
            _resave_source(st, path, profile, record)
            return
        analysis = analyze_job(record.job_text, profile.atoms, as_of=record.as_of)
        fresh = build_resume(
            profile.atoms,
            analysis,
            cover_letter=record.wrote_cover,
            job_text=record.job_text,
            academic=is_academic(profile),
        )
        if record.wrote_cover and not fresh.cover:
            raise CareerCopilotError("No confirmed role sentence is available for a cover letter.")
        sentences = record.sentences
        cover = record.cover
        if record.wrote_resume:
            sentences = with_texts(
                record.sentences,
                _texts_for_save(st, f"doc-resume-{record.record_id}", record.sentences),
            )
        if record.wrote_cover:
            cover = with_texts(
                record.cover,
                _texts_for_save(st, f"doc-cover-{record.record_id}", record.cover),
            )
        result = save_generation(
            _root(),
            profile=profile,
            profile_key=profile_key(path, profile),
            job_text=record.job_text,
            as_of=record.as_of,
            analysis=analysis,
            base_sentences=fresh.sentences,
            base_cover=fresh.cover,
            base_diff=fresh.diff_markdown,
            sentences=sentences,
            cover=cover,
            write_resume_doc=record.wrote_resume,
            write_cover=record.wrote_cover,
            record_id=record.record_id,
            created_at=record.created_at,
            publication_note=fresh.publication_note,
            job_label=record.job_name or job_name(record.job_text),
        )
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    if not result.written:
        st.error("No file was written.")
        for violation in result.violations:
            st.error(_violation_line(violation))
        return
    _flash(st, "Saved the edited text.")
    st.rerun()


def _resave_source(st, path: Path, profile, record) -> None:
    analysis = analyze_job(record.job_text, profile.atoms, as_of=record.as_of)
    source = assemble_source(profile, analysis, profile_path=path)
    if source is None:
        raise CareerCopilotError("The original CV is not available. Nothing was written.")
    summary = _session_text(st, f"doc-cv-summary-{record.record_id}")
    skills = _session_text(st, f"doc-cv-skills-{record.record_id}")
    sentences, problems = sentences_from_edits(source, summary, skills)
    if problems or sentences is None:
        st.error("No file was written.")
        for violation in problems:
            st.error(_violation_line(violation))
        return
    cover = record.cover
    if record.wrote_cover:
        cover = with_texts(record.cover, _texts_for_save(st, f"doc-cover-{record.record_id}", record.cover))
    result = save_generation(
        _root(),
        profile=profile,
        profile_key=profile_key(path, profile),
        job_text=record.job_text,
        as_of=record.as_of,
        analysis=analysis,
        base_sentences=record.sentences,
        base_cover=record.cover,
        base_diff=record.diff_markdown,
        sentences=sentences,
        cover=cover,
        write_resume_doc=True,
        write_cover=record.wrote_cover,
        record_id=record.record_id,
        created_at=record.created_at,
        publication_note=False,
        job_label=record.job_name or job_name(record.job_text),
        source_sections=preview_sections(source, summary, skills),
        source_cv=True,
        summary_text=summary,
        skills_text=skills,
    )
    if not result.written:
        st.error("No file was written.")
        for violation in result.violations:
            st.error(_violation_line(violation))
        return
    _flash(st, "Saved the edited text.")
    st.rerun()


def _saved_source(st, path: Path, profile, record):
    try:
        analysis = analyze_job(record.job_text, profile.atoms, as_of=record.as_of)
        return assemble_source(profile, analysis, profile_path=path)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return None


def _legacy(st) -> None:
    files = legacy_docx(Path.cwd() / "out")
    if not files:
        return
    st.subheader("Earlier command-line files")
    st.caption("These files stay as they are. Download a copy to read it.")
    root = Path.cwd() / "out"
    for path in files:
        _download(st, path, root, path.name)


def _sentence_editors(st, sentences, prefix: str) -> None:
    for index, sentence in enumerate(sentences):
        key = f"{prefix}-{index}"
        stored = sentence.text if isinstance(sentence.text, str) else ""
        shown = _display_sentence(stored)
        too_long = len(stored) > MAX_SENTENCE_TEXT or "\x00" in stored
        if too_long:
            st.text(shown or stored)
            st.caption("Type a shorter version to replace this sentence.")
        fallback = "" if too_long else shown
        st.session_state[key] = _box_text(st.session_state.get(key, fallback), MAX_SENTENCE_TEXT, fallback)
        evidence = sentence.evidence_ids[0] if sentence.evidence_ids else "sentence"
        st.text_area(
            f"{sentence.kind.value} · {evidence}",
            key=key,
            height=80,
            max_chars=MAX_SENTENCE_TEXT,
        )


def _seed_draft_editors(st, draft: Draft) -> None:
    stamp = f"{st.session_state.get('draft_nonce')}:{len(draft.sentences)}:{len(draft.cover)}"
    if st.session_state.get("editor_stamp") == stamp:
        return
    for index, sentence in enumerate(draft.sentences):
        st.session_state[f"resume-sentence-{index}"] = _box_text(
            _display_sentence(sentence.text), MAX_SENTENCE_TEXT, ""
        )
    for index, sentence in enumerate(draft.cover):
        st.session_state[f"cover-sentence-{index}"] = _box_text(
            _display_sentence(sentence.text), MAX_SENTENCE_TEXT, ""
        )
    st.session_state["editor_stamp"] = stamp


def _editor_texts(st, prefix: str, count: int) -> list[str]:
    texts: list[str] = []
    for index in range(count):
        value = st.session_state.get(f"{prefix}-{index}", "")
        if not isinstance(value, str):
            raise CareerCopilotError("Sentence text must be text.")
        texts.append(value)
    return texts


def _texts_for_save(st, prefix: str, sentences) -> list[str]:
    texts = _editor_texts(st, prefix, len(sentences))
    cleaned: list[str] = []
    for sentence, text in zip(sentences, texts):
        stored = sentence.text if isinstance(sentence.text, str) else ""
        if not text.strip() and (len(stored) > MAX_SENTENCE_TEXT or "\x00" in stored):
            raise CareerCopilotError("Shorten the long sentence before saving a change.")
        cleaned.append(_stored_or_edit(text, stored))
    return cleaned


def _display_sentence(stored: str) -> str:
    if not isinstance(stored, str):
        return ""
    shown = polish(stored)
    return shown or stored


def _stored_or_edit(edited: str, stored: str) -> str:
    """Keep the atom text when the box still shows only the display cleanup."""

    if edited == stored:
        return stored
    shown = polish(stored)
    if shown and edited == shown:
        return stored
    return edited


def _show_analysis(st, analysis) -> None:
    if analysis.model_note == DAILY_LIMIT_NOTE:
        st.warning(analysis.model_note)
    else:
        st.info(analysis.model_note)
    percent = analysis.tally.percent
    st.metric("Match", "unknown" if percent is None else f"{percent:.1f}%")
    st.caption("Judged requirements only. Unknown lines are excluded. This percentage does not add a sentence.")
    matching = [
        item.text
        for item in analysis.requirements
        if item.status == RequirementStatus.met and isinstance(item.text, str)
    ]
    gaps = [item for item in analysis.requirements if item.status != RequirementStatus.met and isinstance(item.text, str)]
    st.subheader("Matching points")
    if matching:
        for text in matching:
            st.text(f"- {_short_line(text)}")
    else:
        st.caption("None.")
    st.subheader("Not a full match")
    if gaps:
        for item in gaps:
            label = _GAP_LABELS.get(item.status, item.status.value)
            st.text(f"- {label}: {_short_line(item.text)}")
    else:
        st.caption("None.")
    st.caption("Partial and missing lines are why a match is below 100%. Not judged lines stay out of the percentage.")
    signal, reason = fit_feedback(analysis)
    st.subheader("Feedback")
    st.badge(
        signal,
        color="green" if signal == "Positive" else "red",
        icon=":material/thumb_up:" if signal == "Positive" else ":material/thumb_down:",
    )
    st.caption(reason)
    with st.expander("Full checklist"):
        st.text(_format_analysis(analysis))


def _download(st, path: Path, root: Path, label: str) -> None:
    allowed = path_inside(path, root)
    if allowed is None or allowed.suffix.casefold() != ".docx":
        return
    try:
        size = allowed.stat().st_size
        payload = allowed.read_bytes()
    except OSError:
        return
    if size > MAX_DOWNLOAD_BYTES or len(payload) > MAX_DOWNLOAD_BYTES:
        st.caption(f"{allowed.name} is too large to download here.")
        return
    st.download_button(
        label,
        data=payload,
        file_name=allowed.name,
        key=f"download-{allowed.parent.name}-{allowed.name}",
    )


def _prepare_session(st) -> None:
    job_text = st.session_state.get("job_text", "")
    if not isinstance(job_text, str) or "\x00" in job_text:
        st.session_state["job_text"] = ""
    elif len(job_text) > MAX_JOB_TEXT:
        st.session_state["job_text"] = job_text[:MAX_JOB_TEXT]
    elif "job_text" not in st.session_state:
        st.session_state["job_text"] = ""
    st.session_state["as_of"] = _today_in_range()


def _profile_label(path: Path) -> str:
    try:
        profile = load_profile(path)
    except CareerCopilotError:
        return "User"
    name = person_name(profile)
    if name and name != "Profile":
        return name
    return "User"


def _as_of(st) -> date:
    chosen = _today_in_range()
    st.session_state["as_of"] = chosen
    return chosen


def _session_text(st, key: str) -> str:
    value = st.session_state.get(key, "")
    if not isinstance(value, str):
        raise CareerCopilotError("That text must be text.")
    return value


def _seed_source_editors(st, draft: Draft) -> None:
    if draft.source is None:
        return
    stamp = f"source:{st.session_state.get('draft_nonce')}:{len(draft.source.summary_text)}:{len(draft.source.skills_text)}"
    if st.session_state.get("source_stamp") == stamp:
        return
    st.session_state["cv-summary"] = _box_text(draft.source.summary_text, MAX_SENTENCE_TEXT, "")
    st.session_state["cv-skills"] = _box_text(draft.source.skills_text, MAX_SENTENCE_TEXT, "")
    st.session_state["source_stamp"] = stamp


def _show_cv(st, source, summary: str, skills: str) -> None:
    with st.container(border=True):
        for section in preview_sections(source, summary, skills):
            if section.heading:
                st.markdown(f"**{_md(section.heading)}**")
            for line in section.lines:
                text = _md(line.text.strip())
                if not text:
                    continue
                if line.style == "name":
                    st.markdown(f"**{text}**")
                elif line.style == "role":
                    prefix = "- " if line.bullet else ""
                    st.markdown(f"**{prefix}{text}**")
                elif line.bullet:
                    st.markdown(f"- {text}")
                else:
                    st.markdown(text)


def _md(text: str) -> str:
    escaped = text
    for char in ("\\", "`", "*", "_", "[", "]"):
        escaped = escaped.replace(char, "\\" + char)
    return escaped


def _record_label(record) -> str:
    when = record.created_at.strftime("%Y-%m-%d")
    match = "unknown" if record.tally_percent is None else f"{record.tally_percent:.1f}%"
    parts: list[str] = [record.status]
    if record.wrote_resume:
        parts.append("resume")
    if record.wrote_cover:
        parts.append("cover")
    title = record.job_name or "Job"
    if len(title) > 60:
        title = title[:60] + "…"
    return f"{when} · {match} · {', '.join(parts)} · {title}"


def _record_actions(st, path: Path, profile, record) -> None:
    key = profile_key(path, profile)
    if record.status == "submitted" and record.sent_on is not None:
        st.caption(f"Sent on {record.sent_on.isoformat()}. This record stays on this computer.")
    elif record.status == "kept":
        st.caption("If you sent this application, record the date. Nothing is uploaded.")
        sent_key = f"sent-on-{record.record_id}"
        if sent_key not in st.session_state:
            st.session_state[sent_key] = record.sent_on or _today_in_range()
        st.date_input("Date sent", key=sent_key, min_value=AS_OF_MIN, max_value=AS_OF_MAX)
        if st.button("Save as sent", key=f"submit-{record.record_id}"):
            _mark_sent(st, key, record.record_id, st.session_state.get(sent_key))
    keep_col, discard_col = st.columns(2)
    if record.status == "draft" and keep_col.button("Keep", key=f"keep-{record.record_id}"):
        _mark_status(st, key, record.record_id, "kept")
    if discard_col.button("Discard", key=f"discard-{record.record_id}"):
        _mark_status(st, key, record.record_id, "discarded")


def _mark_status(st, key: str, record_id: str, status: str) -> None:
    try:
        set_record_status(_root(), key, record_id, status)
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    _flash(st, "Saved the choice on this computer.")
    st.rerun()


def _mark_sent(st, key: str, record_id: str, sent_on: object) -> None:
    try:
        set_record_status(_root(), key, record_id, "submitted", sent_on=require_as_of(sent_on))
    except CareerCopilotError as exc:
        st.error(_public_error(exc))
        return
    _flash(st, "Saved the application on this computer.")
    st.rerun()


def _active_record_id(st, text: str) -> str | None:
    record_id = st.session_state.get("job_record_id")
    if st.session_state.get("job_record_text") == text and isinstance(record_id, str):
        return record_id
    return None


def _init_text(st, key: str, value: str) -> None:
    current = st.session_state.get(key, value)
    st.session_state[key] = _box_text(current, MAX_PREFERENCE_CHARS, _box_text(value, MAX_PREFERENCE_CHARS, ""))


def _init_choice(st, key: str, labels: list[str], allowed: list[str]) -> None:
    current = st.session_state.get(key)
    if isinstance(current, list) and all(item in allowed for item in current):
        return
    st.session_state[key] = list(labels)


def _labels_for(values: list[str], mapping: dict[str, str], fallback: list[str]) -> list[str]:
    reverse = {value: label for label, value in mapping.items()}
    chosen = [reverse[item] for item in values if item in reverse]
    return chosen or list(fallback)


def _chosen_labels(value: object, allowed: list[str]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or item not in allowed for item in value):
        raise CareerCopilotError("Choose a preference from the list.")
    chosen: list[str] = []
    for item in value:
        if item not in chosen:
            chosen.append(item)
    return chosen


def _required_name(value: object) -> str:
    if not isinstance(value, str):
        raise CareerCopilotError("Name must be text.")
    if "\x00" in value:
        raise CareerCopilotError("Name contains a null character.")
    stripped = value.strip()
    if not stripped:
        raise CareerCopilotError("Name is required.")
    if len(stripped) > MAX_PREFERENCE_CHARS:
        raise CareerCopilotError("Name is too long.")
    return stripped


def _document_kind(value: object) -> DocumentKind:
    if not isinstance(value, str) or value not in _KIND_LABELS:
        raise CareerCopilotError("The document type is not valid.")
    return _KIND_LABELS[value]


def _upload_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _ingest_uploads(profile, uploads: list, kind: DocumentKind):
    warnings: list[str] = []
    updated = profile
    for upload in uploads:
        file_name, payload = _read_upload(upload)
        updated, _count, file_warnings = ingest_bytes(updated, name=file_name, payload=payload, kind=kind)
        warnings.extend(file_warnings)
    return updated, warnings


def _read_upload(upload) -> tuple[str, bytes]:
    name = getattr(upload, "name", None)
    getter = getattr(upload, "getvalue", None)
    if not isinstance(name, str) or not callable(getter):
        raise CareerCopilotError("The file could not be read.")
    payload = getter()
    if not isinstance(payload, (bytes, bytearray)):
        raise CareerCopilotError("The file could not be read.")
    return name, bytes(payload)


def _show_warnings(st, warnings: list[str]) -> None:
    for warning in warnings:
        if isinstance(warning, str) and warning and "\x00" not in warning and len(warning) <= 240:
            st.warning(warning)


def _short_line(text: str) -> str:
    cleaned = text.replace("\x00", "").replace("\n", " ").strip()
    if len(cleaned) > 240:
        return cleaned[:240] + "…"
    return cleaned


def _root() -> Path:
    return generated_root()


def _safe_key(path: Path, profile) -> str:
    return profile_key(path, profile)


def _flash(st, message: str) -> None:
    st.session_state["flash"] = message


def _preference_text(value: object, *, required: bool) -> str | None:
    if not isinstance(value, str):
        raise CareerCopilotError("That answer must be text.")
    if "\x00" in value:
        raise CareerCopilotError("That answer contains a null character.")
    stripped = value.strip()
    if len(stripped) > MAX_PREFERENCE_CHARS:
        raise CareerCopilotError("That answer is too long.")
    if stripped:
        return stripped
    if required:
        raise CareerCopilotError("Onsite location is required. Type none if you have no preferred region.")
    return None


def _box_text(value: object, limit: int, fallback: str) -> str:
    if isinstance(value, str) and "\x00" not in value and len(value) <= limit:
        return value
    if "\x00" not in fallback and len(fallback) <= limit:
        return fallback
    return ""


def _today_in_range() -> date:
    today = date.today()
    if today < AS_OF_MIN:
        return AS_OF_MIN
    if today > AS_OF_MAX:
        return AS_OF_MAX
    return today


def _is_loopback(address: object) -> bool:
    if address is None:
        return True
    if not isinstance(address, str):
        return False
    return address.strip().strip("[]").lower() in _LOOPBACK


def _violation_line(violation: object) -> str:
    code = getattr(violation, "code", None)
    detail = getattr(violation, "detail", None)
    if not isinstance(code, str) or not code.strip():
        code = "rejected"
    if not isinstance(detail, str):
        detail = ""
    detail = detail.replace("\x00", "").strip()
    if len(detail) > 240:
        detail = detail[:240] + "…"
    if not detail:
        return "A sentence was rejected."
    return f"{code}: {detail}"


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError) and exc.errors():
        message = str(exc.errors()[0].get("msg", ""))
        prefix = "Value error, "
        if message.startswith(prefix):
            message = message[len(prefix) :]
        if "empty" in message:
            return "Onsite location is required. Type none if you have no preferred region."
        if message and "\x00" not in message and len(message) <= 240:
            return message
    if isinstance(exc, ValueError):
        return "Choose a preference from the list."
    return _UNEXPECTED


def _public_error(exc: BaseException) -> str:
    if isinstance(exc, CareerCopilotError):
        text = str(exc).replace("\x00", "").strip()
        if text and len(text) <= 500:
            return text
        return _UNEXPECTED
    if isinstance(exc, (ValidationError, ValueError)):
        return _error_text(exc)
    return _UNEXPECTED


if __name__ == "__main__":
    main()
