# OpenTracks Developer Document

## App Identity
- Package name: `de.dennisguse.opentracks`
- Main app data root: `/data/data/de.dennisguse.opentracks`
- State summary: recorded tracks are SQLite-backed; documented display/unit settings are SharedPreferences-backed.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/data/de.dennisguse.opentracks/databases/database.db` | Authoritative recorded-track store |
| SharedPreferences XML | `/data/data/de.dennisguse.opentracks/shared_prefs/de.dennisguse.opentracks_preferences.xml` | User-facing units and layout settings |

## DB Entities

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `track` | `database.db`; table `tracks` | `_id`, `name`, `description`, `category`, `activity_type`, `starttime`, `stoptime`, `totaldistance`, `totaltime`, `movingtime`, `avgspeed`, `avgmovingspeed`, `maxspeed`, `elevationgain`, `elevationloss`, `numpoints` |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `trackpoint` | `database.db`; table `trackpoints` | track id, latitude, longitude, altitude, timestamp, sensor values, ordering fields |
| `marker` | `database.db`; table `markers` | track id, position, marker type, name/description |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| track statistics | track/statistics columns or related tables when present | distance, time, speed, elevation, point count |

### Access Semantics
- Source of truth: `tracks` represents recorded-track identity and metadata; trackpoints and markers are dependent detail for a parent track.
- Entity semantics: A track owns ordered geometry/detail rows and may have associated marker rows.
- Selector semantics: Track id is primary; name and start time are alternate selectors and may be ambiguous.
- Value encoding: Preserve coordinate precision, timestamp scale, activity type, stored distance/speed/elevation units, and point ordering.
- Relationship invariants: Trackpoints and markers retain the owning track id and their documented order.
- Boundary: Do not model dependent geometry rows as unrelated primary tracks.

### Access Procedure
- Surface selection: Keep recorded-track DB operations separate from preference settings.
- Runtime discovery: Inspect actual track, trackpoint, marker, statistics, key, and relation columns before access.
- Read procedure: Resolve a parent track first, then query its ordered points, markers, and statistics when details are requested. An initialized store may contain no track rows, in which case listing returns an empty collection. Detail lookup uses a stable id from an existing track row; an unknown id produces an explicit not-found result.
- Write procedure: Track creation inserts a parent track using the discovered required columns and schema defaults and returns its stable id. The initialized schema permits a track row with no associated trackpoints or markers. Metadata updates change only documented track fields and preserve dependent geometry rows. Update and deletion select an existing row by its stable track id.
- Delete procedure: Resolve an exact track id and remove dependent rows according to discovered foreign-key or relation behavior without affecting other tracks.
- Process/file handling: Quiesce the app for private DB replacement and preserve a consistent DB/sidecar snapshot plus original file metadata when replacement is required.

### Consistency / Recovery
- Snapshot consistency: Treat the main DB and active WAL/SHM sidecars as one snapshot.
- Transaction consistency: Parent metadata and dependent-row deletion are atomic when one lifecycle action spans them.
- Relationship consistency: No trackpoint or marker is reassigned to, or orphaned from, the wrong parent track.
- Recovery boundary: Do not reconstruct missing geometry from aggregate statistics.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `opentracks_setting` | `de.dennisguse.opentracks_preferences.xml` | `statsUnits` with documented values such as `IMPERIAL` and `METRIC` |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| custom statistics layout | same XML | `statsCustomLayoutFieldsKey` when initialized |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Access Semantics
- Source of truth: Initialized preference XML stores documented user-facing units and layout settings.
- Value encoding: `statsUnits` uses the app-defined unit-domain strings; custom layout content remains key-specific.
- Boundary: Keep units/layout settings separate from recorded-track rows.

### Access Procedure
- Runtime discovery: Read initialized keys and XML value types before exposing optional settings.
- Read procedure: Parse documented settings structurally.
- Write procedure: Update only documented keys and preserve unrelated XML entries; reject values outside documented domains.
- Process/file handling: Use a quiescent process boundary when replacing XML externally.

### Consistency / Recovery
- Process consistency: External XML replacement must not race an active process holding cached preferences.
- Recovery boundary: A preference mutation cannot alter recorded track geometry or metadata.

## Providers

### Primary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | No provider-backed OpenTracks state interface is documented | Not applicable |

### Secondary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: Do not invent provider CRUD for private OpenTracks state.

### Access Procedure
- Not applicable: Use the SQLite and SharedPreferences surfaces.

### Consistency / Recovery
- Boundary: Provider state cannot substitute for the recorded-track database.

## Files

### Primary
| Entity | Path | Fields |
|---|---|---|
| None | Recorded tracks are represented by the DB interface | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| exported track file | GPX/KML/CSV export path when generated | exact path, format, exported track content |

### Access Semantics
- Entity semantics: Export files are derived artifacts and do not replace the DB track identity.
- Boundary: Do not infer export creation or deletion lifecycle behavior without a documented file interface.

### Access Procedure
- Read procedure: Use exact paths only for an explicitly modeled exported-file action.

### Consistency / Recovery
- Recovery boundary: An export file cannot be used to overwrite live track state unless an import interface is separately documented.
