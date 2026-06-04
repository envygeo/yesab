# YESAB project bundle download heuristics

This note documents local artifact choices made by
`scripts/download_project_bundle.py`. The Registry API remains the source of
truth; these rules only describe how the downloader mirrors public Registry
content into stable local files.

Last updated: 2025-06-04
Changes to code after this date may not be reflected here; always check code for source of truth.

## Attachment timestamp heuristic

Downloaded attachment files have their filesystem modified time set from the
first valid Registry date field found in this order:

1. `receivedDate`
2. `uploadDate`
3. `redactedUploadDate`
4. `submittedDate`
5. `dateSent`
6. `sentDate`

The fields are expected to be Unix epoch timestamps in milliseconds. Missing,
blank, non-numeric, zero, and pre-1970-sentinel values are ignored. If no valid
timestamp is available, the file keeps the normal download-time modified time.

The manifest records the selected field as `timestampField`, the selected epoch
milliseconds as `timestampEpochMs`, the UTC rendering as `timestampIso`, and
whether `os.utime()` was applied as `timestampApplied`.

Rationale:

- `receivedDate` is closest to an as-received date when present.
- `uploadDate` is the next-best general attachment date.
- comment attachment records in current Registry responses often have
  `uploadDate: 0` and a useful `redactedUploadDate`, so zero-like sentinels are
  skipped before falling through.
- `submittedDate`, `dateSent`, and `sentDate` are fallback contextual dates for
  comments or notifications when attachment-level dates are absent.

## Attachment filename heuristic

All downloaded public attachments are kept together under `attachments/` rather
than split into website-tab folders. This makes the bundle easier to browse and
sort while the manifest preserves the source JSON path and section context.

Filename prefix rules:

1. Use the Registry `documentNumber` when present.
2. If the `documentNumber` is only a four-digit sequence such as `0049` and the
   project number is known, expand it to `<projectNumber>-<documentNumber>`, for
   example `2025-0069-0049`.
3. If no document number exists, fall back to `documentId`, then `uploadId`.
4. For comment attachments, append `_cmt` to the prefix, for example
   `2025-0069-0049_cmt_...`.
5. Append the visible Registry filename from `fileName`, `redactedFileName`, or
   the download response filename.
6. Convert the local filename to ASCII-safe characters and add a numeric suffix
   only when needed to avoid collisions.

The `_cmt` marker distinguishes public-comment attachments from main Documents
tab attachments while preserving one flat attachment folder. Email/notification
payloads currently appear to be outgoing YESAB messages, so the downloader does
not add a special email marker.
