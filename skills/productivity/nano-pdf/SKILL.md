---
name: nano-pdf
description: Edit PDFs with natural-language instructions using the nano-pdf CLI. Modify text, fix typos, update titles, and make content changes to specific pages without manual editing.
version: 1.0.0
author: community
license: MIT
metadata:
  hermes:
    tags: [PDF, Documents, Editing, NLP, Productivity]
    homepage: https://pypi.org/project/nano-pdf/
---

# nano-pdf

Edit PDFs using natural-language instructions. Point it at a page and describe what to change.

## Prerequisites

```bash
# Install with uv (recommended — already available in Hermes)
uv pip install nano-pdf

# Or with pip
pip install nano-pdf
```

## Usage

```bash
nano-pdf edit <file.pdf> <page_number> "<instruction>"
```

## Examples

```bash
# Change a title on page 1
nano-pdf edit deck.pdf 1 "Change the title to 'Q3 Results' and fix the typo in the subtitle"

# Update a date on a specific page
nano-pdf edit report.pdf 3 "Update the date from January to February 2026"

# Fix content
nano-pdf edit contract.pdf 2 "Change the client name from 'Acme Corp' to 'Acme Industries'"
```

## Pitfalls

- **Page numbering**: 0 or 1-based depending on version — retry with `page ± 1` if wrong page edited
- **API key required**: Uses LLM under the hood — set `OPENAI_API_KEY` or configure via `nano-pdf --help`
- **Layout corruption**: Complex formatting (tables, columns, images) may break. Reliable on text-only PDFs
- **Silent partial failures**: Output file created even on partial edits — verify content matches expected changes

## Notes

- Always verify the output PDF after editing
- For large structural changes, consider alternative tools or manual editing
