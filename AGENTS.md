# AI Career Copilot

`SPEC.md` is the product contract. Read it before changing behavior. If this file and `SPEC.md` disagree, `SPEC.md` wins.

The system prepares a resume, an optional cover letter, and interview prep. The human reviews and submits. Do not build auto-apply.

## Phase 1

- Local Python 3.11+ CLI, plus a local Streamlit page on 127.0.0.1.
- No FastAPI, public web app, browser extension, accounts, or hosted storage.
- One linear pipeline. No persona agents and no CrewAI.
- LangGraph is allowed only as a state machine with explicit edges and the human checkpoint.
- Job input is pasted text. URL scraping is out of the core path.
- Models propose structured text. They do not judge their own output.

## Gates

- Show document conflicts. Never merge them silently.
- Stop for human confirmation before the first generation.
- Generate only from atoms whose `confirmation` is `confirmed`.
- Keep employment order fixed. Rewrite only the summary, the skills block, and bullets inside existing roles.
- Every generated sentence cites at least one `evidence_id`.
- Employer, title, dates, and numbers must match the atom exactly.
- Skills must be on the confirmed whitelist.
- Ownership verbs and synonyms come from the explicit tables in `SPEC.md`. Do not add a verb or a synonym inside a prompt.
- A confirmed gap appears only in interview prep.
- Hard constraints return `pass`, `fail`, or `unknown`. Never a similarity score and never a match percentage.
- Years of experience use interval intersection. Do not add overlapping jobs together.
- Export a single-column DOCX plus a readable diff of what changed and what was refused.

## Validator first

- Implement the deterministic Python validator and the adversarial CI test before generation prompts.
- The adversarial test adds a required skill that is missing from the confirmed profile. The build fails if that skill appears on the CV or is implied in the cover letter.
- Do not use an LLM as a judge.

## Build order

1. Atom schema.
2. Validator and adversarial test.
3. DOCX and single-column text-PDF parsers, plus conflict detection.
4. Confirmation CLI.
5. Job checklist and years calculation.
6. Constrained generation.
7. Diff and DOCX export.
8. Interview prep, including "what not to say".

## Tests

- Golden set: 15–20 real CVs. Success is zero assertions without `evidence_id`.
- Cover the validator rejections in `SPEC.md` section 7 with deterministic tests.
- Do not treat a prompt snapshot as proof that a claim is grounded.

## Data

- Keep source documents local.
- Do not log full CVs, certificates, or model payloads that contain them.

## Git and communication

- This folder is not a git repository yet. Do not initialize one unless the user asks.
- Do not commit, push, or publish unless the user says `confirmo`.
- Speak English. Keep updates short. Do not add product scope that `SPEC.md` does not already allow.
