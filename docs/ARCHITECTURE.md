# Architecture

This file describes the program that opens today. `SPEC.md` and `AGENTS.md` still describe an older pipeline. That code remains in the repository. It is not on the four pages below.

The match rules are in `docs/MATCH.md`. They apply to every field. Synonyms come from a local ESCO name file, `src/career_copilot/data/esco_labels.json`, when that file is present. Terms are read only under a candidate heading, including Dutch headings such as functie-eisen and jouw profiel. A page with no such heading is saved with its link and is not shown as 100 percent. Menu fragments are left out of Possible matches.

The program reads one CV, names the kinds of work that experience supports, searches for open jobs, and shows a match. The opened job can write a CV and, when the posting asks, a cover letter. Documents lists those files and records whether the person said they applied. The person reviews every file and submits the application. The program does not apply to jobs.

## How to open it

From the project folder, after `grok login` has been done on this computer:

```powershell
.\.venv\Scripts\career-copilot.exe ui
```

The command prints `http://127.0.0.1:8501` and waits. Open that address. Stop the command to close the page. Do not set `XAI_API_KEY`. The page starts the `grok` program that is already signed in. It does not call `https://api.x.ai`. It cannot read the weekly quota, so it does not enforce a spending cap.

## What phase 1 does

Phase 1 is the page you open. Its heading is User.

1. The person uploads one CV, a DOCX or a single-column text PDF.
2. Python reads the text on this computer. A scanned page or a multi-column file is refused in one sentence.
3. The page starts `grok` with `--prompt-file`, one turn, and tools that edit files, run commands, or use the web turned off. The process uses the Grok login already on this computer. `XAI_API_KEY` is removed from that process, so the call spends the existing Grok allowance and does not spend console credits.
4. Grok returns up to eight roles, highest priority first. Each role is a short name, such as professor, secretary, architect, or researcher, plus one sentence saying why the experience supports it. It also returns up to twelve skill names. A skill is kept only when that name appears in the CV. Read the CV writes those names to `.career-copilot/skills.json`.
5. The page shows the roles. It does not show a skills box, and it does not show lines copied from the CV.
6. The person keeps roles, removes roles, and can type a role that was not suggested.
7. Save writes `.career-copilot/roles.json`. That file stores the chosen roles. It does not store the CV text. Skills are edited later, on the Jobs profile card.
8. After that save, Continue opens the Search page.

## What phase 2 does

Phase 2 is the Search page. It does not look for jobs.

1. Until the roles are saved, the page says to save the roles first.
2. No question is required. A blank answer is not a filter. The person can choose more than one option for experience level (Entry level, Senior, Manager, Director, Executive), employment type (Part-time, Full-time, Internship, Volunteer, Contract), and workplace (Local, Remote, Hybrid). There is no Mid-level.
3. Country, city, and languages are always shown. No country means worldwide. A country without a city covers the whole country. Remote ignores the city. A typed country stays stored when Remote is also selected. A typed language removes an automatic result whose description is written in another language, and a posting that requires a language that was not typed. The language is read from the description after the site menu is removed. A posting that does not show its language stays, with a note. An English description that only mentions another language as a plus stays. Other notes is an optional box of up to 1000 characters. Those lines guide the search and the match. They do not remove a job by themselves. An older file can still contain a programming value or an outside-academia value. Save on this page clears those two fields. Save profile on Jobs keeps them.
4. Save writes `.career-copilot/search.json`. The file stores the choices. It does not store the CV.
5. After that save, Continue opens the Jobs page.

## What phase 3 does

Phase 3 is the Jobs page. Automatic search returns the first open job whose quick CV match is at least 65 percent. If none reach that, it returns the best opened page.

1. Until the roles and the search conditions are saved, the page says to save them first. A saved search can leave every field blank.
2. Read the CV saves the text in `.career-copilot/cv.txt` on this computer. Jobs uses that file for the match. It is not sent to a job site and it is not copied into `jobs.json`. Opening Jobs does not search. Above the search buttons, a profile card shows the saved roles and the reason for each one, an editable skills list, the place and the languages as read-only text, and Other notes. Save profile writes the skills and the notes. It does not show the saved Yes and No answers. Automatic search and Match analysis save that card first. The place and the languages are changed on the Search page. The card does not show CV lines, employers, or dates.
3. One press builds two or three short queries from the saved skills, up to two skills in each query, or from the kept role names when no skill is saved. Each query adds the saved country, and the word remote when Remote is selected. It ends with job OR vacancy. City, level, employment, language, notes, and the year are not in the query. Each query is one `grok` call on `grok-4.7-build-fast`, at most two turns, and a 60 second wait. The call asks only for valid https links for open jobs, then stops. Python does not send the CV in that call. A reply from another model counts as no link. Python opens up to three of those links. It drops LinkedIn, private addresses, empty pages, and login walls. It drops a page that says applications are closed, or whose deadline has passed. A deadline such as 15 Nov '26 counts, including when the date is on the next line. Python then lists the saved skills that are written in both the CV and the opened page. A skill that is not in the page is left out of What is missing. The first open page with a clear match is saved and the search stops. If no opened page has a clear match, the best opened page is saved anyway. If no page was opened, the line is Nada foi encontrado. A link the person already removed is skipped. `XAI_API_KEY` is removed from that process. Python still removes the site menu and a topic menu above the vacancy from the stored description.
4. Match analysis still uses the filled search fields. Level, employment, workplace, country, city, and language can add a missing point. An empty field is ignored. A difference, such as hybrid while Remote is selected, or an experience level outside the saved levels, is a missing point. The pasted job stays when it reaches 50 percent. A job that does not mention the workplace, the place, the employment type, the experience level, or a language stays, with a note. A description written in a language that was not saved is still judged on a paste. Automatic search does not apply that language check. Contract covers freelance, contractor, per-task, and a month contract. A sentence that only says someone will sign a contract does not count. Other notes do not add a hard gap. The unused feed parsers still use the 50 percent bar.
5. Automatic search shows that one open job and its quick match percent, including when that job is the best page under 65 percent. A result from an older search that was not scored still says Not scored. A scored result from Match analysis is listed when it reaches 50 percent. The table lists at most three scored jobs at 50 percent or above, highest first. A saved log that already carries the old under-50 sentence still shows that one job and the sentence. If no page was opened, the line is only "Nada foi encontrado." A job the person already kept stays on the list. When some scored jobs fall short and others are shown, the page says how many fell short.
6. The table shows the title, the company, where the job was found, and the match percentage. Choosing a row shows a header and a cleaned posting, not the raw page. The header has the company, the place, the workplace, the deadline when one was read, and the link. The posting keeps the title line, the deadline, the vacancy id, hours, salary, location, the real section names, and the job description. It drops blank runs, dash lines, navigation, country lists, language switchers, and consent comments. A block of short lines that repeats, such as Home, Technology, and Careers, is the site menu and is left out. A long topic menu above the vacancy is left out, including when each topic is a bullet, and including Go to, terug, and What are you searching for? A short list that appears once stays. Requirement bullets that belong to the vacancy stay. Then the row shows the match, what matches, Possible matches, what is missing, notes, and the documents. An automatic result shows the quick match percent, the skills found in the posting, and the skills that were missing. An older automatic result that was not scored says Not scored and does not show those three judged sections. A scored paste shows the match percent. A full match is a requirement the CV states. A possible match is a partial line or an assumed line. It stays out of the percent until the person answers. Yes moves that line into What matches now, saves a strength, and recalculates this job's percent. No moves it into What is missing now, saves a gap, and recalculates the percent. Automatic search finds an open job and opens its link. What matches lists terms clearly written in both the CV and the job. A required skill, technology, or years-of-experience line from the requirements that is not in the CV is a gap, so the percent is not 100. Possible matches are the skill or technology phrases from that section that only partly appear in the CV. Site names, amounts, deadlines, and marketing language are left out. Yes adds a possible match to What matches and counts it. No moves it into What is missing and recalculates the percent. The same answer is used on the next search. A line with no answer stays under Possible matches and is not saved. Neither answer changes the CV. An interview-style line, such as explaining a change clearly, stays out of the percent. A CV is always listed. A cover letter, portfolio, transcript, writing sample, certificate, or ID is listed only when the posting names it. A letter of motivation counts as a cover letter. Generate CV for this job is on every opened job. Generate cover letter for this job appears only when a cover letter is named. The other named documents say to bring your own file and have no Generate button. Generate CV writes an Amsterdam PDF for that job: A4, one column, no photo, and no table, from the CV saved on the User page. If the model text cannot be used, the PDF is still written and the page says that the summary and the skills were left as written in the CV. A skill may use the job's wording, including a plural, when the CV already contains that skill. A different idea is left out. Experience, education, publications, languages, and contact stay word for word. The summary and Technical Skills may change, and only with facts already in that CV. A sentence that fails that check is dropped. If a locked section would change, nothing is written. The preview appears when the file exists. The file name opens it again, and the page offers the PDF download. Notes beside the CV file name applies a change to the summary and Technical Skills only. A note that asks to change a locked section is refused, and the file stays as it was. The cover letter uses the same fact check, has no Notes button, and Generate again replaces that job's letter. Each job keeps its own files under `.career-copilot/documents/`. Writing one job does not change another job or `cv.txt`. After that CV file exists, the page asks Did you apply? Yes and Not yet are stored with that vacancy. Nothing is submitted. When nothing else is named, the note says that only the CV is required. Keep this job, beside the title, keeps that job. Remove this job drops it, and a later search skips that link. Kept jobs stay when Automatic search runs.
7. Manual search opens one text box. Paste the posting, or paste one https link. A lone link is read with a direct request. The CV is not sent. LinkedIn, a private address, an empty page, and a login wall ask for the posting text instead. Match analysis scores the paste. Automatic search uses the quick skill tally on the opened page. A workplace, place, employment, or language conflict is one of the missing points on a paste. The paste is added to the table when it reaches 50 percent. A pasted posting does not start a web search.

