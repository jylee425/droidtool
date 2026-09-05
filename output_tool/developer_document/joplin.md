# Joplin Developer Document

## App Identity
- Package name: `net.cozic.joplin`
- Main app data root: `/data/data/net.cozic.joplin`
- State summary: notes and notebook/folder relationships are stored in an app-private SQLite database.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/data/net.cozic.joplin/databases/joplin.sqlite` | Common authoritative local note store |
| SQLite DB | `/data/data/net.cozic.joplin/databases/database.sqlite` | Alternate/older local note store |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `note` | selected Joplin DB; table `notes` | `id`, `title`, `body`, `parent_id` when present, `created_time`, `updated_time`, deleted/conflict flags when present |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `folder` | selected Joplin DB; table `folders` | `id`, `title`, parent id, `created_time`, `updated_time` |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `note_search_projection` | selected Joplin DB; table `notes_normalized` when present | note `id`, `parent_id`, normalized `title`/`body`, todo and user-time fields exposed by the initialized schema |
| `tag` | selected Joplin DB; tables such as `tags`, `note_tags` when present | tag id/name and note relation |
| `resource` | selected Joplin DB; resource tables and app files when present | attachment metadata and note relation |
| `sync_item` | selected Joplin DB; sync tables when present | sync target metadata |

### Access Semantics
- Source of truth: Read notes and folders from the selected Joplin SQLite DB, not from rendered note UI or sync cache alone. The state surfaces are the literal absolute device paths `/data/data/net.cozic.joplin/databases/joplin.sqlite` and `/data/data/net.cozic.joplin/databases/database.sqlite`; a logical label such as `joplin_sqlite_db` is not a state-surface identifier.
- Entity semantics: Notes contain title/body and optional parent-folder linkage; folder, tag, resource, sync, encryption, conflict, deletion, and markup state remain distinct modeled fields or relations.
- Selector semantics: Use exact textual `id` as the durable selector for mutation. Titles are discovery selectors only; resolve them to ids first and reject ambiguous mutation targets.
- Value encoding: Infer textual id representation and timestamp scale from existing rows rather than assuming autoincrement, UUIDs, seconds, or milliseconds. Treat note titles and bodies as opaque text values whose line breaks, whitespace, punctuation, and Unicode content are part of the stored state. Creation sets `created_time`/`updated_time`; update preserves creation time and advances update time in the existing scale.
- Relationship invariants: Validate folder ids before assigning `parent_id`, prevent invalid/cyclic hierarchy, preserve auxiliary tag/resource/sync relations unless explicitly changed, and keep an initialized `notes_normalized` projection aligned with its owning note when that projection participates in app search.
- Boundary: Deleted or conflicted rows are not ordinary active notes. Exclude them from default user-facing reads when the schema exposes those states, while retaining explicit access only when the modeled action requests it. Do not treat exported Markdown files or SharedPreferences as authoritative note/folder content.

### Access Procedure
- Surface selection: Preserve each documented database candidate's literal absolute device path as the declared state surface and probe it in the app-private data area through a privileged context. Preserve the probe outcome: distinguish an absent candidate from a failed or unauthorized probe, and never report an access or command-execution failure as “database not found.”
- Initialization: Require an initialized database with Joplin-shaped note and folder tables; do not create a replacement schema when neither documented candidate is usable.
- Runtime discovery: Choose the initialized candidate only after a successful privileged probe confirms a regular file, its SQLite header, and the required Joplin tables. Inspect note id, timestamp, parent, deletion/conflict, required, relation, and optional normalized-search columns while tolerating optional schema elements. Missing-file, permission, and command-execution failures remain distinct outcomes.
- Read procedure: Folder listing, note search, and note detail are distinct reads. Read complete authoritative title and body values from `notes`; use `notes_normalized` only as a discovered search projection and resolve results back to note ids. Title matching may support exact and partial discovery, but normalized or approximate matches must return all candidates rather than silently select one when multiple notes remain. Default reads exclude rows marked deleted or conflicted when those columns exist. Folder listing and note search remain meaningful when the initialized database contains no rows and do not require a pre-existing row.
- Write procedure: Resolve one active note id before update. Note creation returns the stable note id used by subsequent detail, update, and delete operations. A non-empty `parent_id` references an existing folder row; schemas that permit a parentless note store the documented empty parent representation. Append preserves the existing body and adds exactly the requested text without an implicit separator; replace substitutes the body. Creation on a missing title occurs only when `create_if_missing` is explicitly modeled, follows discovered required-column defaults, and preserves requested parent linkage. When an initialized normalized-search projection is required by the schema, create/update its note-linked fields in the same transaction. Treat every title, body, id, and parent value as opaque data: bind or encode values without textual placeholder substitution, so literal SQL-significant content—including question marks, quotes, newlines, and Unicode—round-trips unchanged. In particular, updating a note whose existing body contains `?` must never consume that character as a placeholder for a later argument.
- Delete procedure: Resolve one exact active note id. Follow the schema's documented deletion model and update or remove directly owned relation rows atomically; do not delete shared resource files or unrelated folder/tag rows merely because a note is removed.
- Process/file handling: Perform discovery, snapshot, sidecar, metadata, and replacement operations through the same privileged app-private-data boundary. Every step of a multi-step private-file operation must independently retain that boundary; do not assume that privilege established for one step automatically applies to a following step. Stop Joplin before taking or replacing a database snapshot, carry WAL/SHM state consistently, and restore the original database ownership, security context, and restrictive mode.

### Consistency / Recovery
- Snapshot consistency: After a self-contained committed DB is installed, stale device-side `-journal`, `-wal`, or `-shm` files must not remain beside it; incompatible sidecars can replay pre-mutation pages or make the database unreadable.
- Transaction consistency: Validate and mutate a note plus its explicitly modeled relationships atomically.
- Relationship consistency: Note mutation must preserve parent-folder, normalized-search, tag, resource, sync, encryption, conflict, and deletion state unless the requested interface explicitly changes those relations.
- Process consistency: Database discovery and snapshot access require a privileged app-private-data context; command or permission failure remains an access error rather than evidence that the database is absent.
- Recovery boundary: Never synthesize an empty Joplin store over an inaccessible or unrecognized candidate, and never discard attachment resources without relation and ownership evidence.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Preferences are not the note/folder content store | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: Do not use SharedPreferences as a source of note or folder content.

### Access Procedure
- Not applicable: Note and folder content access uses the selected Joplin database.

### Consistency / Recovery
- Boundary: Preference repair must not be used as a substitute for note/folder database recovery.

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
- Boundary: No provider-backed note/folder API is documented for the documented state interface; use the private DB only in controlled root/ADB environments.

### Access Procedure
- Not applicable: Joplin note and folder content access uses the selected private database.

### Consistency / Recovery
- Boundary: Do not create a provider mirror that Joplin does not own.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| None | Note content is represented by DB rows, not exported Markdown files | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| `resource_file` | app-private resource files when present | file path/name referenced by resource metadata |

### Access Semantics
- Boundary: Resource files should be read or written only through metadata-linked resource operations, not as standalone note content.

### Access Procedure
- Surface selection: Resolve resource files through Joplin resource metadata and note-resource relations.
- Delete procedure: A resource file is not an independent note deletion target; deletion requires an explicitly modeled resource lifecycle.

### Consistency / Recovery
- Relationship consistency: Resource metadata, note-resource relations, and the underlying file must remain associated.
