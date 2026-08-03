# How to restore this archived chunk

**Archived from:** {{original_paths_list}}
**Archived to:** {{archive_dir}}

## Restore steps

1. Copy files back to their original locations:

   ```bash
{{restore_commands}}
   ```

2. If the active project state references the restored files, update:
   - `PLAN.md` chunk log
   - `SUPERHUMAN.md` archive log (add a restoration entry)

3. Re-run tests for the affected components:

   ```bash
{{test_commands}}
   ```

## Caveats
{{any_known_breakages_or_dependencies_to_revisit}}
