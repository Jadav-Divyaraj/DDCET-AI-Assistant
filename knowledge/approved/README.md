# Approved DDCET knowledge base

Only material that is approved for DDCET examination preparation belongs here.

1. Put source PDFs or text files inside `files/`.
2. Add one entry per source to `manifest.json`.
3. Run `python -m gtu_bot.ingest`.
4. Review the ingestion report before starting the bot.

Student uploads must never be copied into this shared folder automatically.

## Manifest fields

- `path`: path relative to this directory, normally `files/<name>.pdf`
- `title`: human-readable source title
- `subject`: DDCET subject name
- `unit`: unit identifier, for example `Unit 1`
- `topic`: optional topic covered by the document
- `document_type`: `syllabus`, `notes`, `paper`, `solution`, or `question_bank`
- `approved`: must be `true` for ingestion
- `include_pages`: optional list of original PDF page numbers to index
