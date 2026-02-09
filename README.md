# Nextcloud Forms to Tables Sync

Syncs submissions from Nextcloud Forms to Nextcloud Tables via API. Deduplicates by submission timestamp.

## Features

- ✅ Reads Forms API v3 submissions (`/ocs/v2.php/apps/forms/api/v3/forms/{id}/submissions`)
- ✅ Writes to Tables API v1 (`/index.php/apps/tables/api/1/tables/{id}/rows`)
- ✅ Handles Forms v3 `answers` list structure (questionId → text/choice/etc.)
- ✅ Converts Unix timestamps to ISO 8601 datetime strings
- ✅ Deduplicates by exact timestamp match (no external files needed)
- ✅ Fully configurable via `.env`
- ✅ Production logging and error handling

## Requirements

```bash
pip install requests python-dotenv
```

Nextcloud apps required:
- [Forms](https://apps.nextcloud.com/apps/forms)
- [Tables](https://apps.nextcloud.com/apps/tables)

## Setup

1. **Copy config**:
```bash
cp .env.example .env
```

2. **Edit `.env`** with your values:
```
NC_URL=https://your-nextcloud.com
NC_USERNAME=your-username
NC_APP_PASSWORD=your-app-password
FORM_ID=123
TABLE_ID=456
TIMESTAMP_COLUMN_NAME="Zeitstempel"
FIELD_MAPPING={"44":"Work E-Mail","50":"Name of challenge",...}
```

3. **Secure the file**:
```bash
chmod 600 .env
```

4. **Generate App Password**:
   - Nextcloud → Settings → Personal → Security → "Create new app password"

## Usage

```bash
python transfer_forms_to_table.py
```

**Output**:
```
INFO:__main__:Retrieved 3 form responses
INFO:__main__:Retrieved 19 columns from table 32
INFO:__main__:Collected 2 existing timestamps from table 32
INFO:__main__:Skipping submission 33 (timestamp 2026-01-15T16:28:48+01:00 already in table)
INFO:__main__:Successfully wrote row to table 32
INFO:__main__:Synced 1 new responses
Synced 1 rows
```

## Configuration

### `.env` Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `NC_URL` | Nextcloud base URL | `https://my-url.org` |
| `NC_USERNAME` | Nextcloud username | `my.user` |
| `NC_APP_PASSWORD` | App password (not main password) | `my-password` |
| `FORM_ID` | Form ID (integer) | `11` |
| `TABLE_ID` | Table ID (integer) | `32` |
| `TIMESTAMP_COLUMN_NAME` | Exact column title for timestamps | `Zeitstempel` |
| `FIELD_MAPPING` | JSON `{questionId: "column title"}` | `{"44":"Work E-Mail","50":"Name of challenge"}` |

### Field Mapping

Get question IDs from:
```
GET /ocs/v2.php/apps/forms/api/v3/forms/{FORM_ID}?format=json
```

**Example**:
```json
{
  "44": "Work E-Mail",
  "50": "Name of challenge", 
  "53": "Challenge Description"
}
```

## How It Works

```
1. Forms API → List submissions (id, timestamp, answers[])
2. Tables API → Get columns {title: id}
3. Tables API → Get existing timestamps from Zeitstempel column
4. For each submission:
   ├─ Skip if timestamp already exists
   ├─ Transform answers[] → {columnId: value}
   └─ Convert timestamp → ISO 8601 string
5. Tables API → POST new rows
```

## API Endpoints Used

| Purpose | URL |
|---------|-----|
| List submissions | `GET /ocs/v2.php/apps/forms/api/v3/forms/{id}/submissions` |
| List columns | `GET /index.php/apps/tables/api/1/tables/{id}/columns` |
| List rows | `GET /index.php/apps/tables/api/1/tables/{id}/rows` |
| Create row | `POST /index.php/apps/tables/api/1/tables/{id}/rows` |

## Deployment

### Cron (every 15 minutes)
```bash
*/15 * * * * cd /path/to/script && /usr/bin/python3 transfer_forms_to_table.py >> /var/log/forms_to_tables.log 2>&1
```

### Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "transfer_forms_to_table.py"]
```

## Troubleshooting

| Error | Fix |
|-------|-----|
| `404` Forms | Check `FORM_ID`, ensure Forms app enabled |
| `404` Tables | Check `TABLE_ID`, ensure Tables app enabled |
| Empty rows | Verify `FIELD_MAPPING` questionIds match form |
| `Column not found` | Check exact column titles (case-sensitive, spaces) |
| `Missing env vars` | Copy `.env.example` → `.env` and fill values |

**Debug**: Add `logging.basicConfig(level=logging.DEBUG)` for full HTTP traces.

## Files

```
├── transfer_forms_to_table.py     # Main script
├── .env                          # Your config (gitignored)
├── .env.example                  # Template (git tracked)
└── README.md                     # This file
```