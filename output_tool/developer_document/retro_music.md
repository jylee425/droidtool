# Retro Music Developer Document

## App Identity
- Package name: `code.name.monkey.retromusic`
- Main app data root: `/data/data/code.name.monkey.retromusic`
- State summary: songs come from MediaStore; playlists and queue state are mirrored/stored in app-private SQLite databases; UI/playback preferences exist.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| ContentProvider | `content://media/external/audio/media` | Audio library read surface |
| ContentProvider | `content://media/external/audio/playlists` | Platform playlist surface when available |
| ContentProvider | `content://media/external/audio/playlists/<playlist_id>/members` | Platform playlist membership surface when available |
| SQLite DB | `/data/data/code.name.monkey.retromusic/databases/playlist.db` | Retro Music playlist/song mirror store |
| SQLite DB | `/data/data/code.name.monkey.retromusic/databases/music_playback_state.db` | Playing queue store |
| SQLite DB | `/data/data/code.name.monkey.retromusic/databases/blacklist.db` | Auxiliary blacklist state |
| SQLite DB | `/data/data/code.name.monkey.retromusic/databases/history.db` | Auxiliary listening history |
| SQLite DB | `/data/data/code.name.monkey.retromusic/databases/song_play_count.db` | Auxiliary play-count state |
| SharedPreferences XML | `/data/data/code.name.monkey.retromusic/shared_prefs/code.name.monkey.retromusic_preferences.xml` | UI/playback preferences |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `playlist` | `playlist.db`; table `PlaylistEntity` | `playlist_id`/`playlistId`/`id`/`_id`, `playlist_name`/`playlistName`/`name` |
| `playing_queue` | `music_playback_state.db`; table `playing_queue` | `title` when initialized minimally, optional `position`/`play_order`/`queue_position`/`song_key`, playback order fields |
| `song` | `playlist.db`; table `SongEntity` plus MediaStore | `id`/`_id`/`song_id`/`songId`, `title`/`name`, `duration`/`duration_ms`, `playlist_creator_id`/`playlistCreatorId`/`playlist_id`, `track_number`, `year`, `data`, `date_modified`, `album_id`, `album_name`, `artist_id`, `artist_name`, `composer`, `album_artist` |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `playlist_item` | `playlist.db`; `SongEntity` rows associated with playlist id, or platform playlist members | playlist foreign key `playlist_creator_id`/`playlistCreatorId`/`playlist_id`, song id/key, position/order |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `history_entry` | `history.db` or `HistoryEntity` when present | song id/key, timestamp |
| `play_count` | `song_play_count.db` or `PlayCountEntity` when present | song id/key, play count, timestamp |
| `blacklist_entry` | `blacklist.db` | path/song identifier |
| `original_playing_queue` | `music_playback_state.db`; table `original_playing_queue` | original queue ordering |

### Access Semantics
- Source of truth: Read songs from MediaStore and/or mirrored `SongEntity` rows; read playlists from `PlaylistEntity` and playlist membership rows; read queue state from `music_playback_state.db`.
- Entity semantics: Playlists own ordered membership; `playing_queue` is active queue state and `original_playing_queue` is consistency/reference state only when the discovered schema uses it.
- Selector semantics: Media/playlist ids are durable selectors; titles and playlist names are discovery/display selectors and may collide, especially in title-only queue schemas.
- Value encoding: Preserve provider/private membership order and populate discovered queue order/key columns; a title-only queue encodes order by insertion order and has weaker song identity.
- Relationship invariants: Target songs must exist before membership creation; provider playlist state and private `PlaylistEntity`/`SongEntity` mirrors represent one semantic playlist.
- Boundary: Do not guess an undocumented queue-shaped SharedPreferences key or delete underlying audio through playlist/queue relations.

### Access Procedure
- Surface selection: Read library song identity from MediaStore and use documented private databases for Retro Music playlist mirrors and queue state.
- Initialization: Prefer an app-initialized queue/playlist schema; do not create a guessed minimal private database unless its exact schema and ownership are documented.
- Runtime discovery: Inspect playlist, song, queue, identity, and ordering columns before choosing a provider or private-DB mutation path.
- Read procedure: Playlist-name and song-title filters are discovery filters and must not replace stable playlist/media ids for mutation; queue reads preserve stored order. Library discovery returns the stable media ids accepted by playlist and queue mutations, including an empty result when no media has been indexed.
- Write procedure: Resolve requested songs against MediaStore before playlist or queue mutation. An empty song-id list creates or replaces a playlist with no membership rows. Playlist identity exists independently of media membership. Queue replacement with an empty song list clears the queue; append preserves existing entries and adds only resolved songs after them.
- Write procedure: Writing an existing playlist name replaces its ordered membership while retaining or resolving the playlist identity; writing a new name creates the playlist and its ordered membership, including an empty membership list.
- Delete procedure: Membership replacement deletes only the selected playlist's prior member relations, not the underlying MediaStore songs.
- Process/file handling: Stop Retro Music before replacing private DB files and preserve original uid/gid, restrictive mode, and security context.

