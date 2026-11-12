"""tools/privacy_check.py: the scan every PR gets, since this repo is public."""
import sys

from jobpipe import config

sys.path.insert(0, str(config.ROOT / "tools"))
import privacy_check  # noqa: E402


def test_scan_finds_terms_and_the_name_but_not_inside_other_words(tmp_path):
    (tmp_path / "a.md").write_text("fine\nCall 555-0100 or ask Sam Example\nexamples of samples\n")
    (tmp_path / "font.bin").write_bytes(b"\0\0 555-0100")
    assert privacy_check.scan(tmp_path, ["555-0100"], "Sam Example") == ["a.md:2: 555-0100, Sam Example"]


def test_exceptions_come_from_the_profile_folder(tmp_path, monkeypatch):
    profile, tree = tmp_path / "profile", tmp_path / "tree"
    profile.mkdir(), tree.mkdir()
    monkeypatch.setattr(config, "PROFILE", profile)
    (profile / "publish-allow.txt").write_text("# why\nplaces.py: Atlantis, Oz\n")
    allowed = privacy_check.allowed_terms()
    assert allowed == {"places.py": {"atlantis", "oz"}}
    (tree / "places.py").write_text("CITIES = ['Atlantis']\n")
    (tree / "notes.md").write_text("moved to Atlantis\n")
    assert privacy_check.scan(tree, ["Atlantis"], "", allowed) == ["notes.md:1: Atlantis"]
