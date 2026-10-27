# How the match works

The Jobs page uses these rules. `docs/ARCHITECTURE.md` describes the rest of the program.

The examples in this file are only examples. The same rules apply to medicine, law, finance, engineering, and any other field.

The search still only finds an open vacancy and opens its link. The person reviews the match and submits the application. The program does not apply.

## What is compared

The match uses two sides.

The profile side is the person's saved skills, education, and the roles they kept.

The vacancy side is only the part that says what the candidate must be or must have. That includes experience, education, skills, and the profile they ask for. Text about the employer, the benefits, the salary, and the deadline is not part of the match.

Each requirement is either essential or nice to have. Essential lines are the ones the vacancy marks as required, must, or qualifications. Nice-to-have lines are the ones it marks as preferred, bonus, or nice to have. An essential line weighs more than a nice-to-have line.

## Three results

| Result | Rule |
|---|---|
| Match | The vacancy and the profile name the same thing, or a true synonym of it. |
| Missing match | The vacancy asks for something, and the profile does not contain that thing or a synonym. |
| Possible match | The profile has something related, or the system is not sure. The person answers Yes or No. |

An unanswered line stays under Possible matches and does not change the percent.

Yes moves that line to Match and raises the percent. The answer is saved as something the person knows.

No moves that line to Missing match and lowers the percent. The answer is saved as something the person does not know.

The same answer is used on the next vacancy. It does not change the CV.

## Synonym and named tool

A synonym is another name for the same concept. If the vacancy uses one name and the profile uses another name of that concept, the line is a Match.

A technology, a method, or a tool keeps its own name. It is a Match only when that name is on the profile. If the vacancy asks for it and the profile does not name it, the line is a Possible match. It is not a Match, and it is not a Missing match, until the person answers.

This split is the same in every field. A medical concept and its other name are a Match. A named drug is a Possible match until the profile contains that name or the person confirms it. A legal concept and its other name are a Match. A named statute or product is a Possible match until it is confirmed. The same applies to finance and to every other field.

## How a requirement is read

From the candidate section of the vacancy, each requirement becomes one line:

- A length of experience, such as a number of years.
- An education requirement, such as a degree.
- A concept, which may have other names.
- A technology, method, or tool, which is matched only by its own name.

Then the line is compared with the skills, education, and kept roles on the profile.

- The same concept, or a synonym of it, is a Match.
- The exact name of a tool on the profile is a Match.
- A tool the vacancy names and the profile does not name is a Possible match.
- An essential concept with no synonym on the profile is a Missing match.
- Wording the system cannot place is a Possible match.

## ESCO

ESCO is the European classification of skills, competences, qualifications, and occupations. Each concept has one preferred name, other names in several languages, and a stable id. Those other names are the synonym list. The program does not invent synonyms inside a prompt.

ESCO is used only as that name list:

1. Take a term from the profile or from the vacancy.
2. Find the ESCO concept whose preferred name or other name is that term.
3. If the vacancy term and the profile term land on the same ESCO id, the line is a Match.
4. If the vacancy names a tool and that exact name is not on the profile, the line is a Possible match. A relation in ESCO does not turn that tool into a Match.
5. If an essential concept shares no ESCO id with the profile, the line is a Missing match.
6. If ESCO has no concept, or the wording is ambiguous, the line is a Possible match.

The vacancy's own essential and nice-to-have labels win. ESCO does not replace them with a generic occupation profile.

## Percent

An essential line weighs 2. A nice-to-have line weighs 1. A Possible match weighs 0 until the person answers.

The percent is the weight of the Match lines divided by the weight of the Match lines and the Missing match lines.

Yes adds that line's weight to the Match side. No adds that line's weight to the Missing match side. An unanswered line stays out of both sides.

A nice-to-have line that is missing still appears, and it weighs less than an essential gap.

## What is saved about the person

One local note is stored for each confirmed term. It stores the term, the ESCO id when there is one, and Yes or No. Yes means the person knows it. No means the person does not. A later vacancy reads that note before it asks again. An unanswered line is not stored. The note is not the CV, and it is not sent to a job site.
