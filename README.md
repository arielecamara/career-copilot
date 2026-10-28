# Career Copilot

Local tool that reads one CV, suggests roles, searches open jobs, and shows how the profile matches a vacancy. It can draft a CV and, when the posting asks, a cover letter. The person reviews every file and submits the application. The program does not apply to jobs.

## Run

Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\career-copilot.exe ui
```

Open http://127.0.0.1:8501. The page stays on this computer. Sign in to the local `grok` program before using role suggestions or job search. Do not set an API key for that path.

## Pages

1. User. Upload one DOCX or single-column text PDF. Keep, drop, or add roles.
2. Search. Choose level, employment, workplace, place, and language. Empty fields are not filters.
3. Jobs. Search open vacancies or paste a posting. The match lists what fits, what might fit, and what is missing.
4. Documents. List CVs written on this computer and whether the person said they applied.

Saved profiles, CVs, and generated files stay outside git.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```
