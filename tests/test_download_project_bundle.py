from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from scripts import download_project_bundle as bundler


class FakeRegistryClient:
    def __init__(
        self,
        payloads: dict[str, object],
        *,
        upload_bodies: dict[str, bytes] | None = None,
        upload_failures: dict[str, list[Exception]] | None = None,
    ) -> None:
        self.payloads = payloads
        self.upload_bodies = upload_bodies or {}
        self.upload_failures = upload_failures or {}
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
        failures = self.upload_failures.get(upload_id, [])
        if failures:
            raise failures.pop(0)
        return (
            self.upload_bodies.get(
                upload_id,
                f"downloaded:{upload_id}".encode("ascii"),
            ),
            {
                "content_type": "application/octet-stream",
                "content_disposition": f'attachment; filename="{upload_id}.bin"',
            },
        )


class ProjectBundleTests(unittest.TestCase):
    def minimal_dbf(self, records: list[dict[str, str]]) -> bytes:
        fields = [("Name", 10)]
        if any("ProjectID" in record for record in records):
            fields.append(("ProjectID", 10))
        today = datetime.now(UTC)
        header_len = 32 + (32 * len(fields)) + 1
        record_len = 1 + sum(length for _, length in fields)
        header = bytearray(32)
        header[0] = 0x03
        header[1] = today.year - 1900
        header[2] = today.month
        header[3] = today.day
        header[4:8] = len(records).to_bytes(4, "little")
        header[8:10] = header_len.to_bytes(2, "little")
        header[10:12] = record_len.to_bytes(2, "little")
        body = bytearray(header)
        for name, length in fields:
            descriptor = bytearray(32)
            descriptor[:11] = name.encode("ascii")[:11].ljust(11, b"\x00")
            descriptor[11] = ord("C")
            descriptor[16] = length
            body.extend(descriptor)
        body.extend(b"\r")
        for record in records:
            body.extend(b" ")
            for name, length in fields:
                body.extend(record.get(name, "").encode("ascii")[:length].ljust(length))
        body.extend(b"\x1a")
        return bytes(body)

    def dbf_fields_and_rows(self, path: Path) -> tuple[list[str], list[dict[str, str]]]:
        data = path.read_bytes()
        record_count = int.from_bytes(data[4:8], "little")
        header_len = int.from_bytes(data[8:10], "little")
        record_len = int.from_bytes(data[10:12], "little")
        fields: list[tuple[str, int]] = []
        offset = 32
        while offset < header_len - 1 and data[offset] != 0x0D:
            descriptor = data[offset : offset + 32]
            name = descriptor[:11].split(b"\x00", 1)[0].decode("ascii")
            fields.append((name, descriptor[16]))
            offset += 32
        rows: list[dict[str, str]] = []
        for index in range(record_count):
            record = data[header_len + (index * record_len) : header_len + ((index + 1) * record_len)]
            position = 1
            row: dict[str, str] = {}
            for name, length in fields:
                row[name] = record[position : position + length].decode("ascii").strip()
                position += length
            rows.append(row)
        return [name for name, _ in fields], rows

    def test_project_id_from_ref_accepts_registry_url_or_raw_id(self) -> None:
        project_id = "00ba642c-2cef-4a75-8412-6afa6ab76487"

        self.assertEqual(
            bundler.project_id_from_ref(
                f"https://yesabregistry.ca/projects/{project_id}/comments"
            ),
            project_id,
        )
        self.assertEqual(bundler.project_id_from_ref(project_id), project_id)

    def test_safe_filename_cleans_duplicate_and_trailing_underscores(self) -> None:
        self.assertEqual(
            bundler.safe_filename("Export__SHP_-_Trail_.zip", fallback="fallback"),
            "Export_SHP_-_Trail.zip",
        )
        self.assertEqual(bundler.safe_filename("CON_.txt", fallback="fallback"), "CON_file.txt")

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
                    "uploadDate": 1744742320656,
                }
            ],
            f"/api/projects/{project_id}/comments": [{"commentId": "comment-1"}],
            f"/api/projects/{project_id}/comments/comment-1/documents": [
                {
                    "documentId": "comment-doc",
                    "documentNumber": "0042",
                    "redactedUploadId": "upload-2",
                    "fileName": "comment.pdf",
                    "uploadDate": 0,
                    "redactedUploadDate": 1760131642463,
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
            attachments = saved_manifest["attachments"]
            saved_paths = [Path(item["path"]).name for item in attachments]
            self.assertEqual(len(saved_paths), 3)
            self.assertTrue(all(name.encode("ascii") for name in saved_paths))
            self.assertTrue(any("Resume" in name for name in saved_paths))
            self.assertTrue(any(name.startswith("2025-0001-0042_cmt_") for name in saved_paths))

            comment_attachment = next(
                item for item in attachments if item["uploadId"] == "upload-2"
            )
            comment_path = Path(tmp) / comment_attachment["path"]
            self.assertEqual(comment_attachment["timestampField"], "redactedUploadDate")
            self.assertEqual(comment_attachment["timestampEpochMs"], 1760131642463)
            self.assertAlmostEqual(
                os.path.getmtime(comment_path),
                1760131642463 / 1000,
                delta=2,
            )

    def test_write_project_bundle_reuses_existing_manifest_attachment(self) -> None:
        project_id = "project-1"
        payloads: dict[str, object] = {
            f"/api/projects/{project_id}": {
                "project": {
                    "projectId": project_id,
                    "projectNumber": "2025-0001",
                    "title": "Example Project",
                }
            },
            f"/api/projects/{project_id}/documents": [
                {
                    "documentId": "doc-1",
                    "documentNumber": "2025-0001-0001",
                    "redactedUploadId": "upload-1",
                    "fileName": "example.pdf",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            first_client = FakeRegistryClient(payloads)
            first_manifest = bundler.write_project_bundle(
                project_id,
                output_dir,
                client=first_client,
                download_delay_seconds=0,
            )
            first_attachment = first_manifest["attachments"][0]
            first_path = output_dir / first_attachment["path"]

            second_client = FakeRegistryClient(
                payloads,
                upload_bodies={"upload-1": b"new bytes that should not be fetched"},
            )
            second_manifest = bundler.write_project_bundle(
                project_id,
                output_dir,
                client=second_client,
                download_delay_seconds=0,
            )

            self.assertEqual(second_client.requested_uploads, [])
            second_attachment = second_manifest["attachments"][0]
            self.assertEqual(second_attachment["path"], first_attachment["path"])
            self.assertEqual(second_attachment["bytes"], first_attachment["bytes"])
            self.assertTrue(second_attachment["downloaded"])
            self.assertTrue(second_attachment["reused"])
            self.assertEqual(first_path.read_bytes(), b"downloaded:upload-1")

    def test_force_redownloads_existing_attachment_in_place(self) -> None:
        project_id = "project-1"
        payloads: dict[str, object] = {
            f"/api/projects/{project_id}": {
                "project": {
                    "projectId": project_id,
                    "projectNumber": "2025-0001",
                    "title": "Example Project",
                }
            },
            f"/api/projects/{project_id}/documents": [
                {
                    "documentId": "doc-1",
                    "documentNumber": "2025-0001-0001",
                    "redactedUploadId": "upload-1",
                    "fileName": "example.pdf",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            first_client = FakeRegistryClient(
                payloads,
                upload_bodies={"upload-1": b"old bytes"},
            )
            first_manifest = bundler.write_project_bundle(
                project_id,
                output_dir,
                client=first_client,
                download_delay_seconds=0,
            )
            first_attachment = first_manifest["attachments"][0]

            second_client = FakeRegistryClient(
                payloads,
                upload_bodies={"upload-1": b"new bytes"},
            )
            second_manifest = bundler.write_project_bundle(
                project_id,
                output_dir,
                client=second_client,
                download_delay_seconds=0,
                force_downloads=True,
            )

            self.assertEqual(second_client.requested_uploads, ["upload-1"])
            second_attachment = second_manifest["attachments"][0]
            self.assertEqual(second_attachment["path"], first_attachment["path"])
            self.assertEqual(second_attachment["bytes"], len(b"new bytes"))
            self.assertFalse(second_attachment["reused"])
            self.assertEqual((output_dir / second_attachment["path"]).read_bytes(), b"new bytes")

    def test_size_mismatch_redownloads_existing_manifest_attachment(self) -> None:
        project_id = "project-1"
        payloads: dict[str, object] = {
            f"/api/projects/{project_id}": {
                "project": {
                    "projectId": project_id,
                    "projectNumber": "2025-0001",
                    "title": "Example Project",
                }
            },
            f"/api/projects/{project_id}/documents": [
                {
                    "documentId": "doc-1",
                    "documentNumber": "2025-0001-0001",
                    "redactedUploadId": "upload-1",
                    "fileName": "example.pdf",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            first_client = FakeRegistryClient(
                payloads,
                upload_bodies={"upload-1": b"complete bytes"},
            )
            first_manifest = bundler.write_project_bundle(
                project_id,
                output_dir,
                client=first_client,
                download_delay_seconds=0,
            )
            first_attachment = first_manifest["attachments"][0]
            first_path = output_dir / first_attachment["path"]
            first_path.write_bytes(b"short")

            second_client = FakeRegistryClient(
                payloads,
                upload_bodies={"upload-1": b"complete bytes again"},
            )
            second_manifest = bundler.write_project_bundle(
                project_id,
                output_dir,
                client=second_client,
                download_delay_seconds=0,
            )

            self.assertEqual(second_client.requested_uploads, ["upload-1"])
            second_attachment = second_manifest["attachments"][0]
            self.assertEqual(second_attachment["path"], first_attachment["path"])
            self.assertFalse(second_attachment["reused"])
            self.assertEqual(first_path.read_bytes(), b"complete bytes again")

    def test_download_attachments_retries_and_cleans_part_file(self) -> None:
        client = FakeRegistryClient(
            {},
            upload_bodies={"upload-1": b"ok"},
            upload_failures={
                "upload-1": [bundler.BundleError("temporary failure")],
            },
        )
        sleeps: list[float] = []

        with tempfile.TemporaryDirectory() as tmp:
            attachments, errors = bundler.download_attachments(
                client=client,
                output_dir=Path(tmp),
                upload_refs=[
                    {
                        "uploadId": "upload-1",
                        "uploadIdKey": "redactedUploadId",
                        "sourcePath": "$.documents[0]",
                        "sourceKind": "documents",
                        "documentNumber": "2025-0001-0001",
                        "originalFilename": "example.pdf",
                    }
                ],
                download_delay_seconds=0,
                retry_count=1,
                retry_backoff_seconds=2,
                sleep=sleeps.append,
            )

            self.assertEqual(errors, [])
            self.assertEqual(client.requested_uploads, ["upload-1", "upload-1"])
            self.assertEqual(sleeps, [2])
            path = Path(tmp) / attachments[0]["path"]
            self.assertEqual(path.read_bytes(), b"ok")
            self.assertEqual(list(path.parent.glob("*.part")), [])

    def test_download_attachments_paces_between_new_downloads(self) -> None:
        client = FakeRegistryClient(
            {},
            upload_bodies={
                "upload-1": b"one",
                "upload-2": b"two",
            },
        )
        sleeps: list[float] = []

        with tempfile.TemporaryDirectory() as tmp:
            bundler.download_attachments(
                client=client,
                output_dir=Path(tmp),
                upload_refs=[
                    {
                        "uploadId": "upload-1",
                        "uploadIdKey": "redactedUploadId",
                        "sourcePath": "$.documents[0]",
                        "sourceKind": "documents",
                        "documentNumber": "2025-0001-0001",
                        "originalFilename": "one.pdf",
                    },
                    {
                        "uploadId": "upload-2",
                        "uploadIdKey": "redactedUploadId",
                        "sourcePath": "$.documents[1]",
                        "sourceKind": "documents",
                        "documentNumber": "2025-0001-0002",
                        "originalFilename": "two.pdf",
                    },
                ],
                download_delay_seconds=0.5,
                sleep=sleeps.append,
            )

            self.assertEqual(client.requested_uploads, ["upload-1", "upload-2"])
            self.assertEqual(sleeps, [0.5])

    def test_zip_attachment_is_extracted_and_generic_shapefile_is_renamed(self) -> None:
        archive_name = "2025-0069-0056_Export_SHP_-_GEM_and_Sprague_Cks_Trail_.zip"
        archive_stem = "2025-0069-0056_Export_SHP_-_GEM_and_Sprague_Cks_Trail"
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / archive_name
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Polygon.shp", b"dummy shp bytes")
                archive.writestr("Polygon.dbf", self.minimal_dbf([{"Name": "first"}]))
                archive.writestr("Polygon.shx", b"dummy shx bytes")
                archive.writestr("Polygon.docx", b"document bytes")

            client = FakeRegistryClient(
                {},
                upload_bodies={"upload-1": zip_path.read_bytes()},
            )
            attachments, errors = bundler.download_attachments(
                client=client,
                output_dir=Path(tmp) / "bundle",
                project_number="2025-0069",
                upload_refs=[
                    {
                        "uploadId": "upload-1",
                        "uploadIdKey": "redactedUploadId",
                        "sourcePath": "$.documents[0]",
                        "sourceKind": "documents",
                        "documentNumber": "2025-0069-0056",
                        "originalFilename": archive_name,
                    }
                ],
                download_delay_seconds=0,
            )

            self.assertEqual(errors, [])
            self.assertEqual(
                Path(attachments[0]["path"]).name,
                "2025-0069-0056_Export_SHP_-_GEM_and_Sprague_Cks_Trail.zip",
            )
            extract_dir = Path(tmp) / "bundle" / "attachments" / archive_stem
            self.assertTrue(extract_dir.is_dir())
            self.assertTrue((extract_dir / f"{archive_stem}.shp").exists())
            self.assertTrue((extract_dir / f"{archive_stem}.shx").exists())
            self.assertTrue((extract_dir / "Polygon.docx").exists())
            self.assertFalse((extract_dir / "Polygon.shp").exists())
            fields, rows = self.dbf_fields_and_rows(extract_dir / f"{archive_stem}.dbf")
            self.assertIn("ProjectID", fields)
            self.assertEqual(rows, [{"Name": "first", "ProjectID": "2025-0069"}])
            self.assertEqual(
                attachments[0]["extractedPath"],
                f"attachments/{archive_stem}",
            )

    def test_zip_extraction_skips_unsafe_paths_and_file_parent_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bundle.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("../outside.txt", b"bad")
                archive.writestr("/absolute.txt", b"bad")
                archive.writestr("folder", b"file not directory")
                archive.writestr("folder/child.txt", b"skipped")
                archive.writestr("safe/child.txt", b"kept")

            result = bundler.extract_archive_attachment(
                archive_path=zip_path,
                output_dir=Path(tmp),
                project_id="2025-0069",
            )

            extract_dir = Path(tmp) / "bundle"
            self.assertEqual(result["extractedPath"], "bundle")
            self.assertFalse((Path(tmp) / "outside.txt").exists())
            self.assertFalse((Path(tmp) / "absolute.txt").exists())
            self.assertEqual((extract_dir / "folder").read_bytes(), b"file not directory")
            self.assertFalse((extract_dir / "folder" / "child.txt").exists())
            self.assertEqual((extract_dir / "safe" / "child.txt").read_bytes(), b"kept")

    def test_existing_project_id_dbf_field_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dbf_path = Path(tmp) / "Polygon.dbf"
            dbf_path.write_bytes(
                self.minimal_dbf([{"Name": "first", "ProjectID": "existing"}])
            )

            changed = bundler.add_project_id_to_dbf(dbf_path, "2025-0069")

            fields, rows = self.dbf_fields_and_rows(dbf_path)
            self.assertFalse(changed)
            self.assertEqual(fields, ["Name", "ProjectID"])
            self.assertEqual(rows, [{"Name": "first", "ProjectID": "existing"}])

    def test_invalid_zip_keeps_downloaded_attachment_with_extraction_error(self) -> None:
        client = FakeRegistryClient(
            {},
            upload_bodies={"upload-1": b"not a zip"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            attachments, errors = bundler.download_attachments(
                client=client,
                output_dir=output_dir,
                project_number="2025-0069",
                upload_refs=[
                    {
                        "uploadId": "upload-1",
                        "uploadIdKey": "redactedUploadId",
                        "sourcePath": "$.documents[0]",
                        "sourceKind": "documents",
                        "originalFilename": "bad.zip",
                    }
                ],
            )

            self.assertEqual(len(errors), 1)
            self.assertTrue(attachments[0]["downloaded"])
            self.assertIn("extractionError", attachments[0])
            self.assertEqual((output_dir / attachments[0]["path"]).read_bytes(), b"not a zip")


if __name__ == "__main__":
    unittest.main()
