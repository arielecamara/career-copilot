# AI Career Copilot — Engineering Spec v2.0

Version 2.0 — October 2026. Source: `../career-copilot/AI_Career_Copilot_Especificacao_Revisada_v2.pdf`.

> Prefer verifiable honesty over theatrical autonomy.
> "We adapt your real history to the job. You review. You decide what to submit."

The system prepares documents. The human submits them. Do not title or build this as an autonomous application.

## 1. Objective

Help a candidate adapt a resume and prepare for an interview in a traceable, reviewable way, without inventing facts.

The value is a lower risk of submitting something untrue, and a tailoring pass the candidate can review and correct in under 5 minutes. The value is not an unsupervised package generated in 60 seconds.

### Guarantees

- Every claim points at a confirmed profile atom (`evidence_id`).
- Changes are shown as a readable diff, including what was refused and why.
- Gaps use the 3-state model below.
- The candidate confirms the profile before any generation.

### Refusals

- Does not apply or submit for anyone.
- Does not promise absolute zero hallucination.
- Does not invent skills, dates, titles, metrics, or ownership verbs.
- Does not reorder professional chronology.

## 2. Architecture

One linear pipeline. No persona agents and no CrewAI. LangGraph is allowed only as a state machine with explicit edges and a mandatory human checkpoint.

| Stage | Name | Input | Output |
| :---: | :--- | :--- | :--- |
| 1 | Documents | DOCX, or a single-column text PDF: CV, certificates, portfolio notes | Raw document records |
| 2 | Extraction | Stage 1 records | Master-profile atoms with `evidence_id`, evidence category, and source excerpt |
| 3 | Human confirmation | Atoms and detected conflicts | Confirmed master profile. Required before generation |
| 4 | Job analysis | Pasted job-description text | Requirements, hard constraints, and a status checklist |
| 5 | Evidence selection | Confirmed profile + checklist | Chosen atoms, each tied to `evidence_id` |
| 6 | Generation | Selected atoms + checklist | Tailored CV, optional cover letter, interview prep |
| 7 | Deterministic validator | Generated assets + confirmed profile | Pass, or a violation report. Python only |
| 8 | Diff and export | Passing assets + source CV | Readable diff and a single-column DOCX |

URL ingestion is secondary. It is out of the phase-1 core path because many job boards block scraping.

## 3. Master profile

Structured output fixes shape. It does not make a statement true. Each fact is one atom.

### Atom

| Field | Required | Rule |
| :--- | :---: | :--- |
| `evidence_id` | yes | Stable id. Every later claim cites one or more ids. |
| `statement` | yes | One factual claim, copied in meaning from the source excerpt. |
| `category` | yes | `used_in_job`, `project`, `coursework`, or `mentioned_only`. |
| `confirmation` | yes | `extracted`, then `confirmed`. `externally_verified` is future work, not MVP. |
| `source_document_id` | yes | Document the excerpt came from. |
| `source_excerpt` | yes | Verbatim span from that document. |
| `employer` | no | Exact string when the atom is a role fact. |
| `title` | no | Exact job title when the atom is a role fact. |
| `start_date`, `end_date` | no | Exact dates. Open-ended roles use a null `end_date`. |
| `metrics` | no | Numbers copied exactly. No rounding and no inferred numbers. |
| `skills` | no | Skills named by the excerpt. This list is the skill whitelist for that atom. |
| `ownership_verb` | no | Verb as written in the source, if one is present. |

### Confirmation rules

1. Human confirmation happens before the first generation.
2. Conflicts between documents are shown. There is no silent merge.
3. Lifecycle is explicit: extracted automatically, then confirmed by the candidate, then externally verified later.
4. Evidence category is one of the four values above. `mentioned_only` cannot be rewritten as hands-on production ownership.

## 4. Gaps and hard constraints

"Not in the profile" does not mean "the candidate does not know this." The system only knows that it found no evidence.