## What the Documents page does

Documents lists every CV written on this computer.

1. Each row shows the vacancy, the company, the date the CV was written, and whether the person said they applied.
2. The row can open or download that CV, and the cover letter when one was written.
3. Yes and Not yet on this page change the stored answer. The same answer is on the opened job. The program does not send the application.
4. The list is `.career-copilot/applications.json`. It stores the title, the company, the date, the answer, and the key that finds the files. It does not store the CV text. Writing the CV again updates the date and keeps the answer.

Document writing is on the opened job. The public job-board parsers remain in the module for tests. The page does not call them.

## What was done

- `src/career_copilot/roles.py` reads the file, calls the local `grok` command, and parses the roles and the skill names.
- `src/career_copilot/search_intent.py` checks and stores the search conditions. Blank answers are allowed.
- `src/career_copilot/job_list.py` builds two or three short queries and asks the fastest `grok` model for https links. It opens those pages and returns the first one whose quick CV match is at least 65 percent. If none reach that, it returns the best opened page. Closed applications and past deadlines are left out. Match analysis still scores a paste, reads `traits.json`, and uses a saved Yes or No on the next paste. A link the person already removed stays out.
- `src/career_copilot/job_documents.py` writes one job's Amsterdam CV and, when asked, its cover letter. Python checks the new sentences. The model does not judge them. A skill may use the job's wording when the CV already contains that skill. When the model text cannot be used, the CV PDF is still written from the saved CV.
- `src/career_copilot/applications.py` stores the vacancy, the company, the date, and whether the person said they applied. It does not store the CV text, and it does not submit anything.
- `src/career_copilot/ui_app.py` has four pages: User, Search, Jobs, and Documents. User saves roles. Jobs shows the profile card, waits for Automatic search or Manual search, shows a cleaned posting, and moves Yes or No into What matches or What is missing. The saved answers stay in `traits.json` and are not listed on the page. The opened job can write the CV and the cover letter. After the CV exists, Did you apply? stores Yes or Not yet. Documents lists those CVs.
- The saved results are `.career-copilot/roles.json`, `.career-copilot/skills.json`, `.career-copilot/search.json`, `.career-copilot/jobs.json`, `.career-copilot/traits.json`, and `.career-copilot/applications.json`. The CV text is in `.career-copilot/cv.txt` only. One job's written files are in `.career-copilot/documents/`.
- Tests cover the role parser, grounded skills, optional search fields, other notes, the workplace rule, half matches, the 50 percent bar on paste and the unused feed parsers, web discovery without a live call, a missing deadline, assumed points on a paste, the first open page at 65 percent or the best opened page, a pasted posting, the Jobs page, job documents, the topic menu, and the application list. Those tests do not call `grok`.

## Still to do

No next writing phase is waiting. The opened job writes the CV and, when the posting asks, the cover letter. Documents keeps the local list. The person reviews the file. The program does not submit it.

Automatic search builds two or three short queries, asks the fast model for https links, opens those pages, and returns the first one at 65 percent or the best page it opened.

`source_cv.py` can copy one named CV for the older pipeline. An unused Generate screen in `ui_app.py` calls it. Neither is in the menu.

Not on the open pages:

- The page cannot read the Grok quota. It does not stop at 10 percent of a weekly allowance. Do not set an API key to work around that.
- LinkedIn stays paste-in. The search does not use a browser, cookies, or a search-engine scrape.
- The public job-board parsers stay in `job_list.py` for tests. The page does not call them.
- Interview preparation is not on the page.
- Mid-level is not an experience-level choice. The person can write that preference in Other notes.
- A golden set of 15–20 CVs is not the check for these four pages.
- `SPEC.md` and `AGENTS.md` still describe the older pipeline. This file and `Project-Vision-and-Next-Steps.pdf` describe the program that opens.

The modules below this section are the older pipeline. They remain in the repository. The page does not show them.

## Older pipeline

```text
DOCX or single-column text PDF
        │
        ▼
parse_document          text blocks, file hash, layout refusal
        │
        ▼
extract_atoms           atoms, confirmation = extracted
        │
        ▼
which profile?          strong signal attaches, uncertain asks, otherwise a new profile
        │
        ▼
detect_conflicts        disagreements kept side by side, inside that profile only
        │
        ▼
human confirm / edit    confirmation = confirmed
preferences             saved once on that profile
        │
        ▼
one profile.json        local, separate from every other profile
        │
        ├─ job            checklist, tally, and years, read only
        │
        ├─ resume         show the checklist, ask, then validator → DOCX + markdown diff
        │
        └─ ui             the same checks, in a browser page on 127.0.0.1
```

`job` and `resume` do not write `profile.json`. Generation reads only atoms whose `confirmation` is `confirmed`, and only after the person confirms which documents to write. Preferences are stored and are not used to search for jobs.

## Project structure

