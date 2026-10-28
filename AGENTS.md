# AI Career Copilot

`SPEC.md` is the product contract. Read it before changing behavior. If this file and `SPEC.md` disagree, `SPEC.md` wins.

The open program has four local pages: User, Search, Jobs, and Documents. It reads one CV, suggests roles, searches open jobs, and shows a match. It can write a CV and, when the posting asks, a cover letter. The person reviews and submits. Do not build auto-apply.

`docs/ARCHITECTURE.md` describes the running program. `docs/MATCH.md` is the match contract.

## Open program

- Local Python 3.11+ and a Streamlit page on 127.0.0.1. No FastAPI, public web app, browser extension, accounts, or hosted storage.
- One page flow. No persona agents and no CrewAI.
- The model proposes roles, links, and draft sentences. Python accepts or rejects them. The model does not judge its own output.
- A skill on the written CV must already be in the saved CV. Locked sections stay word for word.
- Search uses the local `grok` login. Do not call `https://api.x.ai`. LinkedIn stays paste-in.
- Do not log full CVs, certificates, or model payloads that contain them.

## Older path

The repository still has the earlier command-line path: atoms, confirmation, a pasted-job checklist, a validator, and a DOCX. It is not on the four pages. Do not extend it unless `SPEC.md` says so.

## Tests

- Tests for the open pages must not call `grok`.
- A prompt snapshot is not proof that a claim is grounded.
- A golden set of 15–20 CVs is not required for the four pages.

## Git and communication

- Local commits record the author name and email already used. Do not change that pair unless the user asks.
- Never push unless the user explicitly asks for that push in the chat.
- Before any push, stop. A public copy must not contain the real name, the real email, the CV, `profile.json`, `.career-copilot/`, or other source documents. Existing commits that contain the real name or email are rewritten to a stand-in name and a private noreply email before that push.
- The CV, the thesis, `profile.json`, generated resumes, and `.career-copilot/` stay out of git.
- Speak English. Keep updates short. Do not add product scope that `SPEC.md` does not already allow.
