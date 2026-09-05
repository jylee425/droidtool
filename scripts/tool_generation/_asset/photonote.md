# PhotoNote Developer Document

## App Identity
- Package name: `com.chartreux.photo_note`
- Main app data root: `/data/data/com.chartreux.photo_note`
- State summary: users, posts, comments, media, follows, likes, and bookmarks are stored in an app-private SQLite database.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/data/com.chartreux.photo_note/databases/PhotoNote.db` | Authoritative social graph/content store |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `user` | `PhotoNote.db`; table `users` | `id`, `name`, `user_name`, `post_count`, profile fields |
| `post` | `PhotoNote.db`; table `posts` | `id`, `user_id`, `text`, `liked_user_name`, `likes_count`, `comments_count`, `created_at`, `updated_at` |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `comment` | `PhotoNote.db`; table `comments` | `id`, `user_id`, `post_id`, `text`, `created_at`, `updated_at` |
| `media` | `PhotoNote.db`; table `media` | media id, uri/path/type metadata |
| `follow` | `PhotoNote.db`; table `follows` | follower id, followed user id, timestamp/status |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `like` | `PhotoNote.db`; table `likes` | user id, post id, timestamp |
| `bookmark` | `PhotoNote.db`; table `bookmarks` | user id, post id, timestamp |
| `media_post` | `PhotoNote.db`; table `media_posts` | post id, media id relation |

### Access Semantics
- Source of truth: Read users, posts, comments, media, follows, likes, and bookmarks from `PhotoNote.db`, not from rendered feed UI alone.
- Entity semantics: Users own posts; comments, likes, bookmarks, media, and follows are dependent content or relation state.
- Selector semantics: Use DB primary keys for users, posts, comments, likes, and follows. User name/post text selectors are alternate and should resolve ids before mutation.
- Value encoding: Preserve timestamp format, counter representation, and like/follow uniqueness encoding.
- Relationship invariants: User/post/comment/media foreign keys must resolve exact parents; denormalized counts such as `users.post_count`, likes, comments, follows, and bookmarks remain consistent with relation rows.
- Boundary: Never substitute an arbitrary user or post for an invalid requested parent id.

### Access Procedure
- Runtime discovery: Inspect `users`, `posts`, `comments`, `likes`, and `follows` columns, including counters, timestamps, constraints, and required parent ids.
- Read procedure: Read users, posts, and comments with their stable ids, parent ids, text, counters, and timestamps; do not infer relations from rendered feed order. Unfiltered user/post reads and searches remain meaningful when the initialized database contains no rows.
- Write procedure: Resolve required parent ids before mutation and use one transaction when rows and denormalized counters change together. A new user row has a unique `user_name`, documented profile defaults, zeroed relation/content counters, and a returned stable id. Post, comment, like, bookmark, and follow rows reference existing user and parent-content ids through their documented foreign-key fields.
- Process/file handling: For whole-DB replacement, stop the app, install a consistent snapshot, and preserve the original database uid/gid and restrictive mode.

### Consistency / Recovery
- Snapshot consistency: Use SQLite busy handling for in-place access; if the DB cannot be quiesced, checkpoint or replace from one consistent snapshot rather than mixing an old main DB with a newer WAL. Stale journal/WAL/SHM files must not survive beside a self-contained replacement DB.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Preferences are not the PhotoNote content store | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: Do not use SharedPreferences as the source of PhotoNote user, post, comment, media, or follow state.

### Access Procedure
- Not applicable: PhotoNote content access uses `PhotoNote.db`.

### Consistency / Recovery
- Boundary: Preference changes cannot repair content rows, relations, or aggregate counters.

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
- Boundary: No provider-backed PhotoNote content API is documented for the documented state interface.

### Access Procedure
- Not applicable: PhotoNote content access uses `PhotoNote.db`.

### Consistency / Recovery
- Boundary: Do not invent provider rows for private PhotoNote content.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| None | Content state is represented by DB rows | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| media file artifact | paths referenced by `media` rows if present | uri/path/type |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| cached image/media | app-private cache paths if present | cached filename/path |

### Access Semantics
- Boundary: Media files are secondary only when linked by DB `media` rows; cached media should not be defined as standalone durable content.

### Access Procedure
- Surface selection: Resolve media paths through the owning PhotoNote metadata row before file access.
- Delete procedure: Cached media is not an independent content deletion target; owned media deletion requires an explicitly modeled relation lifecycle.

### Consistency / Recovery
- Relationship consistency: Durable media metadata and owned files must not be separated by partial mutation; cache loss alone does not delete content identity.
