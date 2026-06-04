from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import download_project_bundle as bundler


class FakeRegistryClient:
    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.requested_json: list[str] = []
        self.requested_uploads: list[str] = []

    def get_json(self, path: str, *, optional: bool = False) -> object:
        self.requested_json.append(path)
        if path in self.payloads:
            return self.payloads[path]
        if optional:
            return []
        raise AssertionError(f"Unexpected required path: {path}")

    def download_upload(self, upload_id: str) -> tuple[bytes, dict[str, str]]:
        self.requested_uploads.append(upload_id)
        return (
            f"downloaded:{upload_id}".encode("ascii"),
            {
                "content_type": "application/octet-stream",
                "content_disposition": f'attachment; filename="{upload_id}.bin"',
            },
        )


class ProjectBundleTests(unittest.TestCase):
    def test_project_id_from_ref_accepts_registry_url_or_raw_id(self) -> None:
        project_id = "00ba642c-2cef-4a75-8412-6afa6ab76487"

        self.assertEqual(
            bundler.project_id_from_ref(
                f"https://yesabregistry.ca/projects/{project_id}/comments"
            ),
            project_id,
        )
        self.assertEqual(bundler.project_id_from_ref(project_id), project_id)

    def test_collect_upload_refs_prefers_public_redacted_upload_ids(self) -> None:
        refs = bundler.collect_upload_refs(
            {
                "documents": [
                    {
                        "documentId": "doc-1",
                        "documentNumber": "2025-0001-0001",
                        "redactedUploadId": "public-upload",
                        "unredactedUploadId": "private-upload",
                        "fileName": "Résumé β.pdf",
                    },
                    {
                        "documentId": "doc-2",
                        "redactedUploadId": "public-upload",
                    },
                    {
                        "commentId": "comment-1",
                        "documents": [
                            {
                                "documentId": "doc-3",
                                "redactedUploadId": "comment-upload",
                            }
                        ],
                    },
                ]
            }
        )

        self.assertEqual([ref["uploadId"] for ref in refs], ["public-upload", "comment-upload"])
        self.assertNotIn("private-upload", [ref["uploadId"] for ref in refs])
        self.assertEqual(refs[0]["documentId"], "doc-1")
        self.assertEqual(refs[0]["documentNumber"], "2025-0001-0001")

    def test_collect_upload_refs_can_include_unredacted_upload_ids(self) -> None:
        refs = bundler.collect_upload_refs(
            {
                "documentId": "doc-1",
                "redactedUploadId": "public-upload",
                "unredactedUploadId": "private-upload",
            },
            public_only=False,
        )

        self.assertEqual(
            [(ref["uploadIdKey"], ref["uploadId"]) for ref in refs],
            [
                ("redactedUploadId", "public-upload"),
                ("unredactedUploadId", "private-upload"),
            ],
        )

    def test_write_project_bundle_fetches_sections_and_downloads_attachments(self) -> None:
        project_id = "project-1"
        payloads: dict[str, object] = {
            f"/api/projects/{project_id}": {
                "project": {
                    "projectId": project_id,
                    "projectNumber": "2025-0001",
                    "title": "Example Project",
                }
            },
            f"/api/projects/{project_id}/meta": {"commentCount": 1},
            f"/api/projects/{project_id}/documents": [
                {
                    "documentId": "doc-1",
                    "documentNumber": "2025-0001-0001",
                    "redactedUploadId": "upload-1",
                    "fileName": "Résumé β.pdf",
                }
            ],
            f"/api/projects/{project_id}/comments": [{"commentId": "comment-1"}],
            f"/api/projects/{project_id}/comments/comment-1/documents": [
                {
                    "documentId": "comment-doc",
                    "redactedUploadId": "upload-2",
                    "fileName": "comment.pdf",
                }
            ],
            f"/api/projects/{project_id}/emails": [
                {
                    "emailTypeGroups": [
                        {
                            "emailRecipientGroups": [
                                {
                                    "emails": [
                                        {"emailMessageId": "email-1"},
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ],
            f"/api/projects/{project_id}/emails/email-1": {
                "subject": "Notification",
                "documents": [
                    {
                        "documentId": "email-doc",
                        "redactedUploadId": "upload-3",
                        "fileName": "notice.pdf",
                    }
                ],
            },
        }
        client = FakeRegistryClient(payloads)

        with tempfile.TemporaryDirectory() as tmp:
            manifest = bundler.write_project_bundle(project_id, Path(tmp), client=client)
            manifest_path = Path(tmp) / "manifest.json"

            self.assertTrue(manifest_path.exists())
            self.assertTrue((Path(tmp) / "json" / "details.json").exists())
            self.assertTrue((Path(tmp) / "json" / "comments" / "comment-1_documents.json").exists())
            self.assertTrue((Path(tmp) / "json" / "emails" / "email-1.json").exists())
            self.assertEqual(client.requested_uploads, ["upload-1", "upload-2", "upload-3"])
            self.assertEqual(manifest["attachmentCount"], 3)

            saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            saved_paths = [Path(item["path"]).name for item in saved_manifest["attachments"]]
            self.assertEqual(len(saved_paths), 3)
            self.assertTrue(all(name.encode("ascii") for name in saved_paths))
            self.assertTrue(any("Resume" in name for name in saved_paths))


if __name__ == "__main__":
    unittest.main()
