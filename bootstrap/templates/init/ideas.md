# {{repo_name}} — idea backlog

<!-- Replace every placeholder in angle brackets. Keep the headings: /ains story reads this shape. -->

## Product

<One paragraph: what the product is, who uses it, where it runs, what it stores. State the things that never change.>

## Rules for every story

- <e.g. no test uses the network>
- <e.g. every setting comes from an environment variable listed in .env.example>
- <e.g. every stored item keeps the link to its source>

## Features and stories

Each feature becomes a Jira epic; each story becomes a Jira story under it, one pull request's worth of work.
`Needs first` names the stories that must be merged before this one can start. `Priority` is MVP or later.

### F1 <feature name>

<Two or three lines on what this feature is for.>

- **F1-S1 <story title, imperative>** — Priority: MVP. Needs first: nothing.
  <What changes and why, in two or three lines. Acceptance: what a test can check.>
- **F1-S2 <story title>** — Priority: MVP. Needs first: F1-S1.
  <...>

### F2 <feature name>

<...>

- **F2-S1 <story title>** — Priority: later. Needs first: F1-S1.
  <...>
