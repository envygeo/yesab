from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts import build_datasette_explorer as explorer


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def sample_projects() -> list[dict[str, object]]:
    return [
        {
            "_cache": {
                "bucket": "2025-2025",
                "bucketCachedAt": "2026-06-08T17:02:26Z",
            },
            "assessmentDistricts": [
                {"assessmentDistrictId": "district-1", "name": "Mayo"}
            ],
            "decisionBodies": [{"decisionBodyId": "body-1", "name": "YG"}],
            "indigenousGovernments": ["First Nation of Na-Cho Nyak Dun"],
            "locations": [{"latitude": 64.12345, "longitude": -135.12345}],
            "outcomes": {
                "decisionName": "Decision Document",
                "outcomeName": "Proceed with terms",
            },
            "planningCommissions": [],
            "projectId": "project-1",
            "projectNumber": "2025-0069",
            "projectScope": {
                "activities": "drilling",
                "summary": "Quartz exploration drilling near Mayo.",
            },
            "projectTypeName": "Evaluation",
            "proponentName": "Florin Resources",
            "sectors": [
                {
                    "sectorId": "sector-1",
                    "name": "Mining - Quartz",
                }
            ],
            "stage": {
                "daysRemaining": 12,
                "extended": False,
                "name": "Screening",
                "stageId": "stage-1",
            },
            "stageHistory": [{"name": "Submitted", "date": "2025-04-15"}],
            "title": "Quartz Exploration - Florin Gold Project",
        },
        {
            "assessmentDistricts": [],
            "decisionBodies": [],
            "indigenousGovernments": [],
            "locations": [],
            "planningCommissions": [],
            "projectId": "project-2",
            "projectNumber": "2024-0001",
            "projectScope": {"summary": "A road maintenance project."},
            "projectTypeName": "Designated Office Evaluation",
            "proponentName": "Road Builder",
            "sectors": [{"sectorId": "sector-2", "name": "Transportation"}],
            "stage": {"name": "Decision Document Issued"},
            "title": "Access road upgrade",
        },
    ]


def sample_map_payload() -> dict[str, object]:
    return {
        "apiSummary": {"projectCount": 2, "mappedProjectCount": 1},
        "layers": [
            {
                "archive": "yesab_all.zip",
                "name": "Projects_Points",
                "type": "Point",
                "features": [
                    {
                        "apiProjectNumber": "2025-0069",
                        "bbox": [492000.0, 1054000.0, 492000.0, 1054000.0],
                        "geometry": {
                            "coordinates": [492000.0, 1054000.0],
                            "type": "Point",
                        },
                        "id": 1,
                        "label": "Florin",
                        "properties": {"ProjectID": "2025-0069"},
                    }
                ],
            }
        ],
        "qa": {"summary": {"mappedApiProjectCount": 1}},
    }


