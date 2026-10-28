# AI Career Copilot

Version 3.0 — October 2026. This file is the product contract for the program that opens today.

The program prepares a reading of one CV, a search for open jobs, a match, and the documents the person chooses to keep. The person reviews every file and submits the application. The program does not apply.

`docs/ARCHITECTURE.md` describes how that program runs. `docs/MATCH.md` is the match contract. If this file and those two disagree about the open pages, this file wins.

## 1. What it is

A local Python program and a Streamlit page on 127.0.0.1. There is no account, no public site, and no hosted copy of the CV.

The model may propose roles, skill names, links, and draft sentences. Python accepts or rejects them. The model does not decide that a sentence is true, and it does not emit a score that authorizes a file.

The page starts the `grok` program already signed in on this computer. It does not call `https://api.x.ai`. It cannot read the weekly allowance, so it does not enforce a spending cap.

## 2. The four pages

### User

1. The person uploads one CV: a DOCX, or a single-column text PDF.
2. Python reads the text on this computer. A scanned page or a multi-column file is refused in one sentence.
3. Grok suggests at most eight roles, each with one sentence on why the experience supports it, and at most twelve skill names. A skill is kept only when that name appears in the CV.
4. The page shows the roles. It does not show lines copied from the CV.
5. The person keeps, removes, or types roles. Save writes the chosen roles. The CV text is stored locally and is not part of that roles file.

### Search

Search does not look for jobs. It opens only after the roles are saved.

No question is required. A blank answer is not a filter. The person may choose more than one experience level (Entry level, Senior, Manager, Director, Executive), employment type (Part-time, Full-time, Internship, Volunteer, Contract), and workplace (Local, Remote, Hybrid). There is no Mid-level choice.

No country means worldwide. A country without a city covers the whole country. Remote ignores the city. A typed language removes an automatic result written in another language, and a posting that requires a language that was not typed. A posting that does not show its language stays, with a note. Other notes guide the search and the match. They do not remove a job by themselves.

### Jobs

Jobs does not search when the page opens. The CV is not sent to a job site and is not copied into the job list.

Automatic search builds two or three short queries from the saved skills, or from the kept roles when no skill is saved. Each query adds the saved country, and the word remote when Remote is selected. It asks the fast local model only for https links. Python opens up to three of those links. It drops LinkedIn, private addresses, empty pages, login walls, closed applications, and past deadlines. It keeps the first open page whose quick CV match is at least 65 percent. If none reach that, it keeps the best opened page. If no page was opened, the page says that nothing was found. A link the person already removed stays out.

Manual search is one box. The person pastes the posting, or one public https link. The CV is not sent to that site. LinkedIn, a private address, an empty page, and a login wall ask for the text instead. A paste is listed when it reaches 50 percent.

The opened job shows the cleaned posting, the match percent, what matches, possible matches, what is missing, notes, and the documents. Yes moves a possible line to what matches, raises the percent, and saves that the person knows it. No moves it to what is missing and saves that the person does not. An unanswered line is not saved. Neither answer changes the CV. The same answer is reused when the wording matches a later vacancy.

Keep this job keeps that vacancy on the next search. Remove this job drops it, and a later search skips that link.

### Documents

Every opened job can write a CV. A cover letter is written only when the posting asks for a cover letter or a letter of motivation. Portfolio, transcript, writing sample, certificate, and identity documents are named only when the posting names them. They are not generated.

The written CV is one A4 column, with no photo and no table. Experience, education, publications, languages, and contact stay word for word. Only the summary and the technical skills may change, and only with facts already in the saved CV. A sentence that adds an employer, title, date, number, or skill that is not already there is dropped. If a locked section would change, nothing is written.

Documents lists those files and whether the person said they applied. The program does not submit them.

## 3. Match

The match compares the saved skills, education, and kept roles with the candidate requirements in the vacancy. Employer text, benefits, salary, and the deadline are not part of the match.

A match is the same concept or a true synonym. A missing match is a requirement the profile does not contain. A possible match is related or uncertain, including a named tool, method, or product whose exact name is not on the profile. The person answers Yes or No. The percent and the weights are defined in `docs/MATCH.md`.

## 4. What stays off the page

- The program does not apply to jobs.
- It does not scrape LinkedIn. A LinkedIn posting is pasted by the person.
- It does not invent a job, a link, or a fact.
- Interview preparation is not on the page.
- A 15–20 CV comparison set is not part of this contract.

Personal files stay on this computer and out of git. That includes the CV, the role and search files, the job list, the Yes and No answers, generated documents, and any profile JSON.

## 5. Earlier pipeline

The repository still contains an earlier command-line path: document atoms, human confirmation, a pasted-job checklist, a deterministic validator, and a single-column DOCX. That path is not on the four pages. New work on the open program follows the sections above. Do not extend the earlier path unless this file says so.