```text
pyproject.toml              package career-copilot, version 0.1.0
SPEC.md                     product contract
AGENTS.md                   build constraints for this repository
src/career_copilot/
  cli.py                    argument parser and command handlers
  models.py                 Pydantic models, extra fields forbidden
  parsers.py                DOCX and PDF readers
  layout.py                 single-column check and visual line joining
  dates.py                  date ranges copied from a line
  vocab.py                  CV section headings and the skill lexicon
  ownership.py              closed ownership-verb table
  extract.py                blocks → atoms
  conflicts.py              cross-document disagreements
  grounding.py              atoms must still quote the stored document
  profile.py                load, save, ingest, confirm, edit, resolve
  identity.py               which profile a new document belongs to
  preferences.py            work-preference questions
  job.py                    pasted job text → checklist and tally
  years.py                  employment interval union
  resume.py                 confirmed atoms → sentences, cover letter, and diff
  presentation.py           section labels, publication check, accent cleanup, cover length
  validator.py              reject an ungrounded sentence
  docx_export.py            single-column DOCX
  inputs.py                 limits for typed text, dates, ids, and local paths
  library.py                profile list, saved generations, edits validated before write
  roles.py                  CV text to roles and grounded skills through the local grok command
  search_intent.py          search conditions, including other notes
  job_list.py               web search, match, and the job list
  job_documents.py          Amsterdam CV and cover letter for one opened job
  applications.py           local list of written CVs and whether the person applied
  source_cv.py              copy of one named CV; summary and skills may change. Not on the open menu.
  ui_app.py                 User, Search, Jobs, and Documents. Older screens stay in the file and are not in the menu.
  errors.py                 CareerCopilotError, ParseError, ProfileError
.streamlit/config.toml      loopback address, usage stats off
tests/                      deterministic pytest suite
golden/                     optional extra real CVs
profile.json                confirmed profile used in this workspace
```

Entry point: `career-copilot = career_copilot.cli:main`. The package can also be run as `python -m career_copilot`.

Runtime dependencies are `pdfplumber`, `pydantic`, `python-docx`, `reportlab`, and `streamlit`. The development dependency is `pytest`. There is no OpenAI, Gemini, Anthropic, xAI, LangChain, or LangGraph package. User, Search, Jobs, and Documents call the `grok` program already installed on this computer when a page needs it. They do not call `https://api.x.ai`.

## Pure Python

The table below is the older pipeline. The open pages are `roles.py`, `search_intent.py`, `job_list.py`, and `ui_app.py`. Those pages ask the local `grok` command for roles, for pages, and for match points. Python drops a skill that is not in the CV, drops a page that does not fit, and computes the percent. Grok does not decide that its own score is final.

Every step in the table is ordinary Python. Rules, regular expressions, hashes, and Pydantic validation decide the result. The same inputs produce the same atoms, checklist, and resume.

| Module | Role |
| :--- | :--- |
| `parsers.py`, `layout.py` | Read a file and refuse a multi-column layout. |
| `extract.py`, `dates.py`, `vocab.py`, `ownership.py` | Propose atoms from the text. |
| `conflicts.py` | Propose disagreements. |
| `profile.py`, `preferences.py`, `cli.py` | Store confirmation and work preferences. |
| `identity.py` | Choose a profile for a new document. Profiles are not merged. |
| `job.py`, `years.py` | Judge a pasted job and count the checklist. |
| `resume.py` | Select and copy confirmed atoms. The CLI asks before writing files. |
| `validator.py` | Accept or reject the package. An LLM is not the judge. |
| `docx_export.py` | Write the file. |
| `library.py` | List local profiles and save a generation only after validation. |
| `ui_app.py` | Show the same steps in a browser on this computer. |

`resume.py` is the proposer for the older pipeline, and it does not paraphrase. `validator.py` still has to accept a resume sentence from that pipeline. The open pages do not call `resume.py`.

## Data model

Pydantic models use `extra = "forbid"`. A field that is not in the schema is a validation error.

### Identifiers

| Id | Form | How it is chosen |
| :--- | :--- | :--- |
| `document_id` | `doc_` + 16 hex characters | First 16 hex characters of the SHA-256 of the file bytes. |
| `evidence_id` | `ev_` + 16 hex characters | SHA-256 of `document_id`, the atom's ordinal in that extraction, and the excerpt. |
| `conflict_id` | `cf_` + 16 hex characters | SHA-256 of the conflict kind and the two evidence ids in sorted order. |
| `profile_id` | `pf_` + 16 hex characters | Random id assigned the first time the profile is saved. |

Re-ingesting the same bytes replaces that document and its atoms, because the document id is the content hash. Editing an atom keeps its `evidence_id`.

### Evidence category

Each atom has one category. The category is a claim about the source, not a rank the generator may upgrade.

| Value | Meaning in extraction |
| :--- | :--- |
| `used_in_job` | Text under an experience heading. |
| `project` | Text under a projects or publications heading, or the default for `portfolio_note`. |
| `coursework` | Text under an education heading, or the default for `certificate`. |
| `mentioned_only` | Summary, skills, languages, and any other section. |

### Confirmation

| State | Who sets it |
| :--- | :--- |
| `extracted` | Extraction. The atom is stored and is ignored by `job` and `resume`. |
| `confirmed` | `confirm`, `confirm-all`, or `review`. Required before a sentence may cite the atom. |
| `externally_verified` | Reserved in the schema. No command sets it. Confirm leaves it unchanged. |

### Dates

An `ExactDate` stores `raw`, `year`, and optional `month` and `day`. A missing part stays missing. The year must be from 1900 through 2100. The raw text is what later checks copy.

Recognized tokens are English and Portuguese month names, `Mon YYYY`, `D Mon YYYY`, `Mon D, YYYY`, `M/YYYY` or `M-YYYY`, and a bare year. A range uses `-`, an en dash, an em dash, `to`, `até`, or `ate`. Open ends are `present`, `current`, `now`, `ongoing`, `today`, `atual`, `atualidade`, `presente`, `hoje`, and `corrente`. The parser keeps the last valid range on the line. German month names such as `Dezember` are not recognized.

### Ownership verbs

The closed table in `ownership.py` is:

`responsible for`, `architected`, `owned`, `led`

A verb is stored only when that exact word or phrase is in the excerpt. The generator does not add a verb. The validator rejects a sentence whose verb is absent from the cited excerpt. There is no strength ladder and no synonym verb.

### Skill lexicon and the one synonym group

`vocab.SKILL_LEXICON` is the only general skill detector. A term is recorded when it appears as its own word. Short names `Go`, `R`, and `C` are omitted on purpose. The list is:

Airflow, Ansible, AWS, Azure, C#, C++, CSS, Cypher, Django, Docker, FastAPI, Flask, GCP, Git, Golang, GraphQL, HTML, Java, JavaScript, Kafka, Kotlin, Kubernetes, Linux, MongoDB, MySQL, Neo4j, Node.js, pandas, PHP, Postgres, PostgreSQL, PyTorch, Python, React, Redis, Ruby, Rust, Scala, scikit-learn, Spark, SQL, SQLite, Swift, Tableau, TensorFlow, Terraform, TypeScript.

The only allowed rewrite group, in `job.SKILL_SYNONYMS`, is `Postgres`, `PostgreSQL`, and `SQL`. `Docker` does not match `Kubernetes`. A skill that is not in this group is copied unchanged.

Skills written in a CV skills section are also detected even when they are outside the lexicon, because extraction splits that section and stores each item as its own atom.

## `profile.json`

The file is a `MasterProfile`, schema version `1`.

```json
{
  "version": 1,
  "profile_id": "pf_0123456789abcdef",
  "data_notice": {
    "purpose": "Build a local master profile so you can confirm facts before any resume or cover letter is generated.",
    "retention": "The profile and source excerpts stay in this local file until you delete it.",
    "deletion": "Delete the profile file and your source documents to remove this data. Nothing is uploaded."
  },
  "documents": [],
  "atoms": [],
  "conflicts": [],
  "preferences": {
    "work_modes": [
      {"mode": "remote", "priority": "high"},
      {"mode": "hybrid", "priority": "medium"},
      {"mode": "onsite", "priority": "only_if_close"}
    ],
    "onsite_location": "Netherlands",
    "sector": "both",
    "time_availability": "full-time"
  },
  "updated_at": "2026-10-13T00:00:00"
}
```

