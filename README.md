# AMConnect survey

A short ranked survey for AMVest 2026, opened from the QR code on the AMConnect teaser video. It includes a passcode-protected results dashboard, an Excel download and a page for removing rogue entries.

It needs only Python 3 (standard library) and stores responses in SQLite.

## Layout

```
server.py               the whole server (Python standard library only)
config/questions.json   the survey questions and options
static/                 the HTML pages and the logo
docs/survey-plan.md     survey design and analysis notes
Dockerfile              for deployment
```

## Run locally

```
cp .env.example .env      # then set ADMIN_PASSCODE (at least 6 characters)
python3 server.py
```

- Survey: http://localhost:8000 (add `?src=video` or `?src=booth` to track the channel)
- Results dashboard: http://localhost:8000/admin
- Manage responses: http://localhost:8000/admin/manage

## Change the questions

Edit `config/questions.json`. You can reword questions and options at any time. Once responses come in, never change or reuse an option's `id` or `code`.

## Deploy

Use the `Dockerfile`. The host needs to provide:
- HTTPS
- a persistent volume at `/data`
- `ADMIN_PASSCODE` as an environment secret

`.env` and `survey.db` are git-ignored and never committed.

See [docs/survey-plan.md](docs/survey-plan.md) for how the questions map to design decisions and how the data is analysed.