| State | Meaning | Where it may appear |
| :--- | :--- | :--- |
| Proven | A confirmed atom supports it | CV and cover letter |
| Not reported | No atom supports it | Ask the candidate, or mark `unknown`. Do not fill it in |
| Confirmed gap | The candidate confirmed they do not have it | Interview prep only |

Hard constraints are boolean filters, never similarity scores. Examples: citizenship, work authorization, a compulsory license, minimum years of experience. Each result is `pass`, `fail`, or `unknown`.

Years of experience use interval intersection across roles. Overlapping jobs are not added together.

## 5. Requirement checklist

Semantic similarity is not professional qualification. "Joined a project that used AWS" is not "administered AWS in production." Hard constraints stay `pass`, `fail`, or `unknown` and never become a score.

The checklist may show one tally. `met` counts 1, `partial` counts 0.5, and `missing` counts 0. `unknown` is excluded. The percentage is that tally divided by the number of judged requirements. It is not a similarity score and it does not decide whether a sentence is allowed.

Each requirement has:

- `text` — the requirement, from the pasted job description.
- `kind` — `mandatory` or `preferred`. These two lists stay separate.
- `status` — `met`, `partial`, `missing`, or `unknown`.
- `evidence_ids` — required for `met` and `partial`. Empty for `missing` and `unknown`.
- `excerpt` — the profile excerpt that supports `met` or `partial`.

## 6. Generation rules

### Resume

- Keep employment chronology unchanged. Recruiters and ATS read it in reverse chronological order.
- For a profile that does not include the original CV file below, rewrite only the summary, the skills block, and bullets inside each existing role.
- Do not add roles, employers, titles, dates, or metrics.
- An ownership verb (`led`, `owned`, `architected`, `responsible for`, and any verb added to the same table) is allowed only when the source atom already has that verb or a verb the table marks as equal or stronger.
- Synonyms come from an explicit table. Initial allowed pair: `Postgres` ↔ `PostgreSQL` ↔ `SQL`. `Docker` must not become `Kubernetes`. A missing pair means the rewrite keeps the original term.

### Original CV

When the open profile includes `sample-cv.pdf` and that file is in the project folder, the written resume starts from that file.

- Only the summary and the Technical Skills section may change, and only to align with the pasted job.
- A skill is written only when that same skill is already in that file and its atom is confirmed.
- Roles, bullets, dates, employers, education, publications, and every other section are copied from that file.
- The page shows one column. Under the preview, only the summary and Technical Skills can be edited. The validator still runs before a file is written.

### Cover letter (optional)

- Optional. A required cover letter increases the invention surface.
- It may name a gap in plain language.
- It may connect one real piece of work to one real requirement.
- It must not invent current company problems, culture, or anything that fills the space between the profile and the job.

### Interview prep

- Talking points, each citing `evidence_id`.
- A "what not to say" list for unsupported claims and for verbs or skills the validator would reject.
- Gap strategy: say the gap, then give adjacent confirmed evidence when it exists.
- Questions for the candidate to ask.

## 7. Deterministic validator

The model proposes. Python decides. An LLM must not be the judge.

Reject the package when any of these fail:

- A generated sentence has no confirmed `evidence_id`.
- Employer, title, dates, or numbers differ from the confirmed atom. Near matches fail.
- An ownership verb is above the atom's allowed level.
- A skill is outside the confirmed profile whitelist.
- A confirmed gap, or a skill absent from the profile, appears in the CV or is implied in the cover letter.

### Adversarial CI test

Inject a required skill that is absent from the confirmed profile. The build fails if that skill appears on the CV or is suggested in the cover letter.

## 8. Phase-1 MVP

Phase 1 runs locally: a command-line tool and a Streamlit page bound to 127.0.0.1. No FastAPI, no public web app, no browser extension, no cloud accounts, and no auto-apply until the metrics in section 9 beat a plain LLM baseline.

