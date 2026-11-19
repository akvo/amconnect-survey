# AMVest 2026 investor survey

Draft, 19 Nov 2026. A short ranked survey, opened from the QR code on the teaser video's end card. It sits alongside the conversation logging at AMVest (23–26 Nov) and feeds the Jan–Feb sub-working-group workshops.

## Files in this folder

| File | What it is | Who edits it |
| --- | --- | --- |
| `questions.json` | The questions and options; the survey, validation and dashboard all read from it | Anyone, to change the survey |
| `index.html` | The public survey page (`/`) | Rarely |
| `admin.html` | The results dashboard (`/admin`, behind a passcode), with an Excel download | Rarely |
| `manage.html` | The manage page (`/admin/manage`): every response in an expandable table, with remove and restore | Rarely |
| `login.html` | The passcode page shown at `/admin` before logging in | Rarely |
| `server.py` | A single-file server using only the Python standard library; stores responses in SQLite | Developers |
| `Dockerfile` | For Akvo's hosting | Developers |

## Running and hosting

- **Local:** set `ADMIN_PASSCODE` in `.env` (copy `.env.example`; `.env` is git-ignored), run `python3 server.py`, then open http://localhost:8000. The dashboard is at `/admin`: enter the passcode. A login lasts 12 hours, and changing the passcode logs everyone out.
- **Downloads:** **Download Excel** gives an `.xlsx` with two sheets, Responses and Codebook (every code with its question and wording).
- **Rogue entries:** on **Manage responses**, open a row to see all its answers, then press **Remove from results**. A removed entry leaves the dashboard and the Excel download but stays in the database, so **Restore to results** brings it back.
- **Clearing test responses before the event:** stop the server, rename `survey.db` (for example to `survey-test-backup.db`), and start the server again. It creates a new empty database. On Akvo's hosting, do the same with `/data/survey.db`.
- **Akvo hosting needs three things:**
  1. **HTTPS** in front of the server, because the passcode is only safe over HTTPS.
  2. **A persistent volume at `/data`.** Without one, responses are lost every time the container restarts.
  3. **`ADMIN_PASSCODE`** set as an environment secret (it overrides `.env`, which is not shipped in the Docker image). Share it only with the people who should see results.
- **QR links:** use `https://<host>/?src=video` for the video end card and `?src=booth` for printed cards, so the dashboard can show which channel each response came from.
- **Editing questions:** change `questions.json`. Every question has a code (`Q1`–`Q6`) and every option a code (`1`, `2`… with `98` for "None of these" and `99` for "Other"). Together they make the analysis codes shown in the spreadsheet and dashboard, such as `Q2.1`. Answers are saved by option `id`, so you can reword any question or option at any time without breaking the analysis. Once responses come in, never change an `id` or `code`, and never reuse one for a different meaning. To add an option, give it a new `id` and the next unused code. The server refuses to start if codes are missing or duplicated.

## How the survey leads to design decisions

| Question | Design decision it informs |
| --- | --- |
| Q1 Role | Which user segments to design for first. All other results can be filtered by role. |
| Q2 Most valuable × Q3 Hardest to access | Which content and data layers to build first, and in which module. Each option carries a module tag. |
| Q4 Most useful | Product priorities: maps, comparisons, downloads, guidance, contacts |
| Q5 AI features | Which AI features go into v1. The share choosing "None of these" shows how much demand for AI there is overall. |
| Q6 Comments (optional, open text) | Needs the closed questions missed. Read by hand on the dashboard and grouped into themes with the "Other" answers. |

## How we process the data

The dashboard calculates steps 1–3 live. Steps 4–6 are done by hand.

1. **Score.** Rank 1 earns 3 points, rank 2 earns 2 and rank 3 earns 1. Each option's score is shown as a share of the maximum possible, next to the % of respondents who put it in their top 3.
2. **Value × difficulty.** Q2 and Q3 share one option list, so the dashboard plots each information area by its Q2 score (value) against its Q3 score (difficulty), with dashed lines at the average of each. The chart shows the data only; the team interprets it.
3. **Segment by role** with the role filter. With fewer than 5 responses in a view, the dashboard shows counts instead of percentages.
4. **Read the "Other" answers and comments by hand**, listed on the dashboard, and group them into themes.
5. **Triangulate with the conversation logs.** At an expected 20–50 responses, the survey shows direction, not statistical results. Act on a finding only when the survey and the conversations agree.
6. **Output:** a one-page ranked list of decisions by module for the Jan–Feb workshops, built from the spreadsheet download.

## Critique of the original questions

**What works**
- The questions are closed and capped, which forces trade-offs and keeps the survey fast.
- Q2 allows information that is "currently unavailable", so it captures needs, not just what is already familiar.
- Q3 (hardest to access) is the most valuable question, because it reveals gaps nobody else will tell us about.
- Q5 tests specific AI features instead of asking "do you want AI?".

**What didn't work, and what changed in `questions.json`**

| Issue | Change |
| --- | --- |
| Q2 and Q3 had different option lists (8 vs 7 options, different groupings), so value couldn't be compared with difficulty | One shared list of 7 areas plus Other. Q3 reuses Q2's options through `options_from`. |
| Q1 had only investor roles, although the video also plays to Member State delegations. "Investor" and "Potential investor" overlapped. | Roles that matter for design: mining company, processing / downstream / offtaker, financier or fund, advisor or analyst, government or ASEAN official, other |
| In Q4, "Reliable and regularly updated data" would win by default and say nothing about trade-offs | Removed, because it is a baseline requirement. "Other" added. |
| Q5 didn't test the features the video promises (plain-language search, opportunity mapping). Its 20–25-word options were unreadable on a phone. With no "None" option, every answer looked like support for AI. | Six short labels, including the two features from the video, plus "None of these", which can't be combined with other picks |
| Q3 asked for "the most difficult" information area but allowed two answers | Now "hardest… up to 3, hardest first" |
| The "Other" options had no text box | Choosing Other opens a text box (300 characters max) |
| "30–45 seconds" understated the time | Now "About 1 minute 30 seconds" |
| There was no room for anything outside the fixed options | An optional open question for additional information or comments (2,000 characters max) |

## Still to decide
- **Survey title.** It still says "Investor Quick Survey" although government officials can now answer. Consider "AMConnect Quick Survey".
- **Follow-up contacts.** The email question was replaced by the comments question, so the survey no longer collects a list of people for follow-up calls. To bring it back, add a question with `"type": "email"` to `questions.json`; the server still supports it.
- **QR code.** Generate it once Akvo confirms the URL.
