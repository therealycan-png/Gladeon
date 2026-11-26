"""Fixed replies, spec 12.5. Each entry is (display_text, tts_text)."""

VAULT_LOCKED = (
    "Vault is locked. Unlock it from the phone to use your notes.",
    "Your vault is locked. Please unlock it from your phone to use your notes.",
)
NOT_IN_NOTES = ("I don't see that in your notes.",) * 2
FINANCE_DEFERRED = ("I don't track your finances yet. That comes in a later update.",) * 2
DEEP_UNAVAILABLE_PREFIX = "Deep mode isn't available on this PC, so here's a quick answer."
EMPTY_WAKE = ("Yes?", "Yes?")
ENGINE_EMPTY = ("Sorry, I couldn't produce an answer. Please try again.",) * 2
OUT_OF_TIME = ("Sorry, I ran out of time on that one. Please ask again.",) * 2
LOCK_HINT = ("To lock the vault, say: lock the vault.",) * 2
CANT_SEARCH = ("I can't fully search your notes right now. Try again in a few minutes.",) * 2


def still_reading(n: int):
    """'Still reading' reply; n = pending chunks + unstable files + rescan finds. n == 0 -> 'Can't search'."""
    if n <= 0:
        return CANT_SEARCH
    text = f"I'm still reading your notes ({n} left). Try again in a few minutes."
    return (text, text)


def deep_loading(seconds: int):
    text = f"Loading the deep model. This takes about {seconds} seconds."
    return (text, text)
