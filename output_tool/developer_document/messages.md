# Messages Developer Document

## App Identity
- Package name: Android Telephony/SMS provider surface
- Provider package: `com.android.providers.telephony`
- State summary: SMS messages and conversations are provider-backed system state, not private state of a single SMS UI package.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| ContentProvider | `content://sms` | SMS message read surface |
| ContentProvider | `content://sms/sent` | Sent-message insert surface when permitted |
| SQLite DB | `/data/data/com.android.providers.telephony/databases/mmssms.db` | Root/ADB fallback provider database |
| ContentProvider | `content://com.android.contacts/data` | Contact lookup helper for display names/phone rows |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Direct DB editing is fallback only; prefer provider | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Direct DB editing is fallback only; prefer provider | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| `sms_provider_row` | `mmssms.db`; table `sms` | `_id`, `thread_id`, `address`, `date`, `date_sent`, `read`, `seen`, `status`, `type`, `body`, `protocol` |

### Access Semantics
- Source of truth: `mmssms.db` is a controlled fallback representation; the SMS ContentProvider remains the preferred state surface.
- Entity semantics: The fallback `sms` row represents the same message fields exposed by the provider, including direction, timestamps, read/seen, status, address, and body.
- Boundary: Do not touch MMS tables or unrelated Telephony state unless explicitly modeled.

### Access Procedure
- Surface selection: Prefer SMS ContentProvider reads/writes over direct Telephony DB edits.

### Consistency / Recovery
- Process consistency: Direct Telephony DB access must account for provider concurrency and WAL state; do not construct a replacement from an incomplete snapshot.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Preferences are not the SMS lifecycle store | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: Do not use messaging UI preferences as the source of SMS message or conversation state.

### Access Procedure
- Not applicable: SMS lifecycle access uses the Telephony provider or controlled database fallback.

### Consistency / Recovery
- Boundary: Preference changes cannot repair message, thread, or delivery state.

## Providers
### Primary
| Entity | Provider | Fields |
|---|---|---|
| `sms_message` | `content://sms`, `content://sms/sent` | `_id`, `address`, `body`, `date`, `date_sent`, `type`, `read`, `seen`, `status`, `protocol` |

### Secondary
| Entity | Provider | Fields |
|---|---|---|
| `conversation` | Telephony thread/message grouping through SMS provider fields | thread id, participant address, latest message, message count |

### Auxiliary
| Entity | Provider | Fields |
|---|---|---|
| `participant_contact` | ContactsProvider lookup | display name, normalized phone number |

### Access Semantics
- Source of truth: Read recent messages from `content://sms` with stable fields such as `_id`, `address`, `date`, `type`, and `body`.
- Entity semantics: `content://sms/sent` represents locally recorded sent-message rows when provider insertion is permitted.
- Selector semantics: SMS `_id` is the durable row selector, and a successful provider insert returns the `_id` of the inserted row for read-after-write, status mutation, and deletion on that same provider surface. Phone number, contact name, and conversation grouping are alternate selectors that require provider-compatible normalization and may match multiple rows.
- Value encoding: SMS direction/status is encoded through Telephony `type`, `read`, `seen`, `date`, and `date_sent` fields; phone numbers should be normalized for matching but preserved for insertion.
- Relationship invariants: Contact lookup is a reference operation; provider thread grouping derives from coherent address, date, type, and status fields without mutating ContactsProvider state.
- Boundary: A locally recorded sent row does not claim carrier delivery, and contact mutation is outside the SMS persistence interface.

### Access Procedure
- Surface selection: Use SMS provider query/insert surfaces first; preserve Telephony column expectations in controlled DB fallback and avoid MMS tables unless explicitly modeled.
- Runtime discovery: Inspect available provider projections and resolve display names to unambiguous normalized phone-number candidates; permit Telephony DB fallback only when provider access is unavailable in the controlled environment.
- Read procedure: Provider reads sort returned rows by descending message date; controlled DB fallback uses deterministic descending stored identity when provider access fails. A bounded limit constrains SMS rows rather than conversation groups.
- Write procedure: A sent-row insertion records local sent-message state only and does not claim radio delivery. The inserted provider URI identifies the row for immediate provider readback, update, and deletion without switching to an independently observed storage surface.
- Delete procedure: Delete only the SMS provider row selected by its durable `_id`. A positive affected-row count represents a removed row; process completion alone does not establish deletion. When the provider does not expose a reliable affected-row count, the row's presence on the same provider surface remains authoritative.

### Consistency / Recovery
- Snapshot consistency: Database fallback requires an atomic transaction or quiescent provider context with consistent WAL state.
- Relationship consistency: Provider-managed thread grouping and indexes are derived from message rows; fallback writes must preserve the fields used by that grouping.
- Cross-surface consistency: One lifecycle operation uses a coherent provider or controlled-DB surface for its write and readback. An empty alternate-selector query does not by itself prove that a row returned by `_id` insertion is absent.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| MMS attachment file | Telephony provider attachment paths if MMS support is added | file/content URI, MIME type |

### Access Semantics
- Boundary: No file-backed SMS lifecycle is documented for the documented state interface; MMS attachments require separate provider/file evidence before interface definition.

### Access Procedure
- Not applicable: SMS operations do not access attachment files; a future MMS interface must resolve provider attachment metadata first.

### Consistency / Recovery
- Recovery boundary: Do not infer or delete MMS attachment files without a documented provider-owned message/part relationship.
