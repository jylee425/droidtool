# Files Developer Document

## App Identity
- Package name: Android external-storage/file-manager surface
- State summary: user-facing state consists of shared external-storage files and folders, with MediaStore only as auxiliary indexed metadata when present.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| External storage | `/sdcard/Download` | Common user-visible file directory |
| External storage | `/sdcard/<folder>` | Caller-selected user-visible file or folder root |
| MediaStore | Android media index | Auxiliary metadata for indexed media files |

## DB Entities

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | No DB-backed file lifecycle is documented | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: Filesystem paths, not an inferred database, represent the documented file lifecycle.

### Access Procedure
- Not applicable: Use the Files surface.

### Consistency / Recovery
- Boundary: A database representation cannot repair or replace shared-storage file state.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | No preferences-backed file lifecycle is documented | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: SharedPreferences are not a file-content store.

### Access Procedure
- Not applicable: Use exact filesystem paths.

### Consistency / Recovery
- Boundary: Preference mutation cannot create, move, rename, or delete shared files.

## Providers

### Primary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | Filesystem paths are authoritative for the documented lifecycle | Not applicable |

### Secondary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Provider/URI | Fields |
|---|---|---|
| `media_index_entry` | MediaStore when an exact file is indexed | path/URI, display name, MIME/media type, size, modified time |

### Access Semantics
- Entity semantics: MediaStore rows are auxiliary metadata for media-like files; exact filesystem paths remain the file-operation target.
- Relationship invariants: Indexed metadata, when used, refers to the same file artifact and must not be treated as a second file identity.
- Boundary: Do not define provider-only file mutation when the documented operation targets a filesystem path.

### Access Procedure
- Read procedure: Query provider metadata only when the interface explicitly requests indexed-media information for a known file.

### Consistency / Recovery
- Cross-surface consistency: File identity remains path-based even when auxiliary provider metadata exists.

## Files

### Primary
| Entity | Path | Fields |
|---|---|---|
| `file` | `/sdcard/<folder>/<name>` | filename, exact path, size, modified time, extension/type, byte content when read |
| `media_file` | shared media directories under `/sdcard` | filename, exact path, media type, size, modified time |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| `folder` | `/sdcard/<folder>` | path, name, direct child files/folders, child count |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Access Semantics
- Source of truth: Exact shared-storage paths identify files and folders.
- Entity semantics: Files contain durable bytes; folders contain child paths and are distinct from regular files.
- Selector semantics: Exact absolute path is primary; folder plus filename is an alternate selector normalized under the selected storage root.
- Value encoding: Copy/move preserves file bytes and extension; text writes preserve the requested text encoding when specified.
- Boundary: Reject traversal, root escape, file/directory type confusion, and broad recursive deletion outside an explicit recursive-delete contract.

### Access Procedure
- Runtime discovery: Resolve and normalize the root, target path, type, existence, and destination collision state before mutation.
- Read procedure: List direct children or read metadata/content for the exact requested path.
- Write procedure: Create folders, write files, copy, move, or rename only within the documented root and according to explicit overwrite/collision behavior.
- Delete procedure: Delete the exact selected file or folder; recursive folder deletion is allowed only when the action contract explicitly requests it.
- Process/file handling: Apply containment and collision checks to both source and destination paths.

### Consistency / Recovery
- Transaction consistency: A move or rename does not report a completed destination while leaving an unintended duplicate source.
- Relationship consistency: Parent directories and destination collisions are resolved before changing child paths.
- Cross-surface consistency: Auxiliary indexed metadata does not override exact filesystem state.
- Recovery boundary: Do not broaden a failed exact-path operation into parent- or root-level cleanup.
