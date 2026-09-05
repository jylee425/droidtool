"""Wi-Fi Settings task evaluator."""
from .common import evaluation, service_boolean, setting, snapshot, unavailable, value

TASKS = {"turn_off_wifi": 0, "turn_on_wifi": 1}

def capture(serial, task):
    return snapshot(task, serial, wifi_on=setting(serial, "global", "wifi_on"),
                    wifi_service=service_boolean(serial, ("cmd", "wifi", "status"),
                    r"wifi\s+is\s+enabled", r"wifi\s+is\s+disabled"))

def evaluate(task, before, after):
    if not before.get("ok") or not after.get("ok"): return unavailable(task, before, after)
    old, new = value(before, "wifi_service"), value(after, "wifi_service")
    provider_new, desired = value(after, "wifi_on"), TASKS[task]
    # Turning Wi-Fi on/off is idempotent: an episode is successful when the
    # requested final state is reflected by both the service and provider,
    # even when the device already started in that state.
    success = new == desired and provider_new == desired
    return evaluation(task, before, after, success,
                      f"wifi final state is {desired}: {old!r} -> {new!r}; wifi_on={provider_new!r}")
