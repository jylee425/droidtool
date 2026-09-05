# VLC Developer Document

## App Identity
- Package name: `org.videolan.vlc`
- Main app data root: `/data/data/org.videolan.vlc`
- State summary: media and playlist metadata are stored in version-dependent private SQLite databases; playback/UI preferences are SharedPreferences-backed; media bytes may live in shared storage.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/data/org.videolan.vlc/app_db/vlc_media.db` | Candidate VLC media database |
| SQLite DB | `/data/data/org.videolan.vlc/app_vlc/vlc_media.db` | Version-dependent candidate media database |
| SQLite DB | `/data/data/org.videolan.vlc/databases/vlc_media.db` | Version-dependent candidate media database |
| SQLite DB | `/data/data/org.videolan.vlc/databases/vlc_database` | Version-dependent candidate media database |
| SharedPreferences XML | `/data/data/org.videolan.vlc/shared_prefs/org.videolan.vlc_preferences.xml` | VLC UI/playback preferences |
| External storage | media paths referenced by VLC `Media`/`File` rows | Audio/video artifacts |
| External storage | `/storage/emulated/0/VLCVideos` | Example shared media directory |

## DB Entities

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `media` | VLC media DB; `Media`, `File` | `Media.id_media`, type, duration, title, filename, presence/import fields; `File.media_id`, `mrl`, file/source flags |
| `playlist` | VLC media DB; `Playlist` | `id_playlist` or `id`, name, optional creation date |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `playlist_item` | `PlaylistMediaRelation` or `playlist_media` | playlist id, media id, position/order |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `bookmark` | `Bookmark` when present | media id, time offset, title |
| `subtitle_track` | `SubtitleTrack` when present | media id, subtitle path/language |
| `media_setting` | `Settings` when present | media-specific playback metadata |

### Access Semantics
- Source of truth: Playlist identity comes from `Playlist`; ordered membership comes from the discovered playlist-media relation; media identity/path comes from `Media` and `File`.
- Entity semantics: A playlist owns ordered membership rows but does not own or delete the underlying media rows/files.
- Selector semantics: Playlist id and media id/MRL are primary; playlist/media names are alternate and may be ambiguous.
- Value encoding: Preserve relation order/position and exact stored MRL/path representation, duration, and presence metadata.
- Relationship invariants: Every membership row references an existing playlist and media row; positions remain deterministic and collision-free.
- Boundary: Do not infer membership from filenames alone or treat shared files as VLC media until compatible media/file identity is resolved.

### Access Procedure
- Surface selection: Use the discovered VLC media DB for library/playlist state and SharedPreferences for UI/playback settings.
- Runtime discovery: Select a documented DB candidate by SQLite header and VLC-shaped tables; inspect table/column/relation variants before access.
- Read procedure: Media discovery reads compatible stable media ids from joined media/file rows and returns an empty collection when the initialized library contains no media. Playlist reads join ordered membership rows and media/file rows only through discovered ids.
- Write procedure: Playlist creation uses discovered required/default columns. A playlist row may exist with no membership rows, so its create/read/delete lifecycle is independent of media presence. Membership insertion requires an existing compatible media/file row and assigns its documented order. No media creation or import operation is documented for this state surface.
- Delete procedure: Playlist deletion removes the playlist and its membership rows but not media library rows or media files.
- Process/file handling: Quiesce VLC for private DB replacement and preserve a consistent DB/sidecar snapshot and original file metadata when replacement is required.

### Consistency / Recovery
- Snapshot consistency: Keep the selected DB and active WAL/SHM state from one snapshot.
- Transaction consistency: Playlist and membership changes complete atomically when one action spans both.
- Relationship consistency: Membership rows never reference missing playlist/media ids and preserve their intended order.
- Process consistency: An active VLC process must not race external DB replacement.
- Recovery boundary: Do not rebuild missing VLC media identity from a filename alone.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `vlc_setting` | `org.videolan.vlc_preferences.xml` | `video_hud_timeout_in_s`; `app_theme` and documented playback/UI keys when initialized |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Source of truth: Initialized VLC preference XML stores documented UI/playback settings.
- Selector semantics: Exact preference key selects a setting.
- Boundary: Keep settings separate from media/playlist DB state and do not infer writes for undocumented keys or value domains.

### Access Procedure
- Runtime discovery: Read initialized keys and XML types before exposing optional settings.
- Read procedure: Parse documented user-facing settings structurally.
- Write procedure: Update only documented keys with established value domains and preserve unrelated XML entries.
- Process/file handling: Use a quiescent process boundary when replacing XML externally.

### Consistency / Recovery
- Process consistency: External XML replacement must not race VLC's cached preference state.
- Recovery boundary: Preferences cannot create media rows or playlist membership.

## Providers

### Primary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | VLC playlist state uses its private media DB | Not applicable |

### Secondary
| Entity | Provider/URI | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Provider/URI | Fields |
|---|---|---|
| MediaStore media entry | Android MediaStore for shared media metadata | path/URI, title, MIME type, duration when available |

### Access Semantics
- Entity semantics: MediaStore metadata may help identify a shared media artifact but does not define VLC playlist membership.
- Boundary: Do not invent provider-backed playlist CRUD.

### Access Procedure
- Read procedure: Query provider metadata only for an explicitly selected shared media artifact when needed for media identity resolution.

### Consistency / Recovery
- Cross-surface consistency: VLC media/file ids remain the authoritative playlist relation keys.

## Files

### Primary
| Entity | Path | Fields |
|---|---|---|
| media file backing `media` | exact path/MRL referenced by VLC `Media`/`File` rows | path/MRL, title/name, MIME/type, duration metadata |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| subtitle file | path referenced by subtitle rows | subtitle path/language |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| thumbnail/cache file | VLC cache paths when present | cached derivative path |

### Access Semantics
- Entity semantics: Media and subtitle files are artifacts referenced by VLC DB identity rows.
- Selector semantics: Exact referenced path/MRL is primary; filename alone is insufficient when ambiguous.
- Boundary: Thumbnail/cache files are not authoritative playlist or media-library state.

### Access Procedure
- Surface selection: Resolve file artifacts through compatible VLC media/file rows before playlist membership writes.
- Read procedure: Access exact referenced media/subtitle paths only for documented file operations.

### Consistency / Recovery
- Relationship consistency: Playlist membership remains linked to VLC media ids rather than cache or filename heuristics.
- Recovery boundary: Deleting a playlist never implies deleting its media files.
