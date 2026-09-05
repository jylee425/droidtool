"""Shared app launch/package registry for benchmark rollout scripts."""

APP_PACKAGE = {
    "photonote": "com.chartreux.photo_note/.MainActivity",
    "photo_note": "com.chartreux.photo_note/.MainActivity",
    "joplin": "net.cozic.joplin/.MainActivity",
    "memo": "net.cozic.joplin/.MainActivity",
    "messages": "com.google.android.apps.messaging/.ui.ConversationListActivity",
    "sms": "com.google.android.apps.messaging/.ui.ConversationListActivity",
    "chrome": "com.android.chrome/com.google.android.apps.chrome.Main",
    "youtube": "com.google.android.youtube/com.google.android.youtube.HomeActivity",
    "bank": "com.example.bankApp/.MainActivity",
    "bankapp": "com.example.bankApp/.MainActivity",
    "stock": "com.alifesoftware.stocktrainer/.activities.ApplicationFlavorSelectorActivity",
    "calendar": "com.simplemobiletools.calendar.pro/.activities.MainActivity",
    "settings": "com.android.settings/.Settings",
    "contacts": "com.android.contacts/.activities.PeopleActivity",
    "calculator": "com.google.android.calculator/com.android.calculator2.Calculator",
    "clock": "com.google.android.deskclock/com.android.deskclock.DeskClock",
    "gmail": "com.google.android.gm/.GmailActivity",
    "maps": "com.google.android.apps.maps/com.google.android.maps.MapsActivity",
    "photos": "com.google.android.apps.photos/.PhotosActivity",
    "files": "com.google.android.apps.nbu.files",
    "filesapp": "com.google.android.apps.nbu.files",
    "filesbygoogle": "com.google.android.apps.nbu.files",
    "camera": "com.android.camera2",
    "phone": "com.android.dialer",
    "snapseed": "com.niksoftware.snapseed",
    "wikipedia": "org.wikipedia",
    "org.wikipedia": "org.wikipedia",
    "instagram": "com.instagram.android",
    "walmart": "com.walmart.android",
}

BMOCA_APP_PACKAGE = {
    "calculator": "com.google.android.calculator",
    "calendar": "com.android.calendar",
    "camera": "com.android.camera2",
    "chrome": "com.android.chrome",
    "clock": "com.google.android.deskclock",
    "contacts": "com.android.contacts",
    "files": "com.google.android.apps.nbu.files",
    "gmail": "com.google.android.gm",
    "maps": "com.google.android.apps.maps",
    "messages": "com.google.android.apps.messaging",
    "phone": "com.android.dialer",
    "photos": "com.google.android.apps.photos",
    "settings": "com.android.settings",
    "snapseed": "com.niksoftware.snapseed",
    "wikipedia": "org.wikipedia",
    "org.wikipedia": "org.wikipedia",
    "instagram": "com.instagram.android",
    "walmart": "com.walmart.android",
    "youtube": "com.google.android.youtube",
}


def normalize_app_name(name: object) -> str:
    return str(name or "").strip().lower().replace(" ", "")


def resolve_app_package(name: object) -> str | None:
    return APP_PACKAGE.get(normalize_app_name(name))


def resolve_b_moca_app_package(name: object) -> str | None:
    return BMOCA_APP_PACKAGE.get(normalize_app_name(name))
