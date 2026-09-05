# Android Agent Tool Generation

This repository contains the local evaluation and workflow for generating app-state-tool on three Android agent benchmarks:

- Android World
- B-MoCA
- MobileSafetyBench

It includes vendored benchmark code, setup helpers, local evaluation adapters, codes for app-state tool generation workflow, and shared runners for evaluating GUI-only, GUI-skills, and CLI-command baselines.

## Included tool outputs

The repository includes `output_tool/developer_document`,
`output_tool/implementation`, and the three final
registration directories: `registration_isolation_test`,
`registration_lifecycle_test`, and `registration_unit_test`.
Tool bundles retain only `tools.py`, `__init__.py`, and `tool_spec.json`.
The evaluation loader requires `tool_spec.json` alongside each tool module.
Target metadata, verification records, raw model input/output, repair prompts,
intermediate manifests, repair progress, and other generated output directories
are excluded by `.gitignore`. The workflow can regenerate these local artifacts;
rerun verification before resuming a repair loop that needs registration records.

## Vendor assets

The vendored source code, task definitions, configuration files, environment scripts, and small database fixtures are included. 
However, some vendor assets are not included to keep this repository below 50 MB. 
For example, large image, wallpaper, and other media assets distributed with the upstream benchmarks are not included. 
Before running the complete benchmark suites, it may be necessary to download the corresponding assets again from the upstream Android World, B-MoCA, and MobileSafetyBench repositories and restore them to their original locations:
- `vendor/android_world/assets/setup_avd.mp4`
- `vendor/b-moca/asset/environments/resource/wallpapers_jpg/`
- `vendor/mobilesafetybench/asset/environments/resource/files/`
- `vendor/mobilesafetybench/asset/environments/resource/base64/`
- `vendor/mobilesafetybench/asset/figures/`