`profile_id`, `display_name`, `residence`, and `preferences` may be null on a file saved by an older version. The next explicit save assigns an id. Opening the page does not write the file to fill those fields in. Preferences stay empty until they are saved.

`preferences.work_modes` has remote, hybrid, and onsite exactly once. Each priority is `high`, `medium`, `low`, or `only_if_close`. `sector` is `academia`, `industry`, or `both`. `onsite_location` is required text. `time_availability` may be null. These answers are not compared with job postings.

Each document stores `document_id`, `filename`, `media_type` (`docx` or `pdf`), `kind` (`cv`, `certificate`, or `portfolio_note`), `sha256`, `single_column` (always `true`), the text `blocks`, and parser `warnings`.

Each atom stores:

| Field | Required | Notes |
| :--- | :--- | :--- |
| `evidence_id` | yes | Stable id. Later claims cite it. |
| `statement` | yes | The claim. New atoms copy the excerpt. |
| `category` | yes | One of the four evidence categories. |
| `confirmation` | yes | `extracted` until a person confirms it. |
| `source_document_id` | yes | The document the excerpt must quote. |
| `source_excerpt` | yes | Verbatim span of that document. |
| `employer`, `title` | no | Copied from a role line. Bullets inherit them. |
| `start_date`, `end_date` | no | `ExactDate` objects. |
| `metrics` | no | Exact substrings of the excerpt, such as `20%`. |
| `skills` | no | Each name must appear in the excerpt. |
| `ownership_verb` | no | One table verb, as written in the excerpt. |

A model validator rejects an atom when a skill, metric, ownership verb, or statement number is not in `source_excerpt`.

Each conflict stores `conflict_id`, `kind` (`employer`, `title`, `dates`, `metric`, or `skill_category`), a `summary`, exactly two `evidence_ids`, `left_value`, `right_value`, `resolved`, and `resolution_note`. A resolved conflict must have a note. Resolving does not merge or delete atoms.

### How the file is used

`load_profile` reads UTF-8 JSON and validates it. A missing file becomes an empty profile. `save_profile` writes indented JSON.

The CLI default path for most commands is `.career-copilot/profile.json` in the current directory. This workspace passes `--profile profile.json`, which is the confirmed file at the repository root. That file is one profile. It is not mixed with the registry unless you point a command at it.

| Command | Effect on the file |
| :--- | :--- |
| `ingest --profile` | Adds or replaces one document and its atoms in that file, then recomputes conflicts. |
| `ingest` without `--profile` | Routes the document to one profile under `.career-copilot/profiles/<profile_id>/profile.json`, or creates a new folder there. |
| `preferences` | Writes `preferences` once. `--update` replaces them. |
| `confirm`, `confirm-all`, `unconfirm`, `edit`, `resolve`, `review` | Update atoms or conflict notes and save. |
| `status`, `list`, `show`, `conflicts`, `check` | Read only. |
| `job`, `resume` | Read only. |

`job` and `resume` ignore atoms that are still `extracted`. Confirmed gaps are not a stored type. Removing an atom has no command. The current way to drop one is to edit the JSON and leave the rest valid.

`check` runs `grounding_problems`. It fails when an excerpt, employer, title, or date raw text is no longer a verbatim piece of the stored document, when an evidence id is duplicated, or when a conflict cites one document twice.

## Preferences

`preferences` asks once per profile:

- Priority for remote, hybrid, and onsite. Each is `high`, `medium`, `low`, or `only_if_close`.
- The location or region where onsite work is acceptable.
- Academia, industry, or both.
- Time availability, which may be skipped.

A second run with no new flags prints the saved answers and does not prompt. Passing the flags again without `--update` exits with code 1 and leaves the file unchanged. The command does not read job boards and does not filter postings.

The page stores the same record and also stores three optional lists: `job_types` (`remote`, `hybrid`, `onsite`), `areas` (`academia`, `industry`, `other`), and `schedules` (`part_time`, `full_time`, `other`). A selected job type is saved as `high`. An unselected one stays on the record as `low`, so remote, hybrid, and onsite are still present once each. Academia alone sets `sector` to `academia`. Industry alone sets `industry`. Academia and industry together, or Other alone, set `sector` to `both`, because the older sector field has no Other value. The selected schedules are joined into `time_availability` (`Part-time`, `Full-time`, `Other`). An empty onsite location becomes `none` unless Onsite is selected, in which case the location is required. Older files that omit the three lists still load. These answers still do not search for jobs.

## Which profile a document belongs to

`ingest --profile PATH` always writes to that file. The registry is not consulted.

`ingest` without `--profile` parses the file first. A parse failure writes nothing. It then loads every `profile.json` in `--profiles-dir`, which defaults to `.career-copilot/profiles/`. A folder is used only when its name equals the `profile_id` inside the file.

`decide_match` compares the new atoms with each profile:

| Signal | Strength | Rule |
| :--- | :--- | :--- |
| Email | strong | The same address, compared case-insensitively, in either statement or excerpt. |
| Phone | strong | The same digit string of at least 8 digits. The text needs a `+`, a space, parentheses, or at least two hyphens, so a year range such as `2019-2020` is not a phone. |
| Employment | strong | Two `used_in_job` atoms with the same normalized employer and overlapping dates. An open role runs through 2100-12-31 for this comparison. A year with no month covers that whole year. |
| Name | uncertain | The same 2–4 word `mentioned_only` line, with no digits and no `@`. |

One strong profile is chosen and the command prints the reason. More than one strong profile, or only a shared name, prints a numbered choice and waits. No signal creates a new profile. `--choose new` or `--choose pf_...` answers without a prompt. End of file writes nothing.

Atoms, documents, conflicts, and preferences stay inside the chosen profile. Nothing is copied into another profile.

## Parse

`parse_document(path, kind)` accepts `.docx` and `.pdf`.

A DOCX is read with `python-docx`. A section whose column count is greater than 1 raises `UnsupportedLayoutError`. Body paragraphs become blocks. Table cells are read once, in document order. A paragraph style that looks like a heading, or a short line that is a known section title, becomes a heading block. A leading bullet character becomes a bullet block. Other text becomes a paragraph.

A PDF is read with `pdfplumber`. Encrypted files and files with no text layer are refused. Scanned pages are not OCR'd. Each page's words are clustered into visual lines. `is_multi_column` refuses a page when a vertical gutter splits it into two prose columns on enough lines. A page number or a short table cell on the right is not a second column. Wrapped lines are joined. A heading, bullet, role line, or degree line stays on its own block. Pages with no words add a warning and are skipped. The warning text is `Skipped N page(s) with no text layer.`

The document id is the content hash. `single_column` is stored as `true` only after those checks pass.

## Extract

`extract_atoms` walks the blocks in order. A known heading changes the current section and is not itself an atom. Unknown heading-shaped lines become section `other`.

Heading names live in `vocab.HEADINGS`. They cover English and Portuguese titles for summary, skills, experience, projects, publications, education, languages, and a few other sections. `training` is an education heading. A line that merely contains a date or a `|` is not treated as a heading.

| Current section | Atom category | Special case |
| :--- | :--- | :--- |
| skills | `mentioned_only` | The line is split on commas. Parentheses stay intact. Each item is its own atom whose statement is the item. |
| experience | `used_in_job` | A line with a date range is a role header. A line with `|` and no date is held until the date line. Following bullets inherit employer, title, and dates. |
| projects | `project` | A short label line is skipped. |
| education | `coursework` | A short label line is skipped. A certificate starts here before any heading. |
| anything else | `mentioned_only` | A portfolio note starts as projects before any heading. |

A role header is split on `|` into title and employer after the date span is removed. The statement of a new atom is the excerpt. Skills on a non-skills line are the lexicon terms plus any skill-section phrases found in that line. Metrics are numbers and percentages in the excerpt, excluding the role's date span and numbers glued to a slash. The ownership verb, if one is present, is the earliest match in the closed table.

