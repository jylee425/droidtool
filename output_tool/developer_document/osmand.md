# OsmAnd Developer Document

## App Identity
- Package name: `net.osmand`
- Main app data root: `/data/data/net.osmand`
- App-specific external root: `/data/media/0/Android/data/net.osmand/files`
- State summary: map markers are SQLite-backed; favorites and saved GPX tracks are file-backed; documented profile/settings references are SharedPreferences-backed.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/data/net.osmand/databases/map_markers_db` | Authoritative map-marker store |
| GPX file | `/data/media/0/Android/data/net.osmand/files/favorites/favorites.gpx` | Favorites store |
| GPX file | `/data/data/net.osmand/files/favourites_bak.gpx` | Legacy/backup favorites artifact |
| Directory | `/data/media/0/Android/data/net.osmand/files/tracks/` | Saved GPX track files |
| SharedPreferences XML | `/data/data/net.osmand/shared_prefs/net.osmand.settings.xml` | Settings and profile references |

## DB Entities

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `map_marker` | `map_markers_db`; table `map_markers` | `marker_id`, `marker_lat`, `marker_lon`, `marker_description`, `marker_active`, `marker_added`, `marker_visited`, `group_name`, `group_key`, `marker_color`, `marker_next_key`, `marker_disabled`, `marker_selected`, `marker_map_object_name`, `title` |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| marker group/order state | marker DB tables or relation columns when present | group key/name, ordering/next key, active/history metadata |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| marker history metadata | marker DB rows/columns when present | added/visited/selection state |

### Access Semantics
- Source of truth: `map_markers_db` is authoritative for map markers.
- Entity semantics: Marker identity includes its row id and stored coordinate/name/group metadata.
- Selector semantics: Marker id is primary; an exact name/coordinate pair with explicit coordinate tolerance is alternate.
- Value encoding: Coordinates use decimal degrees; preserve marker active, group, order, color, selected, disabled, and history encodings.
- Relationship invariants: Group/order/history metadata remains associated with the intended marker.

### Access Procedure
- Runtime discovery: Inspect marker tables, columns, primary key, and group/order relations before mutation.
- Read procedure: Query marker rows and apply explicit coordinate tolerance only when coordinate selection is requested.
- Write procedure: Insert or update exact marker rows using compatible discovered columns and defaults.
- Delete procedure: Resolve exact marker rows and remove related group/history rows only when discovered schema relations require it.
- Process/file handling: Quiesce OsmAnd for private DB replacement and preserve consistent sidecars and original file metadata when applicable.

### Consistency / Recovery
- Snapshot consistency: Treat the marker DB and active sidecars as one snapshot.
- Transaction consistency: Marker and required dependent-row mutations complete atomically.
- Relationship consistency: Group/order/history state does not become associated with a different marker.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `osmand_setting` | `net.osmand.settings.xml` and initialized profile XMLs | `application_mode`, `last_used_application_mode`, `external_storage_dir*`; `preferred_locale` when present |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| profile reference | initialized profile/settings XML | profile name/mode and external-storage reference keys |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Access Semantics
- Source of truth: Initialized settings/profile XML provides the current application-mode and storage references.
- Selector semantics: Exact initialized keys select settings; profile keys are build/state dependent.
- Boundary: Do not define writes for `preferred_locale` or other values whose stable mutation semantics are not documented.

### Access Procedure
- Runtime discovery: Parse initialized settings and profile XML structurally and inspect available key/value types.
- Read procedure: Return documented application-mode or storage-reference settings only when their keys are present.

### Consistency / Recovery
- Process consistency: Profile/settings files may be rewritten by the running app and are not interchangeable with marker or GPX state.
- Recovery boundary: Preference state cannot repair marker rows or GPX content.

## Providers

### Primary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | No provider-backed marker/favorite/track interface is documented | Not applicable |

### Secondary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: Do not invent provider state for OsmAnd markers, favorites, or saved tracks.

### Access Procedure
- Not applicable: Use the documented DB, SharedPreferences, and GPX file surfaces.

### Consistency / Recovery
- Boundary: Provider operations cannot substitute for marker DB or GPX lifecycle operations.

## Files

### Primary
| Entity | Path | Fields |
|---|---|---|
| `favorite` | `/data/media/0/Android/data/net.osmand/files/favorites/favorites.gpx` | waypoint name, latitude, longitude, GPX metadata |
| `gpx_track` | `/data/media/0/Android/data/net.osmand/files/tracks/*.gpx` | filename/path, track name, ordered track points, waypoints, timestamps |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| GPX point | owning favorites/track GPX | waypoint or trackpoint coordinates, name/time metadata, order |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| `favorite_backup` | `/data/data/net.osmand/files/favourites_bak.gpx` | backup waypoint entries |
| `map_file` | OsmAnd map data directories | map region files outside favorite/track lifecycle |

### Access Semantics
- Source of truth: Favorites are GPX waypoint elements in the primary favorites file; saved tracks are GPX files under the tracks directory.
- Entity semantics: A favorite is one waypoint; a saved track owns ordered `trk/trkseg/trkpt` elements.
- Selector semantics: Favorites use exact waypoint name plus coordinate tolerance when needed; tracks use exact file path/name.
- Value encoding: Coordinates use decimal degrees; preserve valid GPX XML, namespaces, timestamps, and waypoint/track ordering.
- Relationship invariants: Trackpoints remain ordered within their owning segment/track; backup favorites are auxiliary rather than a second primary store.
- Boundary: Named places require caller-provided coordinates unless a separate geocoder/source is documented.

### Access Procedure
- Runtime discovery: Check file/directory existence, GPX namespace, root element, and destination collision before access.
- Read procedure: Parse waypoint or ordered track elements from the exact selected GPX file.
- Write procedure: Append/create valid waypoint entries in the primary favorites file or create a valid GPX track file under the documented tracks directory.
- Delete procedure: Remove only the exact selected waypoint or track file; do not delete map data or the entire tracks/favorites root.
- Process/file handling: Preserve valid XML structure and write the target GPX artifact atomically when replacing existing content.

### Consistency / Recovery
- Transaction consistency: A GPX mutation leaves either the previous valid document or the complete new valid document.
- Relationship consistency: Ordered trackpoints remain inside the intended parent track and segment.
- Cross-surface consistency: Keep marker DB, SharedPreferences settings, and GPX files as separate state interfaces.
- Recovery boundary: Do not promote the backup favorites file over the primary file without an explicit recovery action.
