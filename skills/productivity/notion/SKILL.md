---
name: notion
description: Notion API for creating and managing pages, databases, and blocks via curl. Search, create, update, and query Notion workspaces directly from the terminal.
version: 1.0.0
author: community
license: MIT
metadata:
  hermes:
    tags: [Notion, Productivity, Notes, Database, API]
    homepage: https://developers.notion.com
prerequisites:
  env_vars: [NOTION_API_KEY]
---

# Notion API

Use the Notion API via curl to create, read, update pages, databases (data sources), and blocks. No extra tools needed — just curl and a Notion API key.

## Prerequisites

1. Create an integration at https://notion.so/my-integrations
2. Copy the API key (starts with `ntn_` or `secret_`)
3. Store it in `~/.hermes/.env`:
   ```
   NOTION_API_KEY=ntn_your_key_here
   ```
4. **Important:** Share target pages/databases with your integration in Notion (click "..." → "Connect to" → your integration name)

## API Basics

All requests use this pattern:

```bash
curl -s -X GET "https://api.notion.com/v1/..." \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json"
```

The `Notion-Version` header is required. This skill uses `2025-09-03` (latest). In this version, databases are called "data sources" in the API.

## Common Operations

### Search

```bash
curl -s -X POST "https://api.notion.com/v1/search" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{"query": "page title"}'
```

### Get Page

```bash
curl -s "https://api.notion.com/v1/pages/{page_id}" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03"
```

### Get Page Content (blocks)

```bash
curl -s "https://api.notion.com/v1/blocks/{page_id}/children" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03"
```

### Create Page in a Database

```bash
curl -s -X POST "https://api.notion.com/v1/pages" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{
    "parent": {"database_id": "xxx"},
    "properties": {
      "Name": {"title": [{"text": {"content": "New Item"}}]},
      "Status": {"select": {"name": "Todo"}}
    }
  }'
```

### Query a Database

```bash
curl -s -X POST "https://api.notion.com/v1/data_sources/{data_source_id}/query" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{
    "filter": {"property": "Status", "select": {"equals": "Active"}},
    "sorts": [{"property": "Date", "direction": "descending"}]
  }'
```

### Create a Database

```bash
curl -s -X POST "https://api.notion.com/v1/data_sources" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{
    "parent": {"page_id": "xxx"},
    "title": [{"text": {"content": "My Database"}}],
    "properties": {
      "Name": {"title": {}},
      "Status": {"select": {"options": [{"name": "Todo"}, {"name": "Done"}]}},
      "Date": {"date": {}}
    }
  }'
```

### Update Page Properties

```bash
curl -s -X PATCH "https://api.notion.com/v1/pages/{page_id}" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{"properties": {"Status": {"select": {"name": "Done"}}}}'
```

### Add Content to a Page

```bash
curl -s -X PATCH "https://api.notion.com/v1/blocks/{page_id}/children" \
  -H "Authorization: Bearer $NOTION_API_KEY" \
  -H "Notion-Version: 2025-09-03" \
  -H "Content-Type: application/json" \
  -d '{
    "children": [
      {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Hello from Hermes!"}}]}}
    ]
  }'
```

## Property Types

Common property formats for database items:

- **Title:** `{"title": [{"text": {"content": "..."}}]}`
- **Rich text:** `{"rich_text": [{"text": {"content": "..."}}]}`
- **Select:** `{"select": {"name": "Option"}}`
- **Multi-select:** `{"multi_select": [{"name": "A"}, {"name": "B"}]}`
- **Date:** `{"date": {"start": "2026-01-15", "end": "2026-01-16"}}`
- **Checkbox:** `{"checkbox": true}`
- **Number:** `{"number": 42}`
- **URL:** `{"url": "https://..."}`
- **Email:** `{"email": "user@example.com"}`
- **Relation:** `{"relation": [{"id": "page_id"}]}`

## Key Differences in API Version 2025-09-03

- **Databases → Data Sources:** Use `/data_sources/` endpoints for queries and retrieval
- **Two IDs:** Each database has both a `database_id` and a `data_source_id`
  - Use `database_id` when creating pages (`parent: {"database_id": "..."}`)
  - Use `data_source_id` when querying (`POST /v1/data_sources/{id}/query`)
- **Search results:** Databases return as `"object": "data_source"` with their `data_source_id`

## Pitfalls

### Known Failure Modes and Error Patterns

**1. `403 Forbidden` or `"code": "unauthorized"`**
- **Cause**: API key is invalid, expired, or the integration has not been granted access to the target page/database
- **Fix**: Verify key starts with `ntn_` or `secret_`. In Notion UI, open the page → click `...` → `Connections` → add your integration by name. Each page/database must be shared individually

**2. `400 Bad Request` — `"Object does not belong to the owner of this token"`**
- **Cause**: Trying to access a page/database from a different workspace than the integration was created in
- **Fix**: Integrations are workspace-scoped. Create a new integration in the correct workspace, or move the page to the integration's workspace

**3. `429 Too Many Requests`**
- **Cause**: Exceeding the ~3 req/s rate limit, especially during batch operations
- **Fix**: Add `sleep 0.4` between curl calls in loops. For bulk page creation, create pages in a database via a single PATCH with multiple `children` (block append) instead of individual POST requests

**4. Creating pages with wrong property type structure**
- **Cause**: Notion API is strict — sending `{\"Status\": \"Done\"}` instead of `{\"Status\": {\"select\": {\"name\": \"Done\"}}}` returns a 400 error
- **Fix**: Always nest values inside their type wrapper (`select`, `rich_text`, `number`, `date`, etc.). Check the database schema first: `GET /v1/data_sources/{id}` to see property types

**5. `"code": "validation_error"` on database queries**
- **Cause**: Using `database_id` instead of `data_source_id` for query endpoints in API version 2025-09-03, or referencing a property name that doesn't exist
- **Fix**: Use `data_source_id` for `/v1/data_sources/{id}/query`. Verify property names match exactly (case-sensitive) from the schema

**6. Page blocks return 100 items max (truncated content)**
- **Cause**: The `/children` endpoint paginates with a max of 100 blocks per request
- **Fix**: Check `has_more` in the response. If true, use `start_cursor` from the response as a query parameter: `GET /v1/blocks/{id}/children?start_cursor=xxx`

**7. `\"code\": \"limit_exceeded\"` — block children limit**
- **Cause**: A page can have a maximum of 2,500 children blocks
- **Fix**: If approaching the limit, nest content inside toggle blocks or create child pages. For logs/feeds, archive old entries

**8. Missing `NOTION_API_KEY` in environment**
- **Cause**: Key not exported or not in `~/.hermes/.env`
- **Fix**: `export NOTION_API_KEY="ntn_your_key"` or add to `~/.hermes/.env` and restart. Verify with `curl -s https://api.notion.com/v1/users/me -H "Authorization: Bearer $NOTION_API_KEY" -H "Notion-Version: 2025-09-03" | jq .`

## Notes

- Page/database IDs are UUIDs (with or without dashes)
- Rate limit: ~3 requests/second average
- The API cannot set database view filters — that's UI-only
- Use `is_inline: true` when creating data sources to embed them in pages
- Add `-s` flag to curl to suppress progress bars (cleaner output for Hermes)
- Pipe output through `jq` for readable JSON: `... | jq '.results[0].properties'`
