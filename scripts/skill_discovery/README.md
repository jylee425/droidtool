# Skill Discovery Pipeline

This directory contains the current GUI skill-discovery workflow:

```text
rule-based proposal
  -> GUI exploration
  -> Skill generation
  -> Markdown rendering
```

The proposal stage is deterministic and does not call a model. Both model-based creation stages default to `gemini-3.5-flash`. The registration package is not part of this workflow; it converts legacy app-state `tool_spec.json` files into an eval-compatible tool registry and does not call a model.

## Generate an exploration proposal

Generate the three sandbox exploration patterns (`ux_overview`,
`settings_configuration`, and `entity_lifecycle`):

```bash
python scripts/skill_discovery/proposal/run_proposal.py \
  --app settings \
  --system_doc scripts/tool_generation/_asset/settings.md
```

This writes JSON artifacts under:

```text
output_skill/proposal/<app_slug>/
  exploration_proposal.json
  proposal_context.json
  run_summary.json
```

## Collect GUI exploration evidence

Run the proposal through the Android GUI agent:

```bash
python scripts/skill_discovery/creation/run_exploration.py \
  --app broccoli_app \
  --model gemini-3.5-flash \
  --emulator_serial emulator-5554
```

Limit a run to selected proposal targets or actions when iterating:

```bash
python scripts/skill_discovery/creation/run_exploration.py \
  --app broccoli_app \
  --targets recipe_management \
  --actions create_recipe read_recipe_details
```

Exploration does not create Markdown. It retains one replayable evidence bundle
per proposed action:

```text
output_skill/exploration/<app_slug>/<target_slug>/<action>/
  rollout.json
  step_000_screen.png
  step_000.json
  step_001_screen.png
  step_001.json
  ...
```

Each step JSON follows the GUI rollout log shape and records the screenshot
supplied to the model, prompts, raw response, parsed action, and execution
result. These artifacts are the input to the later `SKILL.md` creation stage.

The default model is `gemini-3.5-flash`; `--model` can override it.

## Generate skill JSON and Markdown

Generate structured app knowledge and GUI skills, then render Markdown:

```bash
python scripts/skill_discovery/creation/run_skill_discovery.py \
  --model gemini-3.5-flash \
  --workers 4
```

```text
output_skill/skill/
  <app_slug>.md
  json/<app_slug>.json
  raw_io/<app_slug>/
  _run_summary.json
```

This stage also defaults to `gemini-3.5-flash`. It promotes only complete,
repeatable, visibly verified routes from the exploration evidence into skills.

Re-render Markdown from the saved JSON without calling the model:

```bash
python scripts/skill_discovery/creation/run_skill_discovery.py --convert_only
```

## Registration

`registration/register_tools.py` belongs to the older app-state tool pipeline.
It expects generated `tools.py` and `tool_spec.json` files, not the
`output_skill/skill` artifacts produced above. It is retained only for that
legacy registry conversion and performs no Gemini calls.