Every new atom has `confirmation = extracted`.

## Conflicts

`detect_conflicts` compares atoms from different documents. It never merges them and never deletes them. Same-document pairs are ignored.

Role headers are `used_in_job` atoms whose statement contains the employer and, when a title exists, the title, and which have a start date. Two headers from different documents are compared only when both of these are true:

- The employers are equal, or one name contains the other as a whole prefix (`Acme` and `Acme Corp`). Different employers are left alone.
- The titles are equal, or the date ranges overlap. Different titles with no date overlap are left alone.

| Kind | When it fires |
| :--- | :--- |
| `employer` | The pair passed the gate above and one employer name contains the other. |
| `title` | The pair passed the gate, both titles are present, and they differ. In practice this requires overlapping dates, because different titles with no overlap never reach this check. |
| `dates` | The pair passed the gate, both have a start date, and the start or end parts differ. |
| `metric` | Same employer and title, same statement after numbers are replaced with `#`, and the metric lists differ. This check uses atoms that have metrics and are not role headers. |
| `skill_category` | The same skill name has different evidence categories in two documents. The strongest category per document is kept for that comparison. |

Strength order, used only for `skill_category`, is `mentioned_only` < `coursework` < `project` < `used_in_job`. Equal strengths do not conflict.

Date overlap here is only a "might be the same stint" test. A missing month spans the whole year. A missing end day uses day 28 for this comparison. That day-28 bound is not used by the years calculation.

Ingest keeps previous resolution notes when the same `conflict_id` appears again.

`confirm` still confirms an atom that sits in an unresolved conflict, and it prints a warning that both atoms stay. `confirm-all` refuses while any conflict is unresolved and exits with code 2.

## Confirm

Confirmation is a human act. The software does not infer it.

`review` walks atoms that are still `extracted`. The prompt is `[c]onfirm, [e]dit statement, [s]kip, [q]uit:`. `e` asks for `statement:` and changes only the statement. An empty statement is left unchanged. An edited atom stays unconfirmed until it is confirmed again. End of file prints `Stopped.` and saves the atoms already confirmed.

`edit` can change statement, category, employer, title, dates, skills, metrics, and ownership verb. Each new value must still be grounded in the source excerpt and in the stored document text. Clearing a field uses the matching `--clear-*` flag. The evidence id does not change.

`unconfirm` sets the atom back to `extracted`.

## Job analysis

`analyze_job(text, atoms, as_of)` reads the pasted text and the confirmed atoms. It returns a `JobAnalysis` with `requirements`, `hard_constraints`, and `years`. The model forbids a score or a match percentage.

### Which lines become requirements

A heading is a whole line, at most 60 characters, with no `.`, `!`, or `?`. A trailing colon is removed. The match is exact after case-folding. These are the closed sets:

Mandatory: `requirements`, `required`, `required qualifications`, `minimum qualifications`, `basic qualifications`, `qualifications`, `must have`, `must-have`, `what you need`, `what you'll need`, `what you will need`, `what we're looking for`, `what we are looking for`.

Preferred: `preferred`, `preferred qualifications`, `nice to have`, `nice-to-have`, `nice to haves`, `bonus`, `plus`, `desired`, `good to have`.

Duties, stored as mandatory requirements: `responsibilities`, `responsibility`, `what you'll do`, `what you will do`, `the role`, `your role`.

Skipped sections: `about`, `about us`, `about the company`, `benefits`, `perks`, `what we offer`, `what you'll get`, `equal opportunity`, `how to apply`, `our team`, `who we are`, `compensation`.

`Key Responsibilities` and `Required Technical Skills` are not in these sets. Lines under an unrecognized heading are not requirements. A bullet that appears before any heading is mandatory only when it names a known skill, a degree group, or a hard constraint.

Under a recognized heading, a bullet or a line of at most 240 characters is one requirement. A line with no known skill and no degree group is `unknown`, unless it is only a hard-constraint line, in which case it is not also a requirement.

### Status

Search phrases are the skill lexicon, the synonym group, and the skills named on confirmed atoms. Longer phrases are tried first so `PostgreSQL` wins over `SQL`.

| Status | Rule |
| :--- | :--- |
| `met` | Every skill or degree on the line is supported. `met` and `partial` store evidence ids and the first supporter's excerpt. |
| `partial` | At least one part is supported and at least one is not fully met. |
| `missing` | A known skill or degree was checked and no confirmed atom supports it. Evidence is empty. |
| `unknown` | The line has nothing the checker can test. Evidence is empty. |

A hands-on cue (`experience`, `hands-on`, `production`, `proficient`, `built`, `develop`, `implement`, and the related forms in `job.py`) makes a skill `met` only when the strongest supporter is `used_in_job`. A project, coursework, or `mentioned_only` atom then yields `partial`. A bare skill name, with no hands-on cue, is `met` from any confirmed category.

Several skills on one line must all be met for the line to be `met`. One supported skill and one missing skill produce `partial`. The word `or` changes this rule for degrees only.

Degree groups, with no hierarchy between groups, are:

- `phd`, `ph.d`, `doctorate`, `doctoral`
- `master's`, `masters`, `msc`, `m.sc`
- `bachelor's`, `bachelors`, `bsc`, `b.sc`

A PhD atom does not satisfy a bachelor's line. `master` alone is not a degree token. A line that says `or` is `met` when any one of its degree groups is supported.

`atom_supports` is true when the excerpt, the statement, or `atom.skills` contains the term or another member of its synonym group.

### Hard constraints

Each result is `pass`, `fail`, or `unknown`.

Citizenship, work authorization, and license pass only when the matched phrase itself appears in a confirmed excerpt. Silence stays `unknown`. A different fact is not a `fail`.

A minimum-years line needs a cue such as `experience` or `N+ years`, and a number of years. General years use the employment union below. A line that also names a skill uses only `used_in_job` atoms that support that skill.

| Evidence | Result |
| :--- | :--- |
| No dated evidence, or zero days | `unknown` |
| Merged days already cover the bar | `pass`, even if some role has no start date |
| The timeline is complete and still short | `fail` |
| The timeline is short and a role has no start date | `unknown` |

`meets_minimum` is `days + 1 >= required_years * 365.25`. The extra day keeps a span that includes a leap day from missing a whole-year bar by a fraction.

### Years

`experience_span` unions confirmed `used_in_job` intervals. Overlapping days are counted once. Adjacent roles stay as separate intervals. Bullets that repeat a role share its interval, so they do not add time. Coursework and projects are not employment.

A year with no month covers 1 January through 31 December. A missing start day is the first of the month. A missing end day is the last day of the month. An open role ends on `as_of`. A future end is capped at `as_of`. An interval that ends before it starts is dropped.

`complete` is false when a confirmed role header has no start date. The reported year count is `days / 365.25`. The CLI prints one decimal place.

The conflict detector's day-28 overlap test is a different function. Do not reuse it here. Profile routing uses its own date bounds, in `identity.py`, and also requires the same employer.

### Checklist tally

`checklist_tally` counts requirement statuses. `met` counts 1, `partial` counts 0.5, and `missing` counts 0. `unknown` is left out of the denominator. The percentage is that total divided by the number of judged lines, rounded to one decimal. When every line is `unknown`, or there are no requirements, `percent` is null and the CLI prints `Match: unknown`.

The model rejects a percentage that does not equal that formula, and it still rejects a field named `score`. Hard constraints are not part of the tally. The percentage does not decide which sentences are written.

The CLI prints the counts and the percentage above the requirement lists. `job --out` stores them as `tally` in the checklist JSON.

## Resume generation

