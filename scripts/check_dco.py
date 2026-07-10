"""Verify Developer Certificate of Origin sign-offs on pull-request commits."""

import os
import re
import subprocess

from appwright.models.base import StrictModel

RECORD_SEPARATOR = "\x1e"
FIELD_SEPARATOR = "\x1f"
SIGN_OFF = re.compile(r"^Signed-off-by:\s*(.+?)\s+<([^>]+)>\s*$", re.IGNORECASE | re.MULTILINE)


class CommitRecord(StrictModel):
    revision: str
    author_name: str
    author_email: str
    message: str


def commit_records(log_output: str) -> tuple[CommitRecord, ...]:
    records: list[CommitRecord] = []
    for encoded_record in log_output.split(RECORD_SEPARATOR):
        selected_record = encoded_record.strip()
        if not selected_record:
            continue
        fields = selected_record.split(FIELD_SEPARATOR, 3)
        if len(fields) != 4:
            raise ValueError("git log returned an invalid DCO record")
        records.append(
            CommitRecord(
                revision=fields[0],
                author_name=fields[1],
                author_email=fields[2],
                message=fields[3],
            )
        )
    return tuple(records)


def unsigned_commits(records: tuple[CommitRecord, ...]) -> tuple[CommitRecord, ...]:
    unsigned: list[CommitRecord] = []
    for record in records:
        signoffs = tuple(SIGN_OFF.findall(record.message))
        author_signed = any(
            name.strip() == record.author_name
            and email.strip().casefold() == record.author_email.casefold()
            for name, email in signoffs
        )
        if not author_signed:
            unsigned.append(record)
    return tuple(unsigned)


def main() -> int:
    base_revision = os.environ.get("APPWRIGHT_DCO_BASE")
    if base_revision is None:
        print("APPWRIGHT_DCO_BASE is not set; skipping pull-request DCO verification")
        return 0
    result = subprocess.run(
        (
            "git",
            "log",
            f"{base_revision}..HEAD",
            f"--format=%H{FIELD_SEPARATOR}%an{FIELD_SEPARATOR}%ae{FIELD_SEPARATOR}%B{RECORD_SEPARATOR}",
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    unsigned = unsigned_commits(commit_records(result.stdout))
    if not unsigned:
        print("All pull-request commits contain matching DCO sign-offs")
        return 0
    print("The following commits need a matching Signed-off-by line:")
    for record in unsigned:
        print(f"- {record.revision[:12]} {record.author_name} <{record.author_email}>")
    print("Amend commits with: git commit --amend --signoff")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
