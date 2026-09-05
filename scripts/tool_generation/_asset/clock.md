# Clock Developer Document

## App Identity
- Package names: `com.google.android.deskclock`; fallback `com.android.deskclock`
- Main app data root: `/data/user_de/0/<clock_package>` for alarm DB; `/data/data/<clock_package>` or `/data/user_de/0/<clock_package>` for preferences depending on build
- State summary: alarms are stored in device-protected private SQLite state; some user-facing clock settings are stored in private SharedPreferences.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/user_de/0/com.google.android.deskclock/databases/alarms.db` | Authoritative alarm store for Google Clock builds |
| SQLite DB | `/data/user_de/0/com.android.deskclock/databases/alarms.db` | Package fallback alarm store |
| SharedPreferences XML | `/data/user_de/0/com.google.android.deskclock/shared_prefs/com.google.android.deskclock_preferences.xml` | Device-protected preference path |
| SharedPreferences XML | `/data/data/com.google.android.deskclock/shared_prefs/com.google.android.deskclock_preferences.xml` | Credential-protected preference path; select the path used by the installed package before access |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `alarm` | `alarms.db`; table `alarm_templates` | `_id`, `hour`, `minutes`, `daysofweek`, `enabled`, plus optional `label`, `vibrate`, `delete_after_use`, `wakeup`, sound-related fields when present |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `alarm_instance` | `alarms.db`; table `alarm_instances` | instance id, alarm id, scheduled time, state |

### Access Semantics
- Source of truth: Use the installed Clock package's device-protected `alarms.db`, not rendered alarm widgets.
- Entity semantics: `alarm_templates` contains durable alarm definitions and `alarm_instances` contains derived scheduled occurrences; these tables do not model timer, stopwatch, world-clock, or bedtime state.
- Selector semantics: `_id` is the durable alarm selector. `(hour, minutes)` is a user-facing alternate selector and can match more than one row; ambiguous matches should be reported or resolved before mutation.
- Value encoding: `daysofweek` is a repeat-days bitmask. Use Monday=1, Tuesday=2, Wednesday=4, Thursday=8, Friday=16, Saturday=32, Sunday=64; `0` means one-time/no repeat. Boolean alarm fields are commonly stored as integer 0/1.
- Relationship invariants: Preserve unrelated alarm fields such as sound, `vibrate`, `label`, `delete_after_use`, and `wakeup`; every instance `alarm_id` must refer to an existing template.

### Access Procedure
- Initialization: If the selected package has not created `alarms.db`, initialize that Clock package before schema discovery or mutation.
- Runtime discovery: Inspect `alarm_templates` columns and populate only fields available on the installed build.
- Read procedure: Read configured alarm templates from the selected package and treat a missing initialized table as an empty collection only after package/database selection is complete.
- Write procedure: Add/update/delete alarm operations should inspect available columns and mutate exact `alarm_templates` rows; delete operations should also handle related `alarm_instances` rows by `alarm_id`.
- Write procedure: New alarm rows derive optional-field defaults from the installed schema or existing build-compatible rows; do not invent values for unknown columns.
- Write procedure: A time-based enabled-state update applies to every template matching the requested `(hour, minutes)` selector.
- Delete procedure: A time-based delete removes every matching template and its `alarm_instances` rows; an id-based delete, if exposed, removes only the selected template.
- Process/file handling: Use the installed package's device-protected `alarms.db`; force-stop before DB replacement, treat every privileged database transfer as fallible, reject missing, empty, or incomplete snapshots, handle WAL/SHM sidecars consistently, and preserve the selected resource identity, owner, mode, and security context.

### Consistency / Recovery
- Snapshot consistency: Stop the selected Clock package before copying a mutable alarm database. If WAL mode is active, either copy the main DB with matching `-wal`/`-shm` files or checkpoint to a self-contained snapshot; never combine sidecars from different moments.
- Relationship consistency: `alarm_templates` is durable alarm definition state, while `alarm_instances` represents derived scheduled occurrences; mutations must not leave instances pointing to deleted templates.
- Recovery boundary: Preserve the original database uid/gid, restrictive mode, and security context; a failed or partial transfer leaves the original alarm database authoritative and unmodified, and stale device-side WAL/SHM may be removed only when the replacement database already contains their committed state.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `clock_preference` | selected Clock preference XML | `display_clock_seconds` boolean |
| `clock_preference` | selected Clock preference XML | `home_time_zone` when initialized |
| `clock_preference` | selected Clock preference XML | build-dependent clock style, alarm, timer, week-start, and screen-saver keys with discovered value domains |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: Only define preference writes for exact keys with a documented value domain.

### Access Procedure
- Runtime discovery: Resolve the actual preference path for the installed package before reading or writing.
- Write procedure: Parse and write XML structurally and preserve unknown keys.

### Consistency / Recovery
- Process consistency: Quiesce the app before external preference replacement so in-process cached values cannot overwrite the file.

## Providers
### Primary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Secondary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: No provider-backed Clock state API is documented for the documented state interface.

### Access Procedure
- Not applicable: Use the selected Clock package's database and preferences.

### Consistency / Recovery
- Boundary: Do not infer a provider mutation path from rendered Clock UI state.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: No file-backed Clock object lifecycle is documented for the documented state interface.

### Access Procedure
- Not applicable: Alarm and Clock preference access uses private DB/XML surfaces.

### Consistency / Recovery
- Boundary: Do not infer file-backed alarm state from exports, screenshots, or UI artifacts.