`build_resume(atoms, analysis, cover_letter=False, job_text="", academic=False)` raises if no atom is confirmed. It does not call a model and does not invent a sentence. `academic` is true only when the saved areas include academia and do not include industry. An empty preference, an industry choice, or a mix of academia and industry keeps the industry order.

When the profile's documents include `sample-cv.pdf` and that file sits beside the profile, `assemble_source` is used instead. It copies the file's sections. Summary and Technical Skills are the only sections that may change, and a skill is kept only when a confirmed atom states it. Role lines, bullets, education, publications, and other sections stay word for word. The DOCX for that path is `write_source_resume`. It uses the file's headings and does not add the publication cap line. A profile that does not include that file still uses `build_resume`.

### Grouping

Confirmed atoms are grouped in list order. Role order is that order. Jobs are not sorted by date.

A role header is a `used_in_job` atom whose statement contains its title, employer, and start-date raw text. Following `used_in_job` atoms with the same employer, title, and date raw text are that role's bullets. Another `used_in_job` atom that does not match becomes a loose bullet, and the open role ends.

Other categories:

| Shape | Section |
| :--- | :--- |
| `coursework` | education |
| `project` | projects |
| `mentioned_only`, one skill, statement equal to that skill | skills |
| `mentioned_only` matching `Label: value`, and not a skill atom | languages |
| `mentioned_only` containing `@` | contact |
| other `mentioned_only` longer than 80 characters, or ending with `.` | summary |
| remaining short `mentioned_only` | name |

### What may change

The example image `example_cv.png` is a guide for section clarity: contact, education, skills, and languages on one side, and the name, a short summary, experience, and publications on the other. The written file stays one column, with no table and no text box, because `SPEC.md` requires an ATS-friendly single-column DOCX. The example's photo, invented contact details, and placeholder text are not copied.

Industry order, top to bottom: name, contact, summary, experience, loose bullets, at most two publications, education, skills, languages, then any other project that is not a publication. Academic order moves education ahead of experience and keeps publications with the experience block. Role order inside experience is still the confirmed profile order.

A publication is a `project` atom whose statement matches publication, published, journal, conference, proceedings, doi, thesis, or paper. The profile table lists every publication fact. Job-matching publications come first in the resume. At most two are written. Each extra one is listed under Omitted with the reason "Publications are limited to the two most relevant." The line "Full list available upon request." is added by the DOCX writer after the last publication paragraph. It is not a `ResumeSentence` and it is not validated as a claim. When no publication atom exists, that line is omitted.

| Piece | Rule |
| :--- | :--- |
| Role header, education, projects, contact, languages, name | The statement is copied. |
| Skills | Job-relevant skills come first. The others stay, in their original relative order. A skill label may be replaced by the job's member of the Postgres group. |
| Summary and bullets | The statement is copied. A Postgres-group term may be replaced by the member the job uses. |
| Bullets inside a role | If any bullet is cited by a `met` or `partial` requirement, or supports a preferred skill from those requirements, only those bullets are kept. If none match, every bullet in the role is kept. |
| Missing job skills | They are listed in the diff and are not written into a sentence. |

Preferred skills are lexicon and synonym terms found in `met` or `partial` requirement text.

### Cover letter

The cover letter is optional. For each `met` or `partial` requirement it adds one sentence: `What this background contributes: ` plus the statement of the best cited work atom that has not already been used. A bullet or loose `used_in_job` atom ranks above a project, which ranks above a role header. Skill-list atoms are not letter sentences. If no requirement cites a work atom, the letter falls back to the first bullet of the first role, or that role's header. Requirement text is not copied, and the fixed lead contains no skill, number, or ownership verb, so a missing skill named only in the job cannot enter the letter. A bare copy of the atom statement still validates if the person removes the lead while editing.

The body stops at a word budget. The job text is scanned for a cover-letter word count, or for "one page" or "two pages". One page is 320 words, which is the stand-in for one page of Arial 11. When the job states no limit, the budget is 320 words. The first sentence is kept even when it is longer than the budget. Later sentences stop when the next one would pass it. The greeting `Hello,` and the close `Sincerely,` are added by the exporter and are not validated sentences. They are not counted in the word budget.

### Confirmation before writing

`resume` prints the checklist, the tally, and the hard constraints, then asks `Generate resume? [y/n]` and `Generate cover letter? [y/n]`. Only a yes is written. End of file, or no to both, writes nothing. `--confirm resume`, `--confirm cover`, or `--confirm resume,cover` answers without a prompt. The validator runs on the sentences that will be written. A violation writes no file. `--out` is the resume path. `--cover-letter` is the cover path, and it defaults to the resume name with `-cover.docx` when the person asks for a cover letter and omits the flag. The diff is written only when the resume is written.

### Diff

The markdown diff has four sections:

| Section | Contents |
| :--- | :--- |
| Changed | Skill reordering or a synonym rewrite, with the original text. |
| Kept | Role headers and the bullets that were written. |
| Omitted | A bullet left out because another bullet in that role matched the checklist, or a publication past the two-item limit. |
| Refused | Lexicon skills the job asks for that no confirmed atom names, requirements that are `missing` or `unknown`, and hard constraints whose result is not `pass`. |

A line that never became a requirement is absent from Refused. A tool name outside the skill lexicon is also absent from Refused. The generator still cannot insert it, because sentences are copies of atoms.

### Validation

`validate_resume` checks the sentences it is given. The CLI passes the resume sentences when a resume was confirmed and the cover sentences when a cover letter was confirmed. It prints each violation as `code: detail` on stderr, returns exit code 1, and writes no file.

| Code | Rejection |
| :--- | :--- |
| `evidence` | An evidence id is not in the profile. |
| `confirmation` | The cited atom is not `confirmed`. |
| `rewrite` | The text is not the atom statement, an allowed synonym fold of it, the role line built from title, employer, and date raw text, or, for a cover sentence, that same statement with the fixed contribution lead. |
| `number` | A number or percentage in the sentence is missing from the cited excerpt. |
| `ownership` | A table verb in the sentence is missing from the cited excerpt. |
| `skill` | A lexicon skill in the sentence is missing from the cited excerpt and from its synonym group. |
| `employer`, `title`, `dates` | A role sentence is not the atom statement or the exact role line, or a stored field is missing from that line. |
| `absent_skill` | The sentence names a lexicon skill that a non-`met` requirement asks for and that no confirmed atom names. |

The adversarial test builds a profile that does not contain Kubernetes, requires Kubernetes, and fails when that word appears in the resume or the cover letter. A hand-built sentence that inserts Kubernetes is rejected with `absent_skill`.

### DOCX

`write_resume` builds an 8.5 by 11 inch document with 0.75 inch margins, one column, Arial body text, and no tables. The name is a bold 16 pt paragraph. Section titles are Heading 1: Contact, Summary, Experience, Publications, Education, Skills, Languages, and Projects. Role lines are bold. Skills, bullets, education, projects, and publications are paragraphs whose text starts with `- `. Word list styles are not required.

Displayed text is passed through `polish` at write time and on the profile fact table. Unicode is normalized. A split accent is joined to the vowel it belongs to, whether the mark sits before the vowel (`Ciˆencia` becomes Ciência, `Jo˜ao` becomes João) or after it (`aˆrvore` becomes árvore, `arqueolo´gico` becomes arqueológico). `ão` keeps the tilde on the a. A cedilla joins with `c` from either side, including `c¸˜` and `¸c˜`, so `Comunicac¸˜ao` becomes Comunicação and `Associa¸c˜ao` becomes Associação. A bullet between words becomes a space. An exclamation mark between letters becomes `ff`, which repairs a broken `ff` ligature such as `di!erent`. A real `Hello!` stays. Repeated spaces collapse, and a space before punctuation is removed. Numbers stay. The stored atom is not changed, so the validator still sees the atom text. A review box shows the cleaned text. Saving that box unchanged writes the original atom text, and the DOCX cleans it again. A typed city, country, or extra contact line is written under the name when `residence` is set. It is header text, not a sentence, and it is not accepted as evidence. A typed `display_name` is used as the bold name only when no confirmed name atom was copied.

