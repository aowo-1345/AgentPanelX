"""Deterministic tests for the bounded active Stage output observer."""

from types import SimpleNamespace

from agentplanex.services.external_agent_runtime.observation import StageOutputObserver


def test_observer_keeps_ordered_chunks_and_marks_finished() -> None:
    observer = StageOutputObserver()
    observer.begin("stage-1")
    observer.publish("stage-1", "first\r\n")
    observer.publish_event(
        "stage-1",
        SimpleNamespace(
            method="item/agentMessage/delta",
            payload=SimpleNamespace(delta="second"),
        ),
    )

    chunks, active = observer.read_since("stage-1")

    assert active is True
    assert [(chunk.sequence, chunk.data) for chunk in chunks] == [
        (1, "first\r\n"),
        (2, "second"),
    ]

    observer.finish("stage-1")
    assert observer.is_active("stage-1") is False
    chunks, active = observer.read_since("stage-1", after_sequence=1)
    assert active is False
    assert [chunk.data for chunk in chunks] == ["second"]


def test_observer_has_no_cross_stage_output() -> None:
    observer = StageOutputObserver()
    observer.begin("stage-1")
    observer.begin("stage-2")
    observer.publish("stage-1", "one")
    observer.publish("stage-2", "two")

    first, _ = observer.read_since("stage-1")
    second, _ = observer.read_since("stage-2")

    assert [chunk.data for chunk in first] == ["one"]
    assert [chunk.data for chunk in second] == ["two"]


def test_observer_cleanup_is_idempotent() -> None:
    observer = StageOutputObserver()
    observer.begin("stage-1")
    observer.publish("stage-1", "output")

    observer.close("stage-1")
    observer.close("stage-1")
    assert observer.read_since("stage-1") == ((), False)

    observer.begin("stage-2")
    observer.close_all()
    observer.close_all()
    assert observer.is_active("stage-2") is False
