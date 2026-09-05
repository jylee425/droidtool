# Simple Calendar Pro Developer Document

## App Identity
- Package name: `com.simplemobiletools.calendar.pro`
- Main app data root: `/data/data/com.simplemobiletools.calendar.pro`
- State summary: local events are stored in the app-private `events.db`; app display/storage defaults are stored in preferences.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/data/com.simplemobiletools.calendar.pro/databases/events.db` | Authoritative local calendar event store |
| Android provider DB | `/data/data/com.android.providers.calendar/databases/calendar.db` | System CalendarProvider, not authoritative for local Simple Calendar event state |
| SharedPreferences XML | `/data/data/com.simplemobiletools.calendar.pro/shared_prefs/Prefs.xml` | App preferences such as calendar view |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `calendar_event` | `events.db`; table `events` | `id`, `start_ts`, `end_ts`, `title`, `location`, `description`, `time_zone`, `repeat_interval`, `repeat_rule`, `repeat_limit`, `repetition_exceptions`, `attendees`, `import_id`, `flags`, `event_type`, `parent_id`, `last_updated`, `source`, `availability`, `color`, `type`, `reminder_1_minutes`, `reminder_2_minutes`, `reminder_3_minutes`, `reminder_1_type`, `reminder_2_type`, `reminder_3_type` |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | No documented secondary entity | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `event_type` | `events.db`; table `event_types` | event type id/name/color |
| `reminder` | Inline reminder columns in `events`; CalendarProvider `Reminders` only if provider-backed flow is added | minutes/method relative to parent event |
| `calendar_task` | `events.db`; table `tasks` when present | task text/status/date |
| `widget_state` | `events.db`; table `widgets` when present | widget configuration |

### Access Semantics
- Source of truth: Read local events from `events.db`, especially `events` ordered by `start_ts`, rather than using rendered calendar cells as the source of truth.
- Entity semantics: Each `events` row represents one local event with inline reminder and recurrence fields; provider events remain separate state.
- Selector semantics: Event `id` is the durable selector. Title/date selectors are alternate and can match multiple events; resolve exact event ids before update/delete.
- Value encoding: `start_ts`/`end_ts` use Unix seconds and device-timezone date resolution; `end_ts = start_ts + duration_mins * 60`. Repeat intervals are `0`, `86400`, or `604800`; weekly rules use a Monday-bit-0 mask. Default reminder minutes are `-1` and types are `0` unless modeled.
- Relationship invariants: Preserve event type, parent, recurrence, reminder, import, and source fields as parts of the same event row.
- Boundary: Android CalendarProvider is not the authoritative surface for these local rows.

### Access Procedure
- Surface selection: Use the app-private `events.db`, not Android CalendarProvider, for local Simple Calendar event state.
- Initialization: Require an initialized `events` table before local event access.
- Runtime discovery: Inspect event columns and defaults before constructing a row, while retaining the documented timestamp and repeat encoding.
- Read procedure: Date ranges are inclusive. A start-time filter keeps events whose end is at or after the boundary; an end-time filter keeps events whose start is at or before the boundary; `after_time` compares only event start time. Title filtering is case-insensitive substring matching.
- Write procedure: Write visible event fields, timestamps, repeat fields, event type, and inline reminder columns as one consistent event row.
- Delete procedure: Require at least one date, time, title, or text filter. Resolve matches to exact event ids before deletion; title matching explicitly chooses exact or case-insensitive contains semantics, and text matching spans title, description, and location.
- Process/file handling: Stop the app before copying or replacing the private database and preserve its file metadata.

### Consistency / Recovery
- Snapshot consistency: Capture the main DB with matching WAL/SHM state or checkpoint it first; after replacement, remove only stale sidecars and restore original uid/gid, restrictive mode, and security context.
- Relationship consistency: Preserve non-null/default fields and keep `start_ts`, `end_ts`, repeat interval/rule, all-day, and reminder encodings internally coherent.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `simple_calendar_setting` | `Prefs.xml` | `view` and its documented view values |
| `simple_calendar_setting` | `Prefs.xml` | build-dependent `display_event_types`, `internal_storage_path`, and display/storage defaults |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: Keep settings interfaces separate from `calendar_event` DB interfaces unless a setting is needed only to resolve a storage/display context.

### Access Procedure
- Write procedure: Parse and write `Prefs.xml` structurally and preserve unknown keys.

### Consistency / Recovery
- Process consistency: External preference replacement requires a quiescent app process to avoid conflicts with cached values.

## Providers
### Primary
| Entity | Provider | Fields |
|---|---|---|
| Not used for local Simple Calendar event state | Android CalendarProvider exists separately | Not applicable |

### Secondary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Provider | Fields |
|---|---|---|
| `provider_event` | CalendarProvider `Events` if a provider-backed extension is added | title, time, calendar id |
| `provider_reminder` | CalendarProvider `Reminders` if provider-backed extension is added | event id, minutes, method |

### Access Semantics
- Relationship invariants: A provider-backed extension keeps provider reminders attached to their parent provider event id.
- Boundary: Do not treat Android CalendarProvider as authoritative for local Simple Calendar event state.

### Access Procedure
- Surface selection: Use CalendarProvider only for an explicitly provider-backed extension, not for local `events.db` operations.
- Delete procedure: Provider event deletion must use the provider event id and account for dependent reminder rows.

### Consistency / Recovery
- Relationship consistency: Provider reminders remain dependent on their provider event and must not be mixed with local inline reminder columns.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| None | Calendar event state is represented by DB rows | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| exported calendar file | `.ics` export path if produced | event file path/format |

### Access Semantics
- Entity semantics: Materialized `.ics` files are import/export artifacts, not live local event rows.
- Boundary: Keep file import/export lifecycle separate from direct `events.db` mutation unless explicitly linked.

### Access Procedure
- Surface selection: Use file access only for an explicitly modeled import/export operation with a concrete `.ics` path.
- Delete procedure: Deleting an exported file does not delete its local `events.db` rows, and deleting a local event does not imply export-file deletion.

### Consistency / Recovery
- Cross-surface consistency: Imported/exported artifacts and local event rows are distinct representations unless an interface explicitly links their lifecycle.
