import pytest

from yucode.model_output import ModelOutputParseError, parse_json_object


@pytest.mark.parametrize("output", [
    '{"actions":[]}',
    '```json\n{"actions":[]}\n```',
    '以下是 JSON：\n{"actions":[]}\n请保存。',
])
def test_extracts_a_single_json_object_from_common_model_wrappers(output: str) -> None:
    assert parse_json_object(output) == {"actions": []}


@pytest.mark.parametrize("output", [
    "不是 JSON",
    '{"actions":',
    '{"actions":[]}\n{"actions":[]}',
    '```json\n{"actions":[]}\n```\n```json\n{"actions":[]}\n```',
])
def test_rejects_invalid_or_ambiguous_model_output(output: str) -> None:
    with pytest.raises(ModelOutputParseError):
        parse_json_object(output)
