# init/ — where {{repo_name}} starts

This folder belongs to the project, not to the platform. Onboarding creates it once and never touches it again;
the CI guard does not protect it, so edit freely and commit.

What goes here:

- `ideas.md` — the idea backlog: the product in a paragraph, the rules every story must respect, then features and
  under each feature the stories, one pull request's worth each. Write it with ordinary Claude Code in this clone
  ("help me write init/ideas.md for this project ...").
- anything else that precedes the first story: notes, sketches, links, a competitor list, a glossary.

When the backlog is ready, push it to Jira project `{{jira_key}}` from Claude Code in this folder:

    /ains story read init/ideas.md, propose features as epics and the stories under them, show me the plan, and push to To Do only after I confirm

The skill shows the cut first, drafts each story through the story agent, shows the set, and only on your yes writes
one epic per feature and the stories under it, all in **To Do**. You then release stories one at a time by moving them
to **Ready**; the platform takes each through spec, plan, implement, review and the gates.
