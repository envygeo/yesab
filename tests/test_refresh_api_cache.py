from __future__ import annotations

import unittest

from scripts import refresh_api_cache


class RefreshApiCacheTests(unittest.TestCase):
    def test_build_url_uses_versioned_integration_endpoint(self) -> None:
        self.assertEqual(
            refresh_api_cache.build_url(2021, 2026),
            "https://yesabregistry.ca/api/v1/integration/projects"
            "?startYear=2021&endYear=2026",
        )

    def test_normalize_record_accepts_versioned_integration_shape(self) -> None:
        record = {
            "projectId": "project-id",
            "projectNumber": "2025-0001",
            "projectURL": "https://yesabregistry.ca/projects/project-id",
            "projectTitle": "Guided Backpacking",
            "projectTypeId": "type-id",
            "projectTypeName": "Evaluation",
            "projectProponent": "Alaska Mountain Guides",
            "projectLocation": [{"latitude": 61.3496, "longitude": -138.988}],
            "projectStage": {
                "id": "stage-id",
                "name": "Decision Document Issued",
                "extended": False,
            },
            "projectStageHistory": [
                {
                    "id": "stage-id",
                    "name": "Draft",
                    "start": 1735685262729,
                    "end": 1735685264761,
                    "extended": False,
                }
            ],
            "assessmentDistricts": [{"id": "district-id", "name": "Haines Junction"}],
            "sectors": [{"id": "sector-id", "name": "Recreation and Tourism"}],
            "indigenousGovernments": [{"id": "gov-id", "name": "Kluane First Nation"}],
            "decisionBodies": [{"id": "body-id", "name": "Parks Canada Yukon"}],
            "planningCommissions": [
                {"id": "plan-id", "name": "Dawson Regional Planning Commission"}
            ],
            "ufaBoards": [{"id": "board-id", "name": "Yukon Heritage Board"}],
            "recommendation": {"id": "rec-id", "name": "Proceed"},
            "decision": {"id": "decision-id", "name": "Vary"},
        }

        normalized = refresh_api_cache.normalize_record(record)

        self.assertEqual(normalized["projectId"], "project-id")
        self.assertEqual(normalized["projectNumber"], "2025-0001")
        self.assertEqual(
            normalized["projectURL"], "https://yesabregistry.ca/projects/project-id"
        )
        self.assertEqual(normalized["title"], "Guided Backpacking")
        self.assertEqual(normalized["proponentName"], "Alaska Mountain Guides")
        self.assertEqual(
            normalized["locations"], [{"latitude": 61.3496, "longitude": -138.988}]
        )
        self.assertEqual(
            normalized["stage"],
            {
                "stageId": "stage-id",
                "id": "stage-id",
                "name": "Decision Document Issued",
                "extended": False,
            },
        )
        self.assertEqual(normalized["stageId"], "stage-id")
        self.assertEqual(
            normalized["stageHistory"],
            [
                {
                    "stageId": "stage-id",
                    "id": "stage-id",
                    "name": "Draft",
                    "stageStart": 1735685262729,
                    "stageEnd": 1735685264761,
                    "extended": False,
                }
            ],
        )
        self.assertEqual(normalized["indigenousGovernments"], ["Kluane First Nation"])
        self.assertEqual(normalized["decisionBodies"], ["Parks Canada Yukon"])
        self.assertEqual(
            normalized["planningCommissions"], ["Dawson Regional Planning Commission"]
        )
        self.assertEqual(
            normalized["ufaBoards"], [{"id": "board-id", "name": "Yukon Heritage Board"}]
        )
        self.assertEqual(
            normalized["recommendation"], {"id": "rec-id", "name": "Proceed"}
        )
        self.assertEqual(normalized["decision"], {"id": "decision-id", "name": "Vary"})
        self.assertEqual(
            normalized["outcomes"],
            {
                "outcomeName": "Proceed",
                "decisionName": "Vary",
            },
        )

    def test_bucket_change_report_counts_new_changed_and_removed_records(self) -> None:
        previous = [
            {
                "projectId": "same-id",
                "projectNumber": "2026-0001",
                "title": "Unchanged",
            },
            {
                "projectId": "changed-id",
                "projectNumber": "2026-0002",
                "title": "Old title",
            },
            {
                "projectId": "removed-id",
                "projectNumber": "2026-0003",
                "title": "Removed",
            },
        ]
        current = [
            {
                "projectId": "same-id",
                "projectNumber": "2026-0001",
                "title": "Unchanged",
            },
            {
                "projectId": "changed-id",
                "projectNumber": "2026-0002",
                "title": "New title",
            },
            {
                "projectId": "new-id",
                "projectNumber": "2026-0004",
                "title": "New",
            },
        ]

        report = refresh_api_cache.build_bucket_change_report(
            "2026-2026",
            previous,
            current,
        )

        self.assertEqual(report["status"], "changed")
        self.assertEqual(report["new_count"], 1)
        self.assertEqual(report["changed_count"], 1)
        self.assertEqual(report["removed_count"], 1)
        self.assertEqual(report["record_count_delta"], 0)
        self.assertEqual(report["new_records"], ["2026-0004"])
        self.assertEqual(report["changed_records"], ["2026-0002"])
        self.assertEqual(report["removed_records"], ["2026-0003"])

    def test_format_bucket_change_report_summarizes_unchanged_data(self) -> None:
        report = refresh_api_cache.build_bucket_change_report(
            "2026-2026",
            [{"projectId": "same-id", "projectNumber": "2026-0001"}],
            [{"projectId": "same-id", "projectNumber": "2026-0001"}],
        )

        self.assertEqual(
            refresh_api_cache.format_bucket_change_report(report),
            "2026-2026: no record changes (1 records, delta +0)",
        )


if __name__ == "__main__":
    unittest.main()