`write_cover_letter` writes the name, `Hello,`, each cover sentence as its own paragraph, `Sincerely,`, and the name again. Those paragraphs are polished for display in the same way.

The diff path defaults to the DOCX path with suffix `.diff.md`, so `resume.docx` produces `resume.diff.md`.

## Visual interface

`career-copilot ui` starts Streamlit on `127.0.0.1` only. The default port is 8501. Usage statistics are off. The browser is not shown exception text, tracebacks, or links that would send an error to another site. Host checks accept `localhost` and `127.0.0.1`. If the server address is set to anything else, the page stops. An unexpected error shows "Something went wrong. Nothing was uploaded." A save message does not include the folder path. The page does not create an account and does not upload the CV to a server. Read the CV stores the text in `.career-copilot/cv.txt` on this computer. Automatic search runs only when that button is pressed.

The menu is User, Search, Jobs, and Documents. User asks for one CV. Read the CV sends the text to the local `grok` command, saves the grounded skill names, and shows Possible roles. Each role is a checkbox plus one sentence. Add a role accepts a name the list did not include. Save writes the kept roles. The CV text is not shown and is not written to `roles.json`. Search stores the optional filters and Other notes. Jobs shows the profile card and waits for Automatic search or Manual search. Documents lists each CV written for a vacancy, with the company, the date, and whether the person said they applied.

The notes below describe screens that the code still contains and that the open menu does not show.

Run it from the repository directory. The page looks for profiles in this order:

1. `CAREER_COPILOT_PROFILE`, when that environment variable is set. It hides every other file.
2. `profile.json` in the current directory.
3. `.career-copilot/profile.json`.
4. Each `.career-copilot/profiles/<profile_id>/profile.json` whose folder name is that profile's id.

When that search finds nothing, the page asks for the person's name and accepts PDF or DOCX background files (a CV, a thesis, a paper, or a project note). Creating the profile writes `.career-copilot/profiles/<profile_id>/profile.json` and assigns the id then. It does not run when a profile is already open. An upload is checked before it is parsed: the name must be a single file name of at most 180 characters, the suffix must be `.pdf` or `.docx`, and the bytes must be non-empty and at most 20 MiB. The bytes are not logged. A multi-column file is refused with the same parser error as the command line.

The unused screens, not shown when the page opens, are:

| Page | What it does |
| :--- | :--- |
| Profile | Shows User, then the person's name. When the profile has no name, it shows an empty name box. It does not show the profile id, confirmation counts, the deletion sentence, an extracted phone, preferences, onsite location, document upload, or the extracted-fact list. Contact, city, and country can still be saved. They are header text, not resume claims. A conflict section appears only when two documents disagree. Resolving one stays a command-line action. |
| Job | Keeps the paste area. Open roles use today's date. There is no as-of field. After Analyze, it shows the match percentage, the full matches, and every line that is not a full match. Partial lines are labeled Partial, missing lines are labeled Missing, and unknown lines are labeled Not judged. Partial and missing lines are why a percentage is below 100%. Not judged lines stay out of the percentage. Below those points it shows feedback: Positive or Negative, plus one reason. The reasons are Well qualified, Mostly qualified, Missing key skills, Overqualified, Below the stated experience, Does not meet a stated requirement, and Not enough information. Any missing judged requirement is Missing key skills. Overqualified means every judged requirement is met and the observed time is at least twice each passed year minimum. Unknown lines stay out of the feedback. The feedback is not a score and it does not authorize a sentence. The full checklist stays in an expander. The person continues with the job before a resume is prepared. Analyze stores a local job record and does not write the profile. |
| Generate | Opens after Prepare the resume on the Job page. When the profile includes `sample-cv.pdf` and that file is beside the profile, Generate shows one column copied from that file. Only the summary and Technical Skills can be edited, and the preview updates as those boxes change. Every other section stays as it is in the file. A profile without that file still edits one sentence at a time. Save writes files only after `validate_resume` accepts the text. A failed check writes nothing. |
| Documents | This older screen is not the Documents page on the menu. It lists job records for the open profile, newest first, except records marked discarded. Choosing a saved document stays on this screen and does not open Generate. Each record shows the job name, the date, and the resume and cover letter when they were saved. Keep, Discard, and Save edited text are the choices. After Keep, the screen asks whether it was sent and on which date. Saving that date marks the record submitted. Submitted means the person recorded it locally. Nothing is sent to an employer. Discard hides the record and does not delete the profile or the files. DOCX files already in `out/` are listed for download and are not rewritten. |

A generation is stored under `.career-copilot/generated/<profile key>/<record id>/` as `record.json`, and, once a resume or cover letter is saved, `diff.md` and the DOCX files. The record stores the job text, the job name, the as-of date, the tally percent, the status (`draft`, `kept`, `discarded`, or `submitted`), an optional sent date, and the sentences. It does not copy the profile. An analysis-only record has no DOCX yet. The folder is local. `.gitignore` already ignores `.career-copilot/`.

The profile key is the profile id. A file that has no id yet uses `file_` plus a hash of its path. The first save assigns an id, and the generated folder moves with it.

Manual edits keep each sentence's evidence ids and kind. The validator still has to accept the text. A synonym fold of Postgres, PostgreSQL, and SQL can be saved. A skill that no confirmed atom names, such as Kubernetes, is rejected and the existing files stay as they were. The diff gains a `Manual edits` section only for lines that changed.

Opening the page does not write `profile.json`. A button that saves the profile uses the same `save_profile` path as the command line, so the first save also assigns a profile id when the file did not have one.

## Input checks

Typed values are checked before they are stored or used to build a checklist. A failure raises `CareerCopilotError` and the command exits 1. The page shows that message and does not write the file.

| Value | Check |
| :--- | :--- |
| Job text | Text, no null character, not blank after stripping, at most 100,000 characters. Newlines stay. |
| Job file | At most 1,000,000 bytes, checked before the read, then UTF-8. Standard input is read only up to one character past the text limit. |
| As-of date | `YYYY-MM-DD`, from 1900-01-01 through 2100-12-31. A bad CLI format still prints `--as-of must be YYYY-MM-DD.` |
| Statement edit | Text, no null character, not blank, at most 8,000 characters. Extraction does not use this cap, so a long parsed atom can still be stored. |
| Manual sentence | Text, no null character, not blank, at most 8,000 characters. Checked before `validate_resume` and before any directory is created. |
| Preferences | Onsite location, time availability, contact, city, country, and the display name are at most 200 characters and contain no null character. Job type, area, and time must be chosen from the listed values. |
| Uploaded document | A single `.pdf` or `.docx` name, at most 180 characters, non-empty, and at most 20 MiB. |
| Evidence id | Must match `ev_` plus 16 hex characters. A malformed id is refused without repeating the typed value. |
| Resume and cover flags | Only a real `True` or `False`. The string `"false"` is refused. |
| Profile path | The page opens a path only when it is one of the paths it discovered. |
| Generated files and downloads | The folder name must be a single profile key or record id. The resolved path must stay inside the local directory. `..` is refused. A `record.json` over 2,000,000 bytes is skipped. A download over 20 MiB is refused. The download name is the file name only. |

The page checks the same limits again after the browser widgets. A widget maximum is not the only check. These checks do not change which atoms generation may copy, which job headings are recognized, or how the validator judges a sentence.

## How to run