class DatasetteExplorerTests(unittest.TestCase):
    def test_write_explorer_db_builds_queryable_project_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "yesab-explorer.db"
            bundle_root = Path(tmp) / "project-bundles"
            bundle_dir = bundle_root / "2025-0069"
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "attachmentCount": 1,
                        "attachments": [
                            {
                                "bytes": 123,
                                "contentType": "application/pdf",
                                "description": "Project proposal",
                                "documentNumber": "2025-0069-0001",
                                "documentType": "Project Document",
                                "downloaded": True,
                                "fileName": "proposal.pdf",
                                "path": "attachments\\2025-0069-0001_proposal.pdf",
                                "sourceKind": "documents",
                                "timestampIso": "2025-04-15T18:38:40Z",
                                "uploadId": "upload-1",
                            }
                        ],
                        "downloadedAttachmentCount": 1,
                        "errors": [],
                        "generatedAt": "2026-06-04T22:48:04Z",
                        "projectId": "project-1",
                        "projectNumber": "2025-0069",
                        "projectRef": "2025-0069",
                        "sectionCount": 1,
                        "sections": [
                            {
                                "count": 20,
                                "endpoint": "/api/projects/project-1/documents",
                                "name": "documents",
                                "path": "json\\documents.json",
                            }
                        ],
                        "sourceBaseUrl": "https://yesabregistry.ca",
                        "title": "Quartz Exploration - Florin Gold Project",
                    }
                ),
                encoding="utf-8",
            )

            counts = explorer.write_explorer_db(
                db_path,
                sample_projects(),
                map_payload=sample_map_payload(),
                bundle_root=bundle_root,
            )

            self.assertEqual(counts["projects"], 2)
            self.assertEqual(counts["map_features"], 1)
            self.assertEqual(counts["project_bundles"], 1)
            with closing(sqlite3.connect(db_path)) as db:
                db.row_factory = sqlite3.Row
                project = db.execute(
                    """
                    SELECT project_year, title, first_latitude, districts, sectors
                      FROM projects
                     WHERE project_number = '2025-0069'
                    """
                ).fetchone()
                self.assertEqual(project["project_year"], 2025)
                self.assertEqual(
                    project["title"], "Quartz Exploration - Florin Gold Project"
                )
                self.assertAlmostEqual(project["first_latitude"], 64.12345)
                self.assertEqual(project["districts"], "Mayo")
                self.assertEqual(project["sectors"], "Mining - Quartz")

                sector_names = [
                    row["sector_name"]
                    for row in db.execute(
                        "SELECT sector_name FROM project_sectors ORDER BY sector_name"
                    )
                ]
                self.assertEqual(
                    sector_names, ["Mining - Quartz", "Transportation"]
                )

                fts_hit = db.execute(
                    """
                    SELECT p.project_number
                      FROM projects_fts f
                      JOIN projects p ON p.id = f.rowid
                     WHERE projects_fts MATCH 'quartz'
                    """
                ).fetchone()
                self.assertEqual(fts_hit["project_number"], "2025-0069")

                map_feature = db.execute(
                    "SELECT layer_name, geometry_type FROM map_features"
                ).fetchone()
                self.assertEqual(map_feature["layer_name"], "Projects_Points")
                self.assertEqual(map_feature["geometry_type"], "Point")

                attachment = db.execute(
                    """
                    SELECT description, local_path
                      FROM bundle_attachments
                     WHERE project_number = '2025-0069'
                    """
                ).fetchone()
                self.assertEqual(attachment["description"], "Project proposal")
                self.assertIn("2025-0069-0001_proposal.pdf", attachment["local_path"])

                metadata = explorer.datasette_metadata("yesab")
                for query in metadata["databases"]["yesab"]["queries"].values():
                    db.execute(query["sql"]).fetchone()

    def test_imports_bundle_section_json_into_normalized_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "yesab-explorer.db"
            bundle_root = Path(tmp) / "project-bundles"
            bundle_dir = bundle_root / "2025-0069"

            write_json(
                bundle_dir / "json" / "documents.json",
                [
                    {
                        "description": "Project proposal",
                        "documentId": "doc-1",
                        "documentNumber": "2025-0069-0001",
                        "documentState": "Public",
                        "documentType": "Project Document",
                        "documentTypeId": "type-1",
                        "fileName": "proposal.pdf",
                        "keyDocument": True,
                        "redactedUploadId": "upload-doc",
                        "uploadDate": 1744742320656,
                    }
                ],
            )
            write_json(bundle_dir / "json" / "key_documents.json", [])
            write_json(
                bundle_dir / "json" / "comments.json",
                [
                    {
                        "commentId": "comment-1",
                        "documentNumber": "2025-0069-0042",
                        "firstName": "Jane",
                        "lastName": "Reader",
                        "redactedComment": "Please consider caribou habitat.",
                        "submittedDate": 1760131636448,
                    }
                ],
            )
            write_json(
                bundle_dir / "json" / "comments" / "comment-1_documents.json",
                [
                    {
                        "description": "Comment attachment",
                        "documentId": "comment-doc-1",
                        "documentNumber": "0042",
                        "documentType": "Comment Document",
                        "fileName": "comment.pdf",
                        "redactedUploadId": "upload-comment",
                    }
                ],
            )
            write_json(
                bundle_dir / "json" / "activity_feed.json",
                [
                    {
                        "activityDate": 1756848291846,
                        "documents": [{"documentId": "doc-1"}],
                        "message": [
                            {"message": "YESAB published ", "messagePartType": "Text"},
                            {
                                "linkTo": "doc-1",
                                "message": "Project proposal",
                                "messagePartType": "Document Link",
                            },
                        ],
                        "secondarySort": 3,
                    }
                ],
            )
            write_json(
                bundle_dir / "json" / "notes.json",
                [
                    {
                        "documentNumber": "2025-0069-0018",
                        "note": "<p>See attached.</p>",
                        "noteId": "note-1",
                        "noteState": "Public",
                        "publishedDate": 1745452782048,
                        "stageUploaded": "Proposal Submission",
                        "title": "Lead assessment note",
                    }
                ],
            )
            write_json(
                bundle_dir / "json" / "simplified_information_requests.json",
                [
                    {
                        "answeredDate": 1754330926252,
                        "documentNumberRequest": "2025-0069-0022",
                        "documentNumberResponse": "2025-0069-0024",
                        "number": 1,
                        "projectStage": "Adequacy",
                        "publishedDate": 1753111066046,
                        "simplifiedInformationRequestId": "sir-1",
                        "status": "Answered",
                    }
                ],
            )
            write_json(
                bundle_dir / "json" / "emails.json",
                [
                    {
                        "emailDate": "2025-09-02",
                        "emailTypeGroups": [
                            {
                                "emailRecipientGroups": [
                                    {
                                        "emails": [
                                            {
                                                "content": "<p>Public comment open.</p>",
                                                "emailMessageId": "email-1",
                                                "emailMessageType": "Notification",
                                                "emailRecipientType": "Followers",
                                                "sentDate": 1756849838426,
                                                "stageUploaded": "Evaluation",
                                                "subject": "Public comment period open",
                                            }
                                        ]
                                    }
                                ]
                            }
                        ],
                    }
                ],
            )
            write_json(
                bundle_dir / "manifest.json",
                {
                    "attachmentCount": 2,
                    "attachments": [
                        {
                            "documentId": "doc-1",
                            "documentNumber": "2025-0069-0001",
                            "downloaded": True,
                            "path": "attachments/proposal.pdf",
                            "sourceKind": "documents",
                            "uploadId": "upload-doc",
                        },
                        {
                            "documentId": "comment-doc-1",
                            "documentNumber": "0042",
                            "downloaded": True,
                            "path": "attachments/comment.pdf",
                            "sourceKind": "comments",
                            "sourcePath": "$.comments/comment-1_documents[0]",
                            "uploadId": "upload-comment",
                        },
                    ],
                    "downloadedAttachmentCount": 2,
                    "errors": [],
                    "generatedAt": "2026-06-04T22:48:04Z",
                    "projectId": "project-1",
                    "projectNumber": "2025-0069",
                    "projectRef": "2025-0069",
                    "sectionCount": 8,
                    "sections": [
                        {
                            "count": 1,
                            "endpoint": "/api/projects/project-1/documents",
                            "name": "documents",
                            "path": "json/documents.json",
                        },
                        {
                            "count": 0,
                            "endpoint": "/api/projects/project-1/key-documents",
                            "name": "key_documents",
                            "path": "json/key_documents.json",
                        },
                        {
                            "count": 1,
                            "endpoint": "/api/projects/project-1/comments",
                            "name": "comments",
                            "path": "json/comments.json",
                        },
                        {
                            "count": 1,
                            "endpoint": "/api/projects/project-1/comments/comment-1/documents",
                            "name": "comments/comment-1_documents",
                            "path": "json/comments/comment-1_documents.json",
                        },
                        {
                            "count": 1,
                            "endpoint": "/api/projects/project-1/activity-feed",
                            "name": "activity_feed",
                            "path": "json/activity_feed.json",
                        },
                        {
                            "count": 1,
                            "endpoint": "/api/projects/project-1/notes",
                            "name": "notes",
                            "path": "json/notes.json",
                        },
                        {
                            "count": 1,
                            "endpoint": "/api/projects/project-1/simplified-information-requests",
                            "name": "simplified_information_requests",
                            "path": "json/simplified_information_requests.json",
                        },
                        {
                            "count": 1,
                            "endpoint": "/api/projects/project-1/emails",
                            "name": "emails",
                            "path": "json/emails.json",
                        },
                    ],
                    "sourceBaseUrl": "https://yesabregistry.ca",
                    "title": "Quartz Exploration - Florin Gold Project",
                },
            )

            counts = explorer.write_explorer_db(
                db_path,
                sample_projects(),
                map_payload=sample_map_payload(),
                bundle_root=bundle_root,
            )

            self.assertEqual(counts["bundle_documents"], 2)
            self.assertEqual(counts["bundle_comments"], 1)
            self.assertEqual(counts["bundle_activity_events"], 1)
            self.assertEqual(counts["bundle_notes"], 1)
            self.assertEqual(counts["bundle_simplified_information_requests"], 1)
            self.assertEqual(counts["bundle_emails"], 1)

            with closing(sqlite3.connect(db_path)) as db:
                db.row_factory = sqlite3.Row

                document = db.execute(
                    """
                    SELECT document_number, upload_id, source_section, source_json_path
                      FROM bundle_documents
                     WHERE document_id = 'doc-1'
                    """
                ).fetchone()
                self.assertEqual(document["document_number"], "2025-0069-0001")
                self.assertEqual(document["upload_id"], "upload-doc")
                self.assertEqual(document["source_section"], "documents")
                self.assertEqual(document["source_json_path"], "json/documents.json")

                comment_document = db.execute(
                    """
                    SELECT parent_kind, parent_id, upload_id
                      FROM bundle_documents
                     WHERE document_id = 'comment-doc-1'
                    """
                ).fetchone()
                self.assertEqual(comment_document["parent_kind"], "comment")
                self.assertEqual(comment_document["parent_id"], "comment-1")
                self.assertEqual(comment_document["upload_id"], "upload-comment")

                comment = db.execute(
                    """
                    SELECT submitter_name, document_count, upload_ids
                      FROM bundle_comments
                     WHERE comment_id = 'comment-1'
                    """
                ).fetchone()
                self.assertEqual(comment["submitter_name"], "Jane Reader")
                self.assertEqual(comment["document_count"], 1)
                self.assertEqual(comment["upload_ids"], "upload-comment")

                activity = db.execute(
                    """
                    SELECT message_text, linked_document_ids
                      FROM bundle_activity_events
                    """
                ).fetchone()
                self.assertIn("YESAB published Project proposal", activity["message_text"])
                self.assertEqual(activity["linked_document_ids"], "doc-1")

                note = db.execute("SELECT title FROM bundle_notes").fetchone()
                self.assertEqual(note["title"], "Lead assessment note")

                request = db.execute(
                    """
                    SELECT request_number, request_document_number,
                           response_document_number, status
                      FROM bundle_simplified_information_requests
                    """
                ).fetchone()
                self.assertEqual(request["request_number"], 1)
                self.assertEqual(request["request_document_number"], "2025-0069-0022")
                self.assertEqual(request["response_document_number"], "2025-0069-0024")
                self.assertEqual(request["status"], "Answered")

                email = db.execute(
                    "SELECT subject, message_type, recipient_type FROM bundle_emails"
                ).fetchone()
                self.assertEqual(email["subject"], "Public comment period open")
                self.assertEqual(email["message_type"], "Notification")
                self.assertEqual(email["recipient_type"], "Followers")

    def test_write_datasette_metadata_includes_facets_and_canned_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metadata_path = Path(tmp) / "metadata.json"

            explorer.write_datasette_metadata(metadata_path, database_name="yesab")

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            projects_table = metadata["databases"]["yesab"]["tables"]["projects"]
            self.assertIn("project_year", projects_table["facets"])
            self.assertIn("stage_name", projects_table["facets"])
            self.assertEqual(
                projects_table["plugins"]["datasette-cluster-map"]["latitude_column"],
                "first_latitude",
            )
            queries = metadata["databases"]["yesab"]["queries"]
            self.assertIn("active_projects", queries)
            self.assertIn("projects_by_sector", queries)
            self.assertIn("downloaded_bundle_documents", queries)


if __name__ == "__main__":
    unittest.main()
