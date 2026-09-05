# Markor Developer Document

## App Identity
- Package name: `net.gsantner.markor`
- Main app data root: `/data/data/net.gsantner.markor`
- State summary: notes are external-storage files; Markor preferences can point to the notebook root and track UI/file metadata.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| External storage | configured notebook root, commonly under `/sdcard` | Authoritative note and folder store |
| SharedPreferences XML | `/data/data/net.gsantner.markor/shared_prefs/app.xml` | App preferences and notebook/file-browser references |
| SharedPreferences XML | `/data/data/net.gsantner.markor/shared_prefs/net.gsantner.markor_preferences.xml` | Alternate notebook-root preference file supported by the app |
| SharedPreferences XML | `/data/data/net.gsantner.markor/shared_prefs/DOCUMENT_MOD_TIMES.xml` | App-maintained document modification metadata |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: No DB-backed Markor note lifecycle is documented; external files under the configured notebook root are the authoritative note/folder state.

### Access Procedure
- Not applicable: Markor note and folder access uses the configured external-storage root.

### Consistency / Recovery
- Boundary: Do not create a private-DB representation for file-backed Markor notes.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `notebook_root_reference` | `app.xml` or `net.gsantner.markor_preferences.xml` | `pref_key__notebook_directory`, `exts_notebook_directory`, `pref_key__file_browser_last_browsed_folder` when present |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `document_mod_time_metadata` | `DOCUMENT_MOD_TIMES.xml` | document-path modification entries |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `markor_ui_preference` | `app.xml` | `pref_key__is_main_recreate_required` |

### Access Semantics
- Source of truth: Read notebook root and file-browser references from preferences only to resolve file paths; if no key is present, use `/storage/emulated/0/Documents/markor` as the default notebook path.
- Entity semantics: The resolved notebook-root reference scopes the note and folder entities on the file surface; it is configuration, not note content.
- Selector semantics: Resolve one canonical notebook root from the documented preferences and apply that same root to every relative path in one operation.
- Value encoding: Preference path values may use equivalent external-storage aliases; normalize them to one canonical path without changing the stored preference value.
- Boundary: Do not define write operations for app-managed keys that can be rewritten on launch, such as `pref_key__is_main_recreate_required`.

### Access Procedure
- Runtime discovery: Read notebook-root candidates from `app.xml` and `net.gsantner.markor_preferences.xml`; use the documented default only when configured roots are absent.
- Read procedure: Parse preference XML structurally to resolve notebook-root candidates; keep preference access separate from note-file content access.
- Write procedure: Preference mutation is not part of the documented notebook lifecycle; preserve notebook-root and app-managed metadata keys.
- Delete procedure: Deleting a note or folder does not delete its notebook-root preference or document metadata store wholesale.
- Process/file handling: Resolve the configured root before interpreting relative selectors, then use the same canonical root for source, destination, parent, and containment decisions. A configured root that is invalid or inaccessible is an access error; use the default only when no configured-root value is present.

### Consistency / Recovery
- Process consistency: App-managed recreation and document-modification metadata may be rewritten independently and must not be treated as authoritative file content.

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
| Storage Access Framework document URI | Android document provider if a file was opened through SAF | URI, display name, permissions |

### Access Semantics
- Entity semantics: SAF/document-provider URIs are auxiliary references unless a specific opened document URI is available.
- Boundary: Prefer configured external-storage paths for the documented Markor state interface.

### Access Procedure
- Surface selection: Use a specific persisted SAF URI only when the interface explicitly models document-provider access; otherwise resolve the configured external-storage root.

### Consistency / Recovery
- Recovery boundary: SAF access depends on the URI permission grant and must not be treated as interchangeable with unrestricted filesystem access.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| `note_file` | configured notebook root plus relative file path | filename, path, extension, body/content, modified time |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| `folder` | configured notebook root plus relative folder path | folder name/path, child notes/folders |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| `attachment_file` | paths referenced from note content | linked media/file path |

### Access Semantics
- Entity semantics: Note files contain durable text content; directories contain child paths; `DOCUMENT_MOD_TIMES.xml` and recreation flags are app-maintained metadata rather than file content.
- Selector semantics: Relative path under the configured notebook root is the primary selector. Absolute paths should be accepted only when the documented interface explicitly allows them.
- Value encoding: Preserve requested UTF-8 text exactly, including empty content, newline placement, and file extension; treat folder paths separately from note file paths.
- Relationship invariants: Source, destination, parent, and children are interpreted under the same canonical root. A note write and subsequent note read using the same relative path must address the same filesystem object.
- Boundary: Keep parameters within the canonical notebook root; reject traversal/symlink escape, file/directory type confusion, and destination collisions outside an explicitly documented overwrite/delete contract.

### Access Procedure
- Surface selection: Resolve the notebook root from documented preference candidates and use the documented external-storage default only when no configured root is available.
- Initialization: The notebook root may be initialized only at the documented default or configured location; do not create an alternate root merely because the selected one is inaccessible.
- Runtime discovery: Canonicalize the root and both source and destination paths before filesystem access, resolving external-storage aliases and symlinks for containment decisions.
- Read procedure: Resolve and validate the selected path before reading. Directory listing is non-recursive and may expose name, relative path, type, extension, size, modification time, and permissions; note reads reject directory targets and report missing or inaccessible targets explicitly.
- Write procedure: Note creation/update, folder creation, and move/rename are distinct mutations. Note writes create missing parent directories and atomically create or replace the exact selected file while preserving requested text. Folder creation requires the immediate parent to exist except when initializing the notebook root, and an existing destination is not a successful create. Move/rename requires an existing source, rejects destination collisions, and may create missing destination parents within the root.
- Delete procedure: File deletion removes the exact file. Folder deletion is recursive for the documented delete-item interface, but the notebook root itself and any path escaping it are never valid targets.
- Process/file handling: Perform every file operation under the resolved root using filesystem access properties compatible with the shared-storage owner. Keep SharedPreferences as path configuration, not note content, and avoid racing an actively edited unsaved document when detectable.

### Consistency / Recovery
- Transaction consistency: A replacement or move must not expose a partial destination; failure preserves the last valid source or destination state where the filesystem permits atomicity.
- Relationship consistency: Apply containment and collision rules to both source and destination. Recursive folder deletion is permitted only by an interface whose contract explicitly includes it.
- Cross-surface consistency: Notebook preferences select the file root, while note bytes remain authoritative on the file surface; app-managed document metadata must not replace or contradict the selected file object.
- Process consistency: External files are authoritative, but an open editor can hold unsaved content; avoid replacing an actively edited file when detectable.
- Recovery boundary: Missing, empty, inaccessible, and invalid file states remain distinct. Never redirect a failed operation to a different root or report a partially changed path as a successful mutation.
