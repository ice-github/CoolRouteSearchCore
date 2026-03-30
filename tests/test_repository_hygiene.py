from pathlib import Path


def test_tooling_files_and_references_are_removed() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    token = "".join(["t", "a", "k", "t"])
    token_upper = "".join(["T", "A", "K", "T", "_"])
    hidden_dir = "".join([".", "t", "a", "k", "t"])
    workflow_file = "".join(["t", "a", "k", "t", ".", "y", "m", "l"])

    assert not (repo_root / "package.json").exists()
    assert not (repo_root / hidden_dir).exists()
    assert not (repo_root / ".github/workflows" / workflow_file).exists()

    for relative_path in [
        "README.md",
        "AGENTS.md",
        ".github/workflows/gportal-integration.yml",
    ]:
        contents = (repo_root / relative_path).read_text(encoding="utf-8")
        lowered = contents.lower()

        assert token not in lowered
        assert token_upper not in contents