### Consistency / Recovery
- Snapshot consistency: Copy each private main DB with matching WAL/SHM state or checkpoint first, and remove only sidecars made stale by replacement.
- Relationship consistency: Preserve requested queue order and stable song identity where the discovered schema supports it; title-only schemas have weaker identity and may contain collisions.
- Cross-surface consistency: MediaStore playlists and Retro Music's private mirror are separate consumers of the same semantic state; partial updates must be surfaced or repaired.
- Recovery boundary: Do not silently shrink a requested queue or playlist when a song id cannot be resolved to the representation required by the selected surface.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `retromusic_setting` | `code.name.monkey.retromusic_preferences.xml` | `toggle_volume` boolean |
| `retromusic_setting` | same XML | build-dependent `last_used_tab`, `toggle_add_controls`, and playback/UI preference keys |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: Keep UI/playback preference interfaces separate from playlist/queue DB interfaces.

### Access Procedure
- Write procedure: Parse and write preference XML structurally and preserve unknown keys.

### Consistency / Recovery
- Process consistency: Quiesce the app before external preference replacement to avoid conflicts with in-process cached values.

## Providers
### Primary
| Entity | Provider | Fields |
|---|---|---|
| `song` | `content://media/external/audio/media` | `_id`, `title`, `track`, `year`, `duration`, `_data`, `date_modified`, `album_id`, `album`, `artist_id`, `artist`, `composer`, `album_artist` |
| `playlist` | `content://media/external/audio/playlists` when available | playlist id/name |

### Secondary
| Entity | Provider | Fields |
|---|---|---|
| `playlist_item` | `content://media/external/audio/playlists/<playlist_id>/members` when available | playlist id, audio id, play order |

### Auxiliary
| Entity | Provider | Fields |
|---|---|---|
| `album` | MediaStore album fields | album id/name/artist |
| `artist` | MediaStore artist fields | artist id/name |

### Access Semantics
- Entity semantics: MediaStore songs and playlist members are platform identities; private playlist/song rows are app-owned mirrors where present.
- Selector semantics: Song/media ids are primary selectors for playlist membership; playlist ids are primary selectors for playlist updates. Names are alternate selectors and can be ambiguous.
- Value encoding: Preserve playlist order, relation positions, song metadata mirrored from MediaStore, and playing queue order.
- Relationship invariants: Mirrored `SongEntity` metadata retains matching media title, track, year, duration, path, album, artist, composer, and album-artist fields where columns exist.
- Boundary: Provider and private mirrors must not be treated as unrelated playlists.

### Access Procedure
- Surface selection: Query `content://media/external/audio/media` for library rows and use MediaStore playlist/member surfaces only when the target build exposes them.
- Runtime discovery: Inspect provider availability and projection fields before choosing provider or private-mirror mutation.
- Read procedure: Preserve stable media ids and provider play order when reading songs and playlist membership.
- Write procedure: Use writable MediaStore playlist/member rows for platform state and mirror required playlist/song metadata into the private DB.
- Delete procedure: Membership replacement removes only selected provider membership rows, not audio media rows.

### Consistency / Recovery
- Cross-surface consistency: MediaStore playlist rows and Retro Music private mirrors can become partially updated and must be treated as one semantic playlist operation.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| audio file backing `song` | paths referenced by MediaStore/`SongEntity.data` | path, filename, duration metadata |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| artwork/cache file | app cache/artwork paths if present | image path/cache key |

### Access Semantics
- Boundary: Audio files are backing artifacts for `song`; file operations should not replace MediaStore/internal DB song identity unless file management is the user-visible state being modeled.

### Access Procedure
- Surface selection: Resolve audio file paths through MediaStore or mirrored song metadata before file access.
- Delete procedure: Playlist or queue deletion removes relations only and never deletes the underlying audio file.

### Consistency / Recovery
- Cross-surface consistency: File movement/deletion can invalidate MediaStore and private song metadata and therefore requires a separately modeled media-file lifecycle.
