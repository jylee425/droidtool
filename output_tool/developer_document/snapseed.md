# Snapseed Developer Document

## App Identity
- Package name: `com.niksoftware.snapseed`
- Main app data root: `/data/data/com.niksoftware.snapseed`
- State summary: documented settings are SharedPreferences-backed; input and exported images are file/media artifacts; no confirmed durable DB-backed edit-history model is documented.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SharedPreferences XML | `/data/data/com.niksoftware.snapseed/shared_prefs/Preferences.xml` | Primary documented settings store |
| SharedPreferences XML | `/data/data/com.niksoftware.snapseed/shared_prefs/com.niksoftware.snapseed_preferences.xml` | Alternate settings filename |
| External storage | caller-provided image paths such as `/sdcard/Pictures` | Input and exported image artifacts |
| MediaStore | Android media index | Auxiliary indexed metadata for shared images |

## DB Entities

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | No confirmed Snapseed durable DB entity is documented | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `edit_history_record` | Backing store not confirmed | visible edit-step names such as Original/Tune Image |
| `qr_look` | Backing store not confirmed | QR look create/scan surface |

### Access Semantics
- Boundary: Do not define edit-history or QR-look state interfaces without a durable backing representation.

### Access Procedure
- Not applicable: Use documented preferences and file/media artifacts.

### Consistency / Recovery
- Recovery boundary: Temporary session state cannot be promoted to durable edit history without format evidence.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `snapseed_setting` | `Preferences.xml` or alternate initialized preference file | string `pref_export_setting_long_edge`; boolean `pref_appearance_use_dark_theme`; string `pref_export_setting_compression` |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| export sizing/quality domain | same initialized XML | long-edge numeric string where `0` means original/no resize; compression/quality numeric string, commonly `95`/`100` for PNG-like lossless requests |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Access Semantics
- Source of truth: Use the initialized preference file containing the documented keys.
- Selector semantics: Exact preference keys select settings.
- Value encoding: Dark theme is boolean; export long-edge and compression are string nodes containing documented numeric values.
- Boundary: Do not change the XML node type or infer unrelated Snapseed settings.

### Access Procedure
- Initialization: Use initialized preferences so build-specific keys and defaults exist.
- Runtime discovery: When both candidate files exist, select the one containing documented keys and inspect stored XML types.
- Read procedure: Parse documented user-facing keys structurally.
- Write procedure: Update only requested documented keys, preserve unrelated XML entries, and validate numeric-string domains.
- Process/file handling: Preserve preference-file metadata and use a quiescent process boundary when external replacement is required.

### Consistency / Recovery
- Process consistency: An active process may hold cached preferences and must not race external replacement.
- Recovery boundary: Settings mutation does not create or modify exported image bytes.

## Providers

### Primary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | No provider-backed settings interface is documented | Not applicable |

### Secondary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Provider/URI | Fields |
|---|---|---|
| `media_index_entry` | MediaStore for known input/export images | path/URI, display name, modified time, MIME type, dimensions when available |

### Access Semantics
- Entity semantics: MediaStore rows are auxiliary metadata for image files and do not represent Snapseed settings or edit history.
- Boundary: Do not invent an exact provider URI or provider write contract beyond the documented MediaStore surface.

### Access Procedure
- Runtime discovery: Query only columns exposed by the target Android build when indexed metadata is requested.
- Read procedure: Use provider metadata only for explicitly selected image artifacts.

### Consistency / Recovery
- Cross-surface consistency: Provider metadata remains associated with the same underlying image file.

## Files

### Primary
| Entity | Path | Fields |
|---|---|---|
| `exported_image` | caller-selected Snapseed save/export path under shared storage | exact output path, filename, modified time, size, extension/type |
| `media_file` | caller-provided input path under shared storage | exact input path, filename, metadata |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| edit/session cache file | app-private cache when present | temporary cache path/session data |

### Access Semantics
- Source of truth: Exact shared-storage paths identify input and exported image artifacts.
- Selector semantics: Exact path is primary; recent display-name/time metadata is only an alternate selector and may be ambiguous.
- Boundary: Do not expose temporary cache/session files as durable edit history.

### Access Procedure
- Runtime discovery: Resolve exact path, root containment, existence, type, and available metadata before reading.
- Read procedure: List or read metadata for exact exported-image artifacts under documented shared-storage roots.

### Consistency / Recovery
- Cross-surface consistency: Auxiliary provider metadata must refer to the same exact image artifact.
- Recovery boundary: Do not infer an image export operation or edit-history restoration from cache artifacts.
