# Knowledge Graph Scheduler

You manage scheduled automatic updates to knowledge graphs. This command sets up, lists, or removes cron-based triggers that periodically run `/build-kg` in UPDATE mode.

## Input

Parse `$ARGUMENTS` for one of these modes:

### Create a schedule:
```
/schedule-kg "topic" --cron "0 8 * * 1" --output KG_FolderName
```
- **topic** (required): The research topic
- **--cron** (optional): Cron expression. Defaults to `0 8 * * 1` (every Monday 8am)
- **--output** (optional): Target KG folder name
- **--threshold <N>** (optional): Minimum novel PMIDs (per the preflight check) required to run a scheduled update. Defaults to 3. Recorded as `schedule.threshold` in the manifest and substituted into the scheduled prompt.

### List active schedules:
```
/schedule-kg --list
```

### Remove a schedule:
```
/schedule-kg --remove <trigger-name>
```

---

## Mode: Create Schedule

### Step 1: Verify KG Exists
1. Determine the target KG folder (same logic as `/build-kg` Phase 0):
   - If `--output` is provided, check for that folder
   - Otherwise, scan for `KG_*` folders matching the topic
2. If no existing KG is found, inform the user: "No existing KG found for this topic. Run `/build-kg` first to create the initial knowledge graph, then schedule updates."
3. If found, read its `manifest.json` and confirm with the user.

### Step 2: Create the Trigger
Use the `/schedule` skill to create a remote trigger with:
- **Name**: `kg-update-<slugified-topic>` (e.g., `kg-update-mRNA-vaccines`)
- **Cron**: The user-specified cron expression or default `0 8 * * 1`
- **Prompt**: The following prompt for the scheduled agent:

```
This is a scheduled KG update run for <KG_FolderName>.

1. First run the deterministic preflight check (no MCP tools, no KG loading):
   python3 scripts/preflight.py <KG_FolderName> --threshold <threshold> --log
2. If the JSON output has "proceed": false, report exactly one line — "Quiet week: {novel_count} novel PMIDs since {since_date}, below threshold {threshold} — skipped update." — and STOP. Do not load the KG and do not call any MCP tools.
3. If preflight exits non-zero (network error, or a legacy manifest without search_profile), fall through to step 4 anyway — a wasted full run is better than a silently skipped update.
4. Otherwise run: /build-kg "<topic>" --output <KG_FolderName>
   The KG already exists, so this runs in UPDATE mode: it derives its date window from schedule.last_run and stamps schedule.last_run when it finishes (Phase 4 step 1d). Focus on new research that adds to or revises existing knowledge nodes.
```

### Step 3: Update Manifest
Add or update the `schedule` field in the KG's `manifest.json`:
```json
{
  "schedule": {
    "cron": "0 8 * * 1",
    "last_run": null,
    "trigger_name": "kg-update-<slugified-topic>",
    "threshold": 3
  }
}
```

### Step 4: Confirm
Print a summary:
```
=== Schedule Created ===
KG: KG_TopicName/
Trigger: kg-update-<slug>
Cron: 0 8 * * 1 (Every Monday at 8:00 AM)
Next run: <estimated next run time>
```

Log the operation:
```
python3 scripts/append_log.py {KG_FOLDER} --op schedule --summary "Schedule created: {trigger_name}, cron: {cron_expression}."
```

---

## Mode: List Schedules

1. Scan the current directory for all `KG_*` folders.
2. For each, read `manifest.json` and check for a `schedule` field.
3. Also list any active remote triggers via the `/schedule` skill with a list action.
4. Print a table:

```
=== Active KG Schedules ===
KG Folder            | Trigger Name              | Cron          | Last Run
KG_mRNA_Vaccines     | kg-update-mRNA-vaccines   | 0 8 * * 1     | 2026-04-01
KG_CRISPR_Therapy    | kg-update-CRISPR-therapy  | 0 0 * * 0     | 2026-03-30
```

If no schedules are found, report: "No active KG schedules found."

---

## Mode: Remove Schedule

1. Parse the trigger name from `--remove <name>`.
2. Use the `/schedule` skill to delete the remote trigger by name.
3. Find the KG folder whose `manifest.json` references this trigger name.
4. Remove the `schedule` field from that KG's `manifest.json`.
5. Confirm:

```
=== Schedule Removed ===
Trigger: kg-update-mRNA-vaccines
KG: KG_mRNA_Vaccines/
The KG will no longer be updated automatically.
```

Log the operation:
```
python3 scripts/append_log.py {KG_FOLDER} --op schedule --summary "Schedule removed: {trigger_name}."
```

---

## Important Rules

1. **Never create a schedule for a KG that doesn't exist yet.** The user must run `/build-kg` first.
2. **One schedule per KG.** If a schedule already exists for the target KG, ask the user if they want to replace it.
3. **Validate the cron expression** — it must have 5 fields (minute, hour, day-of-month, month, day-of-week). Reject invalid expressions with a helpful message.
4. **The scheduled agent runs /build-kg** — all the actual KG logic lives there. This command only manages the trigger.
