from transfer_forms_to_table.py import NextcloudFormToTable


def test_transform_response_to_row():
    syncer = NextcloudFormToTable(
        "https://example.com",
        "user",
        "password",
    )

    form_response = {
        "answers": [
            {
                "questionId": 1,
                "type": "text",
                "text": "Max Mustermann",
            },
            {
                "questionId": 2,
                "type": "choice",
                "optionText": "Ja",
            },
        ],
        "timestamp": 1750000000,
    }

    column_map = {
        "Name": 10,
        "Antwort": 11,
        "Zeitstempel": 12,
    }

    field_mapping = {
        1: "Name",
        2: "Antwort",
    }

    row = syncer.transform_response_to_row(
        form_response,
        column_map,
        field_mapping,
        timestamp_column="Zeitstempel",
    )

    assert row[10] == "Max Mustermann"
    assert row[11] == "Ja"
    assert 12 in row
