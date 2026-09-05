# Wikipedia Developer Document

## App Identity
- Package name: `org.wikipedia`
- Main app data root: `/data/data/org.wikipedia`
- State summary: user-facing settings are SharedPreferences-backed; reading lists, saved pages, search history, and history entries are stored in the app-private Wikipedia SQLite database after initialization.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SQLite DB | `/data/data/org.wikipedia/databases/wikipedia.db` | Reading lists, list pages, search/history/offline metadata |
| SharedPreferences XML | `/data/data/org.wikipedia/shared_prefs/org.wikipedia_preferences.xml` | User-facing settings and feed customization |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `reading_list` | `wikipedia.db`; table `ReadingList` | id, listTitle, description, mtime, atime, sizeBytes, dirty, remoteId |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| `reading_list_page` | `wikipedia.db`; table `ReadingListPage` | id, listId, wiki, namespace, displayTitle, apiTitle, description, offline/status, sizeBytes, lang, revId, remoteId |
| `saved_article` | `wikipedia.db`; `ReadingListPage` rows with offline/status fields and related offline metadata | title, wiki/lang, list id, offline status/size |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `search_history_entry` | `wikipedia.db`; table `RecentSearch` | text, timestamp |
| `history_entry` | `wikipedia.db`; table `HistoryEntry` | authority, lang, title fields, timestamp, source, timeSpentSec, description |
| `offline_object` | `wikipedia.db`; table `OfflineObject` when present | page/offline metadata |
| `page_image` | `wikipedia.db`; table `PageImage` when present | page/image URL metadata |

### Access Semantics
- Source of truth: Read reading lists from `ReadingList` and list membership/saved pages from `ReadingListPage`, not from visible Saved tab text alone.
- Entity semantics: `ReadingList` represents list identity; `ReadingListPage` represents membership and saved-article state; search/history rows are auxiliary unless explicitly modeled.
- Selector semantics: Reading-list id and page id are primary selectors; title/wiki/lang selectors are alternate and should resolve exact ids before mutation.
- Relationship invariants: Saved-article state uses `ReadingListPage` plus dependent offline/status/size fields and `OfflineObject`; list membership changes retain exact list/page linkage.

### Access Procedure
- Surface selection: Keep reading-list/history DB operations separate from SharedPreferences settings.
- Runtime discovery: Inspect `ReadingList`, `ReadingListPage`, `OfflineObject`, `RecentSearch`, and `HistoryEntry` columns before DB mutation.

### Consistency / Recovery
- Snapshot consistency: When accessing pulled database state, keep the main `wikipedia.db` and any WAL/SHM sidecars from one consistent snapshot.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| `wikipedia_setting` | `org.wikipedia_preferences.xml` | boolean `showLinkPreviews`, `collapseTables`, `readingFocusModeEnabled`, `matchSystemTheme`, `imageDimming`, `downloadOnlyOverWiFi`; integer `textSizeMultiplier`; strings `readingFontFamily`, `appTheme`, `imageDownloadQuality` |
| `wikipedia_setting_domain` | same XML keys | themes `Light`, `Sepia`, `Dark`, `Black`; fonts `sans-serif`, `serif`; image quality `low`, `medium`, `high`; text size step where `0` is 100% and each step is 10 percentage points |
| `feed_card_setting` | `<string name="feedCardsEnabled">[true,true,false,...]</string>` | 10-element JSON-style boolean vector; legacy `feed_state` may be present |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not documented separately | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| Legacy feed preference | `org.wikipedia_preferences.xml`; `feed_state` | Superseded feed customization representation when present |

### Access Semantics
- Source of truth: Wikipedia user-facing settings and feed customization use initialized `org.wikipedia_preferences.xml` keys with documented value domains.
- Entity semantics: `feedCardsEnabled` represents the complete ten-topic feed-card vector, not a scalar flag.
- Selector semantics: Preference keys select individual settings; feed topic names or indices select positions within the complete vector.
- Value encoding: Feed card vector positions are: 0 `featured_article`, 1 `top_read`, 2 `picture_of_the_day`, 3 `because_you_read`, 4 `in_the_news`, 5 `on_this_day`, 6 `randomizer`, 7 `today_on_wikipedia`, 8 `suggested_edits`, 9 `accessibility`.
- Value encoding: `feedCardsEnabled` is a 10-element JSON-style boolean array string. `textSizeMultiplier` uses app-specific integer steps where 0 represents 100%.
- Relationship invariants: `feed_cards_enabled` replaces the complete vector; topic/index helpers must declare whether unrequested positions are preserved or reset enabled.
- Boundary: `feedCardsEnabled` is not a single boolean; feed customization is stored as an array of exactly 10 boolean entries.

### Access Procedure
- Initialization: Initialize Wikipedia before the first preference mutation so build-specific defaults and preference keys exist, then quiesce the app process before replacing SharedPreferences XML.
- Runtime discovery: Read initialized preference keys and their XML types before update.
- Read procedure: Parse typed XML values structurally, decode `feedCardsEnabled` only as a valid ten-boolean vector, and keep internal/runtime preferences separate from user-facing settings.
- Write procedure: Parse XML structurally, preserve unrelated keys, remove or supersede legacy `feed_state`, and encode `feedCardsEnabled` as the documented string vector.
- Process/file handling: Prefer updating the initialized preference file without replacing its filesystem identity when supported; if replacement is required, treat every privileged filesystem operation as fallible and preserve the original owner, mode, and SELinux context.

### Consistency / Recovery
- Process consistency: An active Wikipedia process can retain cached preferences and later overwrite externally replaced XML; preference replacement requires a quiescent process boundary.
- Process consistency: Initialization and migration may rewrite defaults during startup, so mutation must be based on initialized rather than pre-initialization preference state.
- Recovery boundary: A partial preference replacement or metadata-restoration failure is not success; retain or restore the original valid preference state.

## Providers
### Primary
| Entity | Provider | Fields |
|---|---|---|
| None | No exposed local provider documented for these interfaces | Not applicable |

### Secondary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Provider | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Access Semantics
- Boundary: No exposed local provider is documented for local Wikipedia reading-list/settings state.

### Access Procedure
- Not applicable: Use the private database and SharedPreferences surfaces.

### Consistency / Recovery
- Boundary: Do not invent a provider-backed write path for private Wikipedia state.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| None | Reading-list/settings state is represented by DB/prefs state | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| offline article artifact | app-private offline/cache files if referenced by DB | file path/cache key/size |

### Access Semantics
- Entity semantics: Offline article/cache files are dependent artifacts whose saved-article identity and status come from DB metadata.
- Relationship invariants: Each managed offline artifact remains associated with its owning saved-article metadata.
- Boundary: Do not model direct cache-file mutation as saved-article lifecycle state.

### Access Procedure
- Surface selection: Resolve offline artifacts through their database metadata before any file access.
- Delete procedure: Do not delete cache/offline files independently of the saved-article lifecycle unless a file-specific interface is documented.

### Consistency / Recovery
- Relationship consistency: Offline artifacts and their DB ownership/status metadata must not be separated by partial mutation.
