import wmill
import requests
import logging
import json
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NextcloudFormToTable:
    def __init__(self, nc_url: str, username: str, app_password: str):
        self.base_url = nc_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, app_password)
        self.session.headers.update({
            "OCS-APIRequest": "true",
            "Accept": "application/json",
        })

    def get_form_responses(self, form_id: int) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/ocs/v2.php/apps/forms/api/v3/forms/{form_id}/submissions"
        response = self.session.get(
            url,
            params={"format": "json"},
            timeout=60,
        )
        response.raise_for_status()

        data = response.json()
        submissions = data.get("ocs", {}).get("data", [])

        if isinstance(submissions, dict) and "submissions" in submissions:
            submissions = submissions["submissions"]

        if not isinstance(submissions, list):
            submissions = []

        logger.info("Retrieved %d form responses", len(submissions))
        return submissions

    def get_table_columns(self, table_id: int) -> Dict[str, int]:
        url = f"{self.base_url}/index.php/apps/tables/api/1/tables/{table_id}/columns"
        response = self.session.get(
            url,
            params={"format": "json"},
            timeout=60,
        )
        response.raise_for_status()

        columns = response.json()
        column_map = {col["title"]: col["id"] for col in columns}
        logger.info("Retrieved %d columns from table %s", len(column_map), table_id)
        return column_map

    def get_existing_timestamps(
        self,
        table_id: int,
        column_map: Dict[str, int],
        timestamp_column: str,
    ) -> Set[str]:
        existing: Set[str] = set()

        if timestamp_column not in column_map:
            logger.warning("Timestamp column '%s' not found in table", timestamp_column)
            return existing

        ts_col_id = column_map[timestamp_column]

        try:
            url = f"{self.base_url}/index.php/apps/tables/api/1/tables/{table_id}/rows"
            response = self.session.get(
                url,
                params={"format": "json"},
                timeout=60,
            )
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

        except requests.exceptions.RequestException as e:
            logger.error("Failed to fetch table rows for existing timestamps: %s", e)

        return existing

    def _extract_answer_value(self, ans: Dict[str, Any]) -> str:
        a_type = ans.get("type")

        if "text" in ans and isinstance(ans["text"], str):
            return ans["text"]

        if a_type == "choice":
            return str(ans.get("optionText") or ans.get("optionId") or "")

        if a_type == "multiple":
            options = ans.get("options") or []
            values = []
            for o in options:
                if isinstance(o, dict):
                    values.append(str(o.get("optionText") or o.get("optionId") or ""))
                else:
                    values.append(str(o))
            return ", ".join(v for v in values if v)

        if a_type == "date":
            return str(ans.get("date", ""))

        if "value" in ans and ans["value"] is not None:
            return str(ans["value"])

        return json.dumps(ans, ensure_ascii=False)

    def transform_response_to_row(
        self,
        form_response: Dict[str, Any],
        column_map: Dict[str, int],
        field_mapping: Dict[int, str],
        timestamp_column: Optional[str] = None,
    ) -> Dict[int, str]:
        row_data: Dict[int, str] = {}

        answers = form_response.get("answers", [])
        answer_by_qid: Dict[int, Dict[str, Any]] = {}

        for ans in answers:
            qid = ans.get("questionId")
            if qid is not None:
                try:
                    answer_by_qid[int(qid)] = ans
                except (TypeError, ValueError):
                    continue

        for question_id, table_col_name in field_mapping.items():
            if table_col_name not in column_map:
                logger.warning("Column '%s' not found in table", table_col_name)
                continue

            col_id = column_map[table_col_name]
            ans = answer_by_qid.get(question_id)
            if not ans:
                continue

            row_data[col_id] = self._extract_answer_value(ans)

        if timestamp_column and timestamp_column in column_map:
            ts = form_response.get("timestamp")
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
                row_data[column_map[timestamp_column]] = dt.isoformat()

        return row_data

    def write_row_to_table(self, table_id: int, row_data: Dict[int, str]) -> bool:
        try:
            url = f"{self.base_url}/index.php/apps/tables/api/1/tables/{table_id}/rows"
            response = self.session.post(
                url,
                json={"data": row_data},
                params={"format": "json"},
                timeout=60,
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
        skip_existing_timestamps: bool = True,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        submissions = self.get_form_responses(form_id)
        column_map = self.get_table_columns(table_id)

        existing_timestamps: Set[str] = set()
        if timestamp_column and skip_existing_timestamps:
            existing_timestamps = self.get_existing_timestamps(
                table_id=table_id,
                column_map=column_map,
                timestamp_column=timestamp_column,
            )

        prepared_rows = []
        synced_count = 0
        skipped_count = 0

        for submission in submissions:
            row_data = self.transform_response_to_row(
                form_response=submission,
                column_map=column_map,
                field_mapping=field_mapping,
                timestamp_column=timestamp_column,
            )

            if not row_data:
                continue

            if timestamp_column and timestamp_column in column_map:
                ts_col_id = column_map[timestamp_column]
                ts_value = row_data.get(ts_col_id)
                if skip_existing_timestamps and ts_value and ts_value in existing_timestamps:
                    skipped_count += 1
                    continue

            prepared_rows.append(row_data)

            if not dry_run:
                if self.write_row_to_table(table_id, row_data):
                    synced_count += 1

        return {
            "ok": True,
            "dry_run": dry_run,
            "form_id": form_id,
            "table_id": table_id,
            "column_map": column_map,
            "rows_preview": prepared_rows[:20],
            "submission_count": len(submissions),
            "synced_row_count": synced_count,
            "prepared_row_count": len(prepared_rows),
            "skipped_existing_count": skipped_count,
        }


def _get_required(config: dict, key: str) -> Any:
    value = config.get(key)
    if value is None or value == "":
        raise ValueError(f"{key} fehlt in der Windmill-Resource")
    return value


def main():
    """
    config = Windmill Resource Inhalt, z. B.:
    {
      "NC_URL": "...",
      "NC_USERNAME": "...",
      "NC_APP_PASSWORD": "...",
      "FORM_ID": "15",
      "TABLE_ID": "20",
      "TIMESTAMP_COLUMN_NAME": "timestamp",
      "FIELD_MAPPING": "{\"65\":\"Name des Kindes\"}"
    }
    """
    dry_run: bool = False
    skip_existing_timestamps: bool = True

    config = wmill.get_resource("u/thiemohagedorn/worthwhile_c_my_resource_type")
    nc_url = _get_required(config, "NC_URL")
    nc_username = _get_required(config, "NC_USERNAME")
    nc_app_password = _get_required(config, "NC_APP_PASSWORD")
    form_id = int(_get_required(config, "FORM_ID"))
    table_id = int(_get_required(config, "TABLE_ID"))

    timestamp_column_name = config.get("TIMESTAMP_COLUMN_NAME")

    mapping = {
        "65": "Name des Kindes",
        "67": "Art der Behandlung",
        "69": "Behandlung",
        "72": "Verbrauchte Gegenstände",
        "73": "Datum",
        "74": "Zeit",
        "75": "Leiter",
        "76": "Medikamenten Liste"
    }

    #field_mapping_raw = _get_required(config, "FIELD_MAPPING")
    field_mapping_raw = mapping
    if isinstance(field_mapping_raw, str):
        field_mapping_loaded = json.loads(field_mapping_raw)
    elif isinstance(field_mapping_raw, dict):
        field_mapping_loaded = field_mapping_raw
    else:
        raise ValueError("FIELD_MAPPING muss String oder Objekt sein")

    field_mapping = {
        int(k): str(v) for k, v in field_mapping_loaded.items()
    }

    syncer = NextcloudFormToTable(
        nc_url=nc_url,
        username=nc_username,
        app_password=nc_app_password,
    )

    return syncer.sync_form_to_table(
        form_id=form_id,
        table_id=table_id,
        field_mapping=field_mapping,
        timestamp_column=timestamp_column_name,
        skip_existing_timestamps=skip_existing_timestamps,
        dry_run=dry_run,
    )