Open the four pages with the command in [How to open it](#how-to-open-it). `grok login` must already have been done. Do not set `XAI_API_KEY`.

The commands below belong to the older pipeline. They are not required to use User, Search, Jobs, or Documents.

From the repository root, with the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\career-copilot.exe --help
```

Create or update a profile:

```powershell
.\.venv\Scripts\career-copilot.exe ingest sample-cv.pdf --kind cv --profile profile.json
.\.venv\Scripts\career-copilot.exe ingest LatexThesis_1Vr.pdf --kind portfolio_note --profile profile.json
.\.venv\Scripts\career-copilot.exe review --profile profile.json
.\.venv\Scripts\career-copilot.exe confirm-all --profile profile.json
.\.venv\Scripts\career-copilot.exe preferences --profile profile.json --remote high --hybrid medium --onsite only_if_close --location "Netherlands" --sector both --availability "full-time"
.\.venv\Scripts\career-copilot.exe status --profile profile.json
.\.venv\Scripts\career-copilot.exe check --profile profile.json
```

`review` and `preferences` read the keyboard when the answers are not passed as flags. Run them in an interactive terminal. Piping nothing into `review` stops on the first atom. A second `preferences` run prints the saved answers.

Route a new document without naming a file. A strong match attaches. An uncertain match asks, unless `--choose` is set.

```powershell
.\.venv\Scripts\career-copilot.exe ingest other-cv.pdf --profiles-dir .career-copilot\profiles
.\.venv\Scripts\career-copilot.exe ingest other-cv.pdf --profiles-dir .career-copilot\profiles --choose new
```

Checklist a job. The profile is not modified.

```powershell
.\.venv\Scripts\career-copilot.exe job --profile profile.json --as-of 2026-10-13 --text "Requirements:`n- Python`n- 5+ years of experience"
.\.venv\Scripts\career-copilot.exe job --profile profile.json --as-of 2026-10-13 --out checklist.json job.txt
```

Write a resume. `--out` is required. The command prints the analysis and asks before it writes. `--confirm` skips the questions.

```powershell
.\.venv\Scripts\career-copilot.exe resume --profile profile.json --as-of 2026-10-13 --out out\resume.docx --diff out\resume.diff.md --confirm resume job.txt
.\.venv\Scripts\career-copilot.exe resume --profile profile.json --as-of 2026-10-13 --out out\resume.docx --cover-letter out\cover.docx --confirm resume,cover --text "Requirements:`n- Python"
```

`--as-of` defaults to the machine's current date. Pass an explicit date when a result must be repeatable. Open roles end on that date.

`--port` on `career-copilot ui` chooses another loopback port. Nothing is uploaded.

Exit codes: `0` success, `1` a correctable error or a validator rejection, `2` `confirm-all` while a conflict is unresolved. An unknown command, including `generate`, is an argparse error.

Tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

`tests/test_golden.py` parses `sample-cv.pdf` when that file is present, plus any `.docx` or `.pdf` in `golden/`. It checks that every atom quotes its document and that extraction leaves atoms unconfirmed. The golden set is not yet 15–20 CVs.

## Current limitations

The open pages' remaining work is in [Still to do](#still-to-do). The bullets below describe the older pipeline, including cases where a reader of `SPEC.md` might expect something else.

- The resume copies confirmed sentences. It reorders the skills block and may swap Postgres, PostgreSQL, and SQL. It does not rewrite a summary or a bullet into new prose.
- Job headings are an exact closed set. A posting that says `Key Responsibilities` or `Required Technical Skills` contributes no requirement lines. The checklist then has little to tailor, and the diff's Refused section does not name the duties that were never parsed.
- Tools outside the skill lexicon, such as RDF, OWL, SPARQL, GraphRAG, and Graphwise, are not separate checklist skills and are not `absent_skill` violations. They appear in the resume only when a confirmed atom already contains them.
- A line with two skills is `partial` when only one is supported. `or` does not mean "any skill," except for the degree groups.
- A hands-on requirement is `met` from a `mentioned_only` skill atom when the line has no hands-on cue. With a hands-on cue, that same atom is only `partial`.
- The years report is total confirmed employment. It is not years in one technology unless the constraint line itself names that technology.
- `SPEC.md` says years use interval intersection. The code unions overlapping employment intervals and counts each day once. It does not add overlapping jobs, and it does not count only the overlapping region.
- Conflict detection misses some real disagreements. It compares role headers across documents only when the employer strings are equal or one contains the other. The same degree at two differently written institutions is not a conflict. A publication date that differs inside two project atoms is not a date conflict unless both atoms are role headers.
- The heading `training` switches later text to coursework until another known heading. In a long portfolio note, prose after that word can be labeled education.
- Wrapped PDF lines are joined. Section numbers can be stored as metrics. A number range inside an identifier can be read as a date. Accented LaTeX text can survive as extracted characters.
- There is no command to delete an atom. `review` cannot drop one.
- The DOCX uses prefixed paragraphs, not Word list styles, text boxes, or a table layout. `example_cv.png` is followed as a section order, not as a two-column Word table.
- Accent repair is for display and for the DOCX. It does not rewrite `profile.json`. A split accent that the joiner does not recognize is left as stored. A review box shows the cleaned text, and saving that box unchanged keeps the original atom text.
- The cover letter starts each sentence with "What this background contributes: " and then copies a confirmed work sentence. It does not invent the employer's problem, and it does not name a missing skill. The one-page limit is a word budget, not a measurement of the rendered page.
- Publications are capped at two. The request line for the full list is document chrome, not evidence.
- The checklist percentage is a count of judged lines. It is not evidence that a person is qualified, and a line the parser never turns into a requirement does not affect it. The feedback line uses that same count, plus failed hard constraints and passed year minima. It is not a score and it does not add a sentence. Overqualified is used only when every judged requirement is met and the observed time is at least twice each passed year minimum. The page does not call someone overqualified when the job states no year minimum.
- A shared name does not attach a document by itself. Overlapping dates attach only when the employer text matches after light normalization. `Acme` and `Acme Corp` are not the same employer for this check.
- `ingest` without `--profile` does not look at `profile.json` in the repository root. Pass `--profile` to keep using that file. The local page does look there when it is started from this folder.
- The local page can ingest a single-column PDF or DOCX. It still cannot repair a multi-column file. It does not edit atom statements. The command-line `edit` command still marks a changed statement `extracted`. A manual sentence edit that fails validation writes no file.
- Discarding a job record hides it on the Documents page and leaves the files on disk. Submitted is a local flag. The page does not apply to the job.
- `career-copilot ui` listens on `127.0.0.1` only. It is not a shared server.
- `profile.json` stores full source blocks and excerpts. It is a local sensitive file. The program does not upload it, and it also does not redact it.
- The repository is not a git repository unless someone initializes one. Do not initialize or commit unless asked.

## Intentionally not implemented

These stay out of the program on purpose.

- Auto-apply, a browser extension, accounts, hosted storage, and a public web API. There is no FastAPI app. The Streamlit page is loopback only.
- An API-key call to `https://api.x.ai`. The open pages use the installed `grok` command and the existing login.
- A browser, cookies, or a search-engine scrape. LinkedIn is paste-in. Python fetches one https page when the person pastes that link, or when Grok returns a public link.
- Using the model as the judge of its own sentence. Python computes the percent. The person decides which roles to keep and which job to pursue.
- Interview prep, including talking points, "what not to say," and questions for the candidate.
- A desktop app. The local Streamlit page is the visual interface.
- Cloud deployment and multi-user accounts.
- OCR and multi-column PDF repair.
- Persona agents, CrewAI, and a LangGraph workflow.
- A measured quota cap. The page cannot read the weekly Grok allowance.

The opened job writes the CV and, when asked, the cover letter. The older command that writes a DOCX is `resume`, not `generate`. The person still reviews any file before submitting an application.
