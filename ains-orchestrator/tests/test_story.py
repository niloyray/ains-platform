"""I24 storyboarding: parsing the agent reply, the Jira description shape, create and update through the fakes."""

from __future__ import annotations

from ains_orchestrator.story import StoryDraft, parse_draft, read_story, split_description, update_story, write_story
from tests.fakes import FakeJira

REPLY = (
    "Here is the story.\n```json\n"
    '{"summary": "Add a version command", "description": "Print the version.\\nFrom pyproject.", '
    '"acceptance_criteria": ["prints the version", "exits 0"], "out_of_scope": ["later story: changelog"], '
    '"questions": ["Prefix with v?"]}\n```\nDone.'
)


def test_T_I24_1_parse_draft_fenced_and_bare():
    d = parse_draft(REPLY)
    assert d.summary == "Add a version command" and d.acceptance_criteria == ["prints the version", "exits 0"]
    assert d.questions == ["Prefix with v?"] and d.out_of_scope == ["later story: changelog"]
    bare = parse_draft('{"summary": "S", "description": "D", "acceptance_criteria": "one"}')
    assert bare.acceptance_criteria == ["one"] and bare.questions == []


def test_T_I24_2_jira_description_round_trips():
    d = parse_draft(REPLY)
    text = d.jira_description()
    assert "Acceptance criteria:" in text and "- exits 0" in text and "Out of scope:" in text
    body, ac, oos = split_description(text)
    assert body.startswith("Print the version.") and ac == d.acceptance_criteria and oos == d.out_of_scope


def test_T_I24_3_write_and_update_through_jira():
    jira = FakeJira()
    d = parse_draft(REPLY)
    key = write_story(jira, "AINSSB", d, ready=True)
    assert (
        key == "AINSSB-1" and jira.status_of(key) == "Ready" and jira.issues[key]["fields"]["labels"] == ["ai-native"]
    )
    back = read_story(jira, key)
    assert back.summary == d.summary and back.acceptance_criteria == d.acceptance_criteria
    update_story(jira, key, StoryDraft(summary="Changed", description="New text"))
    assert read_story(jira, key).summary == "Changed"


def test_T_I24_7_epic_and_story_under_it():
    from ains_orchestrator.story import write_epic

    jira = FakeJira()
    epic = write_epic(jira, "AINSSB", "Version tooling", "Everything about versions.")
    assert jira.issues[epic]["fields"]["issuetype"]["name"] == "Epic" and jira.issues[epic]["fields"]["labels"] == []
    key = write_story(jira, "AINSSB", StoryDraft("Add a version command", "Print it."), epic=epic)
    assert jira.issues[key]["fields"]["parent"] == {"key": epic} and jira.issues[key]["fields"]["labels"] == [
        "ai-native"
    ]
