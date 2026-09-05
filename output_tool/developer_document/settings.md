# Settings Developer Document

## App Identity
- Package name: Android system SettingsProvider and shell/service interfaces
- State summary: System, secure, global, provider-backed, and service-backed device state are the documented settings surfaces; private Settings app files are outside this state interface.

## Backing Resources
| Type | Resource | Role |
|---|---|---|
| SettingsProvider | `content://settings/system` | System namespace settings |
| SettingsProvider | `content://settings/secure` | Secure namespace settings |
| SettingsProvider | `content://settings/global` | Global namespace settings |
| Shell command | `settings get/put <namespace> <key>` | SettingsProvider read/write CLI |
| Shell service | `svc wifi`, Bluetooth manager commands, `media volume`, notification commands, package/storage/permission commands | Service-backed settings and app-management state |

## DB Entities
### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Do not target raw SettingsProvider private DB files | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Do not target raw SettingsProvider private DB files | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| provider row | SettingsProvider private DB behind `settings` CLI/provider | namespace, key, value |

### Access Semantics
- Boundary: Do not edit raw SettingsProvider private DB files directly; use `settings`, provider APIs, or service commands.

### Access Procedure
- Not applicable: Use SettingsProvider and owning services rather than raw private database access.

### Consistency / Recovery
- Boundary: Raw SettingsProvider database replacement is outside the documented recovery surface.

## SharedPreferences

### Primary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Private Settings app preferences are not the system settings store | Not applicable |

### Secondary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Auxiliary
| Entity | Backing representation | Fields |
|---|---|---|
| None | Not applicable | Not applicable |

### Access Semantics
- Boundary: Private Settings app SharedPreferences are UI/runtime state, not the authoritative system setting store.

### Access Procedure
- Not applicable: Do not use private Settings app preferences for the documented system-state interfaces.

### Consistency / Recovery
- Boundary: Recovery must operate through SettingsProvider or the owning system service, not by repairing private Settings UI preferences.

## Providers
### Primary
| Entity | Provider/service | Fields |
|---|---|---|
| `settings_snapshot` | SettingsProvider namespaces `system`, `secure`, `global` | namespace/key/value entries for `wifi_on`, `airplane_mode_on`, `dark_mode`, `ui_night_mode`, `screen_brightness`, `mode_ringer_streams_affected`, `volume_alarm`, `volume_music`, `volume_ring`, `volume_voice` |
| `wifi_state` | Wi-Fi service with SettingsProvider mirror | enabled, `global.wifi_on` |
| `airplane_mode_state` | SettingsProvider plus `android.intent.action.AIRPLANE_MODE` broadcast | enabled, `global.airplane_mode_on`, broadcast state |
| `theme_state` | UI mode service and SettingsProvider | enabled, `secure.dark_mode`, `secure.ui_night_mode` |
| `brightness_state` | SettingsProvider | percent `0..100`, raw value `0..255`, brightness mode |
| `audio_stream_state` | Audio service with SettingsProvider mirrors where available | stream name, stream id, level, maximum level, percent; supported streams: alarm, media, ring, call, notification |
| `call_vibration_state` | SettingsProvider | enabled, `system.vibrate_when_ringing`, `system.vibrate_on`, `system.ring_vibration_intensity` |

### Secondary
| Entity | Provider/service | Fields |
|---|---|---|
| `setting_entry` | SettingsProvider namespaces `system`, `secure`, `global` | namespace, key, raw value; build-specific availability and encoding |

### Auxiliary
| Entity | Provider/service | Fields |
|---|---|---|
| `bluetooth_state` | Bluetooth manager/service | enabled state; command support varies by build |
| `display_setting` | SettingsProvider/display service | `system.accelerometer_rotation`, `system.screen_off_timeout`, `system.font_scale` |
| `accessibility_state` | SettingsProvider/accessibility service | `secure.enabled_accessibility_services`, `secure.accessibility_enabled`, explicit service component |
| `notification_policy` | notification service/SettingsProvider | `global.zen_mode`, policy/access state |
| `app_management_state` | package/permission/storage services | package, enabled/installed state, permissions, app-ops, cache/storage state |

### Access Semantics
- Source of truth: Read provider-owned values with `settings get <namespace> <key>` and return an absent `null` value as no value rather than the literal string `"null"`.
- Entity semantics: A settings snapshot contains individually selected fully qualified `namespace.key` entries. Wi-Fi, airplane mode, UI mode, brightness, audio, and call vibration may be service-owned state with provider mirrors.
- Selector semantics: A raw setting is selected by the pair `(namespace, key)`, where namespace is restricted to `system`, `secure`, or `global`. Semantic operations expose bounded user-facing fields instead of accepting arbitrary keys.
- Value encoding: Boolean provider values use `1`/`0`; brightness maps clamped percent to raw `0..255`; audio stream ids are call `0`, ring `2`, media `3`, alarm `4`, notification `5`; call vibration compatibility spans `vibrate_when_ringing`, `vibrate_on`, and `ring_vibration_intensity`.
- Relationship invariants: Wi-Fi uses `global.wifi_on` as a provider mirror; airplane mode couples its global key with the matching broadcast; UI mode may use `dark_mode` and `ui_night_mode`; service-owned and provider-mirror state must refer to the same semantic setting.
- Boundary: Do not edit SettingsProvider private database files or Settings app private preferences directly, and do not expose credentials, account tokens, or private keys.

### Access Procedure
- Surface selection: Use the owning service command for service-backed state and the exact namespace/key pair for provider-owned state. Airplane mode additionally requires its state-change broadcast; brightness additionally requires manual mode when a fixed raw value is requested.
- Runtime discovery: Probe command availability, key presence, and stream ranges before mutation rather than assuming a uniform Android build.
- Read procedure: Read documented `system`, `secure`, and `global` keys individually and represent the provider's absent `null` result as no value.
- Write procedure: Control Wi-Fi through its service; couple airplane-mode key mutation with the matching broadcast; request UI night mode through its service and documented mirror keys; set fixed brightness in manual mode; set audio through the selected stream id/range; apply call-vibration compatibility keys consistently.

### Consistency / Recovery
- Cross-surface consistency: Wi-Fi, airplane mode, UI mode, and audio can expose both active service state and a persisted provider mirror; disagreement is partial state rather than two unrelated settings.
- Process consistency: Service-backed transitions are asynchronous, so command completion and persisted mirror updates may occur at different times.
- Recovery boundary: A missing mirror key can be compatible with service-owned state, but an unsupported service command must not be disguised by writing only a similarly named provider key.

## Files
### Primary
| Entity | Path | Fields |
|---|---|---|
| None | Private Settings app files are outside the documented state interface | Not applicable |

### Secondary
| Entity | Path | Fields |
|---|---|---|
| None | Not documented | Not applicable |

### Auxiliary
| Entity | Path | Fields |
|---|---|---|
| storage/file category | system storage/file indexes when app/storage interfaces inspect usage | app/package, cache size, data size, media category |

### Access Semantics
- Boundary: File/storage interfaces should use documented package/storage services or indexed storage state, not private Settings app files.

### Access Procedure
- Surface selection: Use package/storage services or indexed storage categories for explicitly modeled storage operations.
- Delete procedure: Destructive cache or storage operations require an explicit package/category selector and must not target private Settings app files directly.

### Consistency / Recovery
- Recovery boundary: Do not substitute raw filesystem deletion for service-owned package or storage state transitions.
