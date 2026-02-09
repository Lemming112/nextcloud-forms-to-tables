import requests
import json
import logging
import os
from typing import Dict, List, Optional, Set
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NextcloudFormToTable:
    def __init__(self, nc_url: str, username: str, app_password: str):
        self.base_url = nc_url.rstrip("/")
        self.auth = (username, app_password)
        self.headers = {"OCS-APIRequest": "true"}
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update(self.headers)

    # ---------- Forms (OCS v3) ----------

    def get_form_responses(self, form_id: str) -> List[Dict]:
        """
        /ocs/v2.php/apps/forms/api/v3/forms/{formId}/submissions?format=json
        """
        try:
            url = f"{self.base_url}/ocs/v2.php/apps/forms/api/v3/forms/{form_id}/submissions"
            response = self.session.get(
                url,
                params={"format": "json"},
                headers={"OCS-APIRequest": "true"},
            )
            response.raise_for_status()

            data = response.json()
            submissions = data.get("ocs", {}).get("data", {}).get("submissions", [])
            logger.info("Retrieved %d form responses", len(submissions))
            return submissions
        except requests.exceptions.RequestException as e:
            logger.error("Failed to fetch form responses: %s", e)
            return []

    # ---------- Tables (REST v1) ----------

    def get_table_columns(self, table_id: int) -> Dict[str, int]:
        """
        GET /index.php/apps/tables/api/1/tables/{tableId}/columns?format=json
        """
        try:
            url = f"{self.base_url}/index.php/apps/tables/api/1/tables/{table_id}/columns"
            response = self.session.get(url, params={"format": "json"})
            response.raise_for_status()

            columns = response.json()
            column_map = {col["title"]: col["id"] for col in columns}
            logger.info("Retrieved %d columns from table %s", len(column_map), table_id)
            return column_map
        except requests.exceptions.RequestException as e:
            logger.error("Failed to fetch table columns: %s", e)
            return {}

    def get_existing_timestamps(
        self,
        table_id: int,
        column_map: Dict[str, int],
        timestamp_column: str,
    ) -> Set[str]:
        """
        Read all rows from the table and collect values from the timestamp column.
        """
        existing: Set[str] = set()

        if timestamp_column not in column_map:
            logger.warning("Timestamp column '%s' not found in table", timestamp_column)
            return existing

        ts_col_id = column_map[timestamp_column]

        try:
            url = f"{self.base_url}/index.php/apps/tables/api/1/tables/{table_id}/rows"
            response = self.session.get(url, params={"format": "json"})
            response.raise_for_status()
            rows = response.json()

            for row in rows:
                cells = row.get("data", [])
                if isinstance(cells, list):
                    for cell in cells:
                        if cell.get("columnId") == ts_col_id:
                            val = cell.get("value")
                            if isinstance(val, str) and val:
                                existing.add(val)
                            break

            logger.info(
                "Collected %d existing timestamps from table %s",
                len(existing),
                table_id,
            )
        except requests.exceptions.RequestException as e:
            logger.error("Failed to fetch table rows for existing timestamps: %s", e)

        return existing

    def transform_response_to_row(
        self,
        form_response: Dict,
        column_map: Dict[str, int],
        field_mapping: Dict[int, str],
        timestamp_column: Optional[str] = None,
    ) -> Dict[int, str]:
        """
        Convert one form submission into Tables row data {column_id: value}.
        """
        row_data: Dict[int, str] = {}

        # --- map answers (Forms v3: answers is a list) ---
        answers = form_response.get("answers", [])
        answer_by_qid: Dict[int, Dict] = {}
        for ans in answers:
            qid = ans.get("questionId")
            if qid is not None:
                answer_by_qid[qid] = ans

        for question_id, table_col_name in field_mapping.items():
            if table_col_name not in column_map:
                logger.warning("Column '%s' not found in table", table_col_name)
                continue

            col_id = column_map[table_col_name]
            ans = answer_by_qid.get(question_id)
            if not ans:
                continue

            a_type = ans.get("type")
            value = ""

            if "text" in ans and isinstance(ans["text"], str):
                value = ans["text"]
            elif a_type == "choice":
                value = str(ans.get("optionText") or ans.get("optionId") or "")
            elif a_type == "multiple":
                options = ans.get("options") or []
                value = ", ".join(
                    o.get("optionText", "") if isinstance(o, dict) else str(o)
                    for o in options
                )
            elif a_type == "date":
                value = ans.get("date", "")
            else:
                value = json.dumps(ans, ensure_ascii=False)

            row_data[col_id] = value

        # --- map submission timestamp to datetime column ---
        if timestamp_column and timestamp_column in column_map:
            ts = form_response.get("timestamp")
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
                iso_str = dt.isoformat()
                ts_col_id = column_map[timestamp_column]
                row_data[ts_col_id] = iso_str
            else:
                logger.warning("Submission has no numeric timestamp: %r", ts)

        return row_data

    def write_row_to_table(self, table_id: int, row_data: Dict[int, str]) -> bool:
        """
        POST /index.php/apps/tables/api/1/tables/{tableId}/rows
        """
        try:
            url = f"{self.base_url}/index.php/apps/tables/api/1/tables/{table_id}/rows"
            payload = {"data": row_data}
            response = self.session.post(
                url,
                json=payload,
                params={"format": "json"},
            )
            response.raise_for_status()
            logger.info("Successfully wrote row to table %s", table_id)
            return True
        except requests.exceptions.RequestException as e:
            logger.error("Failed to write row to table: %s", e)
            return False

    def sync_form_to_table(
        self,
        form_id: int,
        table_id: int,
        field_mapping: Dict[int, str],
        timestamp_column: Optional[str] = None,
    ) -> int:
        """
        Sync form responses to table.

        De-duplication: a submission is skipped if its converted timestamp
        already exists in the timestamp_column of the table.
        """
        responses = self.get_form_responses(str(form_id))
        if not responses:
            return 0

        column_map = self.get_table_columns(table_id)
        if not column_map:
            return 0

        existing_ts: Set[str] = set()
        if timestamp_column:
            existing_ts = self.get_existing_timestamps(
                table_id, column_map, timestamp_column
            )

        synced_count = 0

        for response in responses:
            # compute timestamp for this submission in the same format
            current_iso_ts = None
            if timestamp_column and isinstance(response.get("timestamp"), (int, float)):
                dt = datetime.fromtimestamp(
                    response["timestamp"], tz=timezone.utc
                ).astimezone()
                current_iso_ts = dt.isoformat()

                if current_iso_ts in existing_ts:
                    logger.info(
                        "Skipping submission %s (timestamp %s already in table)",
                        response.get("id"),
                        current_iso_ts,
                    )
                    continue

            row_data = self.transform_response_to_row(
                response,
                column_map,
                field_mapping,
                timestamp_column=timestamp_column,
            )

            if self.write_row_to_table(table_id, row_data):
                synced_count += 1
                if current_iso_ts:
                    existing_ts.add(current_iso_ts)

        logger.info("Synced %d new responses", synced_count)
        return synced_count