| Week | Focus | Done when |
| :---: | :--- | :--- |
| 1 | Confirmable profile | DOCX and single-column text PDF parse into the atom schema. Conflicts are detected. A human can confirm or edit in the CLI. Golden set holds 15–20 real CVs. |
| 2 | Job analysis | Pasted text becomes requirements plus hard constraints. Checklist uses the four statuses. Years do not double-count overlap. |
| 3 | Generation and validator | Fixed ATS resume template. CV generator and optional cover letter. Python validator and the adversarial CI test. Readable markdown diff. |
| 4 | Export and prep | CLI runs the full path. Single-column DOCX export. Interview prep includes "what not to say". p95 latency is measured on an already confirmed profile. |

Week-4 success: zero assertions without `evidence_id` on the golden set, a diff a person can use, and human review plus correction under 5 minutes.

### Build order

1. Atom schema and confirmation states.
2. Deterministic validator and the adversarial test, before generation prompts.
3. Parsers and conflict detection.
4. Minimal confirmation CLI.
5. Job analysis and the years calculation.
6. Constrained generation.
7. Diff and DOCX export.
8. Interview prep.

### Stack

- Python 3.11+.
- Pydantic for every model boundary.
- `pdfplumber` and `python-docx`.
- Gemini or GPT-4o only behind structured output. They propose text. They do not validate it.
- LangGraph only if the pipeline needs an explicit state machine.

## 9. Metrics before any larger UI

Compare each package with the same CV and job description given to a plain ChatGPT or Claude session:

1. Unsupported assertions.
2. Changed facts (dates, titles, verbs, metrics).
3. Candidate review time.
4. Manual corrections.
5. Perceived quality of the three documents.
6. Cost and latency per package.

## 10. Data

Resumes and certificates are sensitive. Store them locally. Record purpose, retention, and deletion. Do not add a hosted account system in phase 1.

## 11. Cut from the original proposal

| Item | Decision |
| :--- | :--- |
| "Zero-hallucination" as a promise | Removed |
| Calling generation deterministic | Removed. The flow and the validator are deterministic |
| Multi-agent personas | Removed |
| URL scraper as the core input | Secondary |
| Reordering chronology | Forbidden |
| One match score | Forbidden |
| Mandatory cover letter | Optional |
| Inferring the employer's current problems | Forbidden |
| Decorative or multi-column PDF | Avoided |
| FastAPI in phase 1 | Out of scope |

## 12. Pasted-job model phase

Locked on 19 October 2026. This section is the contract for the work that follows phase 1. Search feeds are not part of it.

The candidate pastes a job. A model may read that posting and propose requirements, evidence ids, and a draft. Python checks every id and every sentence. The candidate confirms the job before a resume is drafted. A cover letter is drafted only when the posting asks for one and the candidate confirms that document too. A portfolio, a writing sample, or a transcript is named and is not drafted.

When the original CV file is `sample-cv.pdf`, the draft copies that file. The model may propose a new summary and a new Technical Skills section. It may not propose a new role, bullet, date, employer, education line, or publication. Python still rejects a skill that the file does not already contain.

The percentage remains the checklist tally in section 5. It does not authorize a sentence. Years and hard constraints stay on the deterministic calculation. The model does not replace them.

A model call is sent only when all three are true:

1. `CAREER_COPILOT_MODEL` is `on`.
2. `XAI_API_KEY` is set in the environment. The key is not stored in the repository or the profile.
3. `CAREER_COPILOT_QUOTA_USD` is the quota the candidate already has, as a positive number of dollars.

The app may spend at most 10 percent of that quota on a calendar day. The output length of a call is capped so the worst case fits under the remainder. At the cap the app does not send the call and says `Stopped. Reached the daily limit.` No purchase, no invoice, and no auto top-up belong to this phase. CV text is not written to logs.

While any of the three is missing, no call is made. The local checklist and the resume built from confirmed sentences are the result. English postings are the first reader. A cheaper model scores a posting. A stronger model drafts. LinkedIn stays paste-only. The app does not apply.
