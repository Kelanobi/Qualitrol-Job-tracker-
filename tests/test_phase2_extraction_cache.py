from dataclasses import dataclass

from app.phase2.extraction_cache import ExtractionCache


@dataclass
class Parsed:
    values: list[str]


def test_unchanged_document_is_extracted_once_and_results_are_isolated(tmp_path):
    path = tmp_path / "document.pdf"
    path.write_bytes(b"first")
    calls = []

    def extract(source, label):
        calls.append(source.read_bytes())
        return Parsed([label])

    cache = ExtractionCache()
    first = cache.extract(extract, path, "DCL")
    first.values.append("caller mutation")
    second = cache.extract(extract, path, "DCL")

    assert calls == [b"first"]
    assert second.values == ["DCL"]


def test_changed_document_is_reextracted(tmp_path):
    path = tmp_path / "document.pdf"
    path.write_bytes(b"first")
    calls = []

    def extract(source):
        calls.append(source.read_bytes())
        return Parsed([source.read_text()])

    cache = ExtractionCache()
    cache.extract(extract, path)
    path.write_bytes(b"second version")
    result = cache.extract(extract, path)

    assert calls == [b"first", b"second version"]
    assert result.values == ["second version"]
