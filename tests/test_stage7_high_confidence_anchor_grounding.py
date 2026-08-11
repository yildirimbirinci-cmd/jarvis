from pathlib import Path

from artmach_assistant.core.own_code_anchor_repair import (
    repair_high_confidence_missing_anchors,
)


def _source() -> str:
    return (
        "class AssistantEngine:\n"
        "    def handle(self, raw_text):\n"
        "        if answer in {\n"
        "            APP_EXIT_SIGNAL, APP_IDLE_SIGNAL,\n"
        "        }:\n"
        "            signal_answer = True\n"
        "        if runtime is not None:\n"
        "            runtime.complete(\n"
        '                "yerel durum komutu",\n'
        "                turn_id=turn_id,\n"
        "                allow_thinking=True,\n"
        "            )\n"
        "        final_answer = answer\n"
    )


def test_near_exact_missing_anchor_is_grounded_from_live_method(
    tmp_path: Path,
) -> None:
    (tmp_path / "core").mkdir()
    source = _source()
    (tmp_path / "core" / "assistant.py").write_text(
        source,
        encoding="utf-8",
    )
    rejected = (
        "if answer in {\n"
        "            APP_EXIT_SIGNAL, APP_IDLE_SIGNAL,\n"
        "        }:\n"
        "            signal_answer = True\n"
        "        if runtime is not None:\n"
        "            runtime.complete(\n"
        '                "yerel durum komutu",\n'
        "                turn_id=turn_id,\n"
        "                allow_thinking=False,\n"
        "            )\n"
        "        final_answer = answer\n"
    )
    payload = {
        "files": [{
            "path": "core/assistant.py",
            "operations": [{
                "op": "replace",
                "old": rejected,
                "new": "        final_answer = answer\n",
            }],
        }]
    }

    repaired = repair_high_confidence_missing_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "core/assistant.py icindeki AssistantEngine.handle "
            "metodunu daha kisa hale getir"
        ),
        minimum_score=0.90,
    )

    old = repaired["files"][0]["operations"][0]["old"]
    assert old in source
    assert source.count(old) == 1
    assert "allow_thinking=True" in old
    assert payload["files"][0]["operations"][0]["old"] == rejected


def test_low_confidence_missing_anchor_is_not_guessed(
    tmp_path: Path,
) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "assistant.py").write_text(
        _source(),
        encoding="utf-8",
    )
    invented = "totally unrelated invented source block\n" * 5
    payload = {
        "files": [{
            "path": "core/assistant.py",
            "operations": [{
                "op": "replace",
                "old": invented,
                "new": "replacement",
            }],
        }]
    }

    repaired = repair_high_confidence_missing_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "core/assistant.py icindeki AssistantEngine.handle "
            "metodunu daha kisa hale getir"
        ),
    )

    assert repaired is payload
    assert repaired["files"][0]["operations"][0]["old"] == invented
