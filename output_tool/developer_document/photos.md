# Photos Developer Document

## App Identity
- Package name: external media/file surface used by Photos-like workflows
- State summary: user-visible media artifacts are shared-storage files; MediaStore supplies indexed metadata without becoming a private Photos-app database.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| External storage | `/sdcard/Download` | Common image/media directory |
| External storage | `/sdcard/<folder>` | Caller-selected media directory |
| MediaStore | Android media index | Indexed media metadata |

## DB Entities

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | No private DB-backed Photos lifecycle is documented | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: Do not invent a private Photos database for shared media-file state.

### Access Procedure
- Not applicable: Use filesystem paths and documented MediaStore metadata.

### Consistency / Recovery
- Boundary: Database replacement cannot substitute for media file operations.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | No preferences-backed media lifecycle is documented | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: SharedPreferences are not an authoritative media-file store.

### Access Procedure
- Not applicable: Use the Providers and Files surfaces.

### Consistency / Recovery
- Boundary: Preference state cannot create, move, or delete media artifacts.

## Providers

### Primary
| Entity | Provider/URI | Fields |
|---|---|---|
| `media_index_entry` | MediaStore for indexed shared media | id/URI, display name, path or relative path, MIME/media type, size, modified/captured time, bucket id/name |

### Secondary
| Entity | Provider/URI | Fields |
|---|---|---|
| `album_bucket` | MediaStore bucket fields | bucket id/name and member media entries |

### Auxiliary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Access Semantics
- Entity semantics: A MediaStore row describes one indexed shared-media file; a bucket groups entries without owning their bytes.
- Selector semantics: Provider id/URI is primary for indexed rows; display name plus bucket/path is alternate and can be ambiguous.
- Relationship invariants: Indexed metadata refers to the same underlying file and does not create a second media identity.
- Boundary: Do not mutate private photo-app caches as authoritative media state.

### Access Procedure
- Runtime discovery: Query columns available on the target Android build and use only documented metadata fields.
- Read procedure: Query indexed metadata for explicitly requested media listings or known file artifacts.

### Consistency / Recovery
- Cross-surface consistency: Filesystem paths remain authoritative for byte-moving/deletion actions while provider rows describe indexed metadata.
- Recovery boundary: Missing provider metadata does not authorize deletion of an unrelated file with a similar display name.

## Files

### Primary
| Entity | Path | Fields |
|---|---|---|
| `media_file` | `/sdcard/Download` or `/sdcard/<folder>` | exact path, filename, media type/extension, size, modified time, bytes |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| media folder | caller-selected shared-storage folder | exact path, direct media children |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| thumbnail/cache file | app/media cache when present | derivative path outside authoritative media lifecycle |

### Access Semantics
- Source of truth: Exact shared-storage path identifies the media artifact operated on by file lifecycle actions.
- Selector semantics: Exact path is primary; display name plus bucket/folder is alternate and must resolve unambiguously.
- Value encoding: Move operations preserve file bytes and extension/MIME assumptions.
- Boundary: Reject path traversal, root escape, ambiguous display-name deletion, and direct cache mutation.

### Access Procedure
- Runtime discovery: Resolve exact path, existence, file type, root containment, and destination collision before mutation.
- Read procedure: List or return metadata for exact media files under the selected root.
- Write procedure: Move media only between explicit source and destination paths within documented roots.
- Delete procedure: Delete only the exact selected file; do not broaden deletion to a folder, bucket, thumbnail, or cache set.
- Process/file handling: Apply containment and collision rules to both source and destination.

### Consistency / Recovery
- Transaction consistency: A move leaves either the original source or the complete destination artifact, not a partial copy presented as complete.
- Cross-surface consistency: Provider metadata cannot override exact filesystem identity.
- Recovery boundary: Do not infer a restore operation from thumbnails or cached derivatives.
