"""DCO policy tests."""

from scripts.check_dco import CommitRecord, commit_records, unsigned_commits


def test_commit_records_and_matching_signoff() -> None:
    log_output = (
        "abc123\x1fBhargav Andhe\x1fbhargavandhe2310@gmail.com\x1f"
        "Initial change\n\nSigned-off-by: Bhargav Andhe <bhargavandhe2310@gmail.com>\n\x1e"
    )
    records = commit_records(log_output)
    assert len(records) == 1
    assert unsigned_commits(records) == ()


def test_missing_or_different_signoff_is_rejected() -> None:
    records = (
        CommitRecord(
            revision="abc123",
            author_name="Bhargav Andhe",
            author_email="bhargavandhe2310@gmail.com",
            message="Unsigned change",
        ),
        CommitRecord(
            revision="def456",
            author_name="Bhargav Andhe",
            author_email="bhargavandhe2310@gmail.com",
            message="Signed-off-by: Another Person <another@example.com>",
        ),
    )
    assert unsigned_commits(records) == records


def test_github_bot_commit_is_exempt() -> None:
    record = CommitRecord(
        revision="abc123",
        author_name="dependabot[bot]",
        author_email="49699333+dependabot[bot]@users.noreply.github.com",
        message="Automated dependency update",
    )
    assert unsigned_commits((record,)) == ()
