from coglossa.schema import (
    TERMINAL_STATES,
    Entity,
    Flag,
    FlagCode,
    FlagStatus,
    Report,
    Revision,
    RevisionSource,
    RunState,
)


def test_run_states_follow_the_spec_lifecycle():
    assert [s.value for s in RunState] == [
        "created",
        "ingesting",
        "profiling",
        "building_guide",
        "awaiting_guide_approval",
        "translating",
        "sweeping",
        "complete",
        "complete_with_errors",
        "failed",
        "cancelled",
    ]
    assert {
        RunState.COMPLETE,
        RunState.COMPLETE_WITH_ERRORS,
        RunState.FAILED,
        RunState.CANCELLED,
    } == TERMINAL_STATES


def test_flags_default_to_open():
    flag = Flag(code=FlagCode.LOW_MEANING, explanation="x")
    assert flag.status is FlagStatus.OPEN
    assert flag.dismiss_reason is None
    assert [s.value for s in FlagStatus] == ["open", "accepted", "dismissed", "resolved"]


def test_revision_round_trips():
    rev = Revision(span_id="p3", source=RevisionSource.SWEEP, before="a", after="b", reason="r")
    assert Revision.model_validate_json(rev.model_dump_json()) == rev


def test_entities_are_unlocked_by_default():
    assert Entity(name="Ana", rendering="Ana", kind="person").locked is False


def test_report_has_run_id_and_revisions():
    fields = Report.model_fields
    assert fields["run_id"].default is None
    assert fields["revisions"].default_factory() == []