if __name__ == "__main__":
    # Read config from environment variables
    NC_URL = os.getenv("NC_URL")
    USERNAME = os.getenv("NC_USERNAME")
    APP_PASSWORD = os.getenv("NC_APP_PASSWORD")
    FORM_ID = int(os.getenv("FORM_ID"))
    TABLE_ID = int(os.getenv("TABLE_ID"))
    TIMESTAMP_COLUMN_NAME = os.getenv("TIMESTAMP_COLUMN_NAME")
    
    # Parse field mapping from JSON string
    field_mapping_json = os.getenv("FIELD_MAPPING")
    if not field_mapping_json:
        logger.error("FIELD_MAPPING env var is required")
        exit(1)
    try:
        FIELD_MAPPING: Dict[int, str] = {
            int(k): v for k, v in json.loads(field_mapping_json).items()
        }
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.error("Invalid FIELD_MAPPING JSON: %s", e)
        exit(1)

    if not all([NC_URL, USERNAME, APP_PASSWORD, FORM_ID, TABLE_ID, TIMESTAMP_COLUMN_NAME]):
        logger.error("Missing required environment variables. See .env.example")
        exit(1)

    syncer = NextcloudFormToTable(NC_URL, USERNAME, APP_PASSWORD)
    synced = syncer.sync_form_to_table(
        FORM_ID,
        TABLE_ID,
        FIELD_MAPPING,
        timestamp_column=TIMESTAMP_COLUMN_NAME,
    )
    print(f"Synced {synced} rows")

