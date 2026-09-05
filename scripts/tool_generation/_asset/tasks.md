# Tasks Developer Document

## App Identity
- Package name: `org.tasks`
- Main app data root: `/data/data/org.tasks`
- State summary: tasks, lists, tags, alarms, attachments, and locations are stored in an app-private SQLite database; app UI/notification preferences exist.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/data/org.tasks/databases/database` | Tasks database path |
| SQLite DB | `/data/data/org.tasks/databases/tasks.db` | Alternate Tasks database path; select the package database path before access |
| SQLite DB | `/data/data/org.tasks/databases/org.tasks_tasks.db` | Older/alternate Tasks database path if present |
| SharedPreferences XML | `/data/data/org.tasks/shared_prefs/org.tasks_preferences.xml` | User-facing app preferences |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `task` | Tasks DB; table `tasks` | `_id`/`id`, `title`, `notes`/`note`/`description`, `dueDate`/`due`/`due_date`, `hideUntil`/`hide_until`, `created`/`createdAt`/`created_at`, `modified`/`modifiedAt`/`modified_at`, `completed`/`completedDate`/`completed_date`, `importance`/`priority`, `deleted`/`_deleted`, `recurrence`, `parent`/`parent_id`, `order`/`position`, `read_only`/`readonly`, `collapsed`, `estimatedSeconds`/`estimated_seconds`, `elapsedSeconds`/`elapsed_seconds`, `calendarUri`/`calendar_uri`, `remoteId`/`remote_id` |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `task_list` | Tasks DB; tables such as `task_list_metadata`, `caldav_lists`, `caldav_tasks` depending on schema | `caldav_tasks.cd_task`, `cd_calendar`, `cd_remote_id`, `cd_deleted`; `caldav_lists.cdl_uuid`, `cdl_name`, `cdl_color`; list id/name/color/account/source metadata |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `tag` | Tasks DB; `tags`, `tagdata` | `task`, `name`, `tag_uid`, plus tag id/color fields when present |
| `alarm` | Tasks DB; `alarms` | `task`, `time`, `type`, `repeat`, `interval` |
| `attachment` | Tasks DB; `attachment` | `task`, `attachment_id`, `file`, `file_uuid`, MIME/name fields when present |
| `geofence` | Tasks DB; `geofences`, `places` | `geofences.task`, `place`, `arrival`, `departure`; `places.uid`, `name`, `address`, `latitude`, `longitude`, `radius` |
| `filter` | Tasks DB; `filters` | filter name/query |

### Access Semantics
- Source of truth: Read tasks and task lists from the selected Tasks DB path, inspecting table and column names defensively.
- Entity semantics: Tasks may belong to lists and own tag, alarm, attachment, geofence, recurrence, parent/subtask, completion, deletion, and sync state.
- Selector semantics: Task id is the primary selector; title/list/date filters are alternate selectors and may match multiple rows. Task-list ids should be resolved before task mutation.
- Value encoding: Date/time fields and filters use epoch milliseconds with device-timezone day boundaries; priorities commonly encode `high=0`, `medium=1`, `low=2`, `none=3`. Preserve completion, deletion, hidden, read-only, recurrence, list, and sync encodings.
- Relationship invariants: Dependent alarms, attachments, geofences, tags, and list relations require a stable parent task id and must not be orphaned.
- Boundary: The currently evidenced interface is read-oriented. A future mutation interface must separately establish schema-specific insert/update defaults and relationship handling rather than extrapolating CRUD from query semantics.

### Access Procedure
- Surface selection: Treat the app-private SQLite DB as authoritative for local task state and SharedPreferences as settings only.
- Runtime discovery: Select a documented candidate only when it has a SQLite header and task-shaped table; discover a projection from actual columns and query optional relation tables only when the requested output or filter needs them.
- Read procedure: Deleted tasks are excluded by default. Title, notes, tag, list, and recurrence filters use case-insensitive substring matching; timestamp start/end boundaries are inclusive.
- Read procedure: Positive `has_*` filters require the corresponding value or relation, while negative filters accept absent/null state. A subtask has a positive parent id; due/completed timestamps use positive stored values.
- Read procedure: Use relation-aware existence predicates for tags, lists, alarms, attachments, and locations, and order results deterministically by due date then title.
- Write procedure: No task mutation procedure is evidenced by the current interface; future writes require schema-specific defaults and relationship handling.
- Delete procedure: No task deletion procedure is evidenced by the current interface; do not extrapolate hard- or soft-delete behavior from query filters.
- Process/file handling: For future direct DB writes, quiesce the app and preserve consistent WAL/SHM state plus original database ownership and mode.

### Consistency / Recovery
- Relationship consistency: Task-list, tag, alarm, attachment, geofence, recurrence, and sync rows depend on stable task ids and must not be orphaned by mutation.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `tasks_setting` | `org.tasks_preferences.xml` | `drawer_lists_enabled` boolean |
| `tasks_setting` | same XML | build-dependent backup, notification, drawer, display, and sync preference keys |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: Keep UI/backup/notification preference interfaces separate from task/list DB interfaces unless the setting directly configures task display or behavior.

### Access Procedure
- Write procedure: Parse and write preference XML structurally and preserve unknown keys.

### Consistency / Recovery
- Process consistency: External preference replacement requires a quiescent app process to avoid conflicts with cached values.

## Providers
### Primary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented for the documented state interface | Not applicable |

### Secondary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Provider | Fields |
|---|---|---|
| CalDAV/sync provider state | provider/sync tables if configured | account/list/task sync ids |

### Access Semantics
- Boundary: No provider-backed task CRUD API is documented for the documented state interface; CalDAV/sync provider state is reference/debug state unless sync state is the user-visible state being modeled.

### Access Procedure
- Not applicable: Local task queries use the app-private database; do not infer provider CRUD from CalDAV metadata tables.

### Consistency / Recovery
- Cross-surface consistency: If a future sync interface is modeled, local task ids and remote/list ids must remain associated without inventing missing provider state.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| None | DB rows are the documented state interface | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| `attachment_file` | paths referenced by `attachment` rows | URI/path/name/MIME |
| backup/export file | backup/export directory if configured | file path/date |

### Access Semantics
- Boundary: Attachment files should be selected through DB attachment rows before file access.
- Boundary: Backup/export files are auxiliary artifacts and should use exact path checks if a backup/export interface is later documented.

### Access Procedure
- Surface selection: Resolve attachment files through their task attachment rows; use backup/export paths only for explicitly modeled operations.
- Delete procedure: Task deletion must not remove attachment files unless attachment ownership and deletion semantics are explicitly established.

### Consistency / Recovery
- Relationship consistency: Attachment rows and owned files must not be orphaned by partial task/file mutation.
- Recovery boundary: Do not treat auxiliary backup/export files as the live task database.
