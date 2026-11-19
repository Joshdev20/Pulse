"""The shipped sticker catalogue.

A sticker is an emoji plus a caption, not an uploaded image. That choice keeps
the whole feature cheap: a message stores a short id instead of bytes, the
artwork is drawn by the reader's own font (so it stays crisp at every size and
needs no CDN), and adding a pack later ships with a deploy rather than a media
migration.

The catalogue lives here rather than only in the frontend because the server has
to validate what it stores. A sticker id from a client is untrusted input, and
the message list, the inbox preview and the push payload all need to name a
sticker that exists.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Sticker:
    id: str
    emoji: str
    label: str
    pack: str


# Ordered by pack so the picker can group them without a second sort.
STICKERS: tuple[Sticker, ...] = (
    Sticker("heart", "\u2764\ufe0f", "Love", "Reactions"),
    Sticker("laugh", "\U0001f602", "Laughing", "Reactions"),
    Sticker("wow", "\U0001f62e", "Wow", "Reactions"),
    Sticker("sad", "\U0001f622", "Sad", "Reactions"),
    Sticker("angry", "\U0001f620", "Annoyed", "Reactions"),
    Sticker("fire", "\U0001f525", "On fire", "Reactions"),
    Sticker("party", "\U0001f389", "Celebrate", "Reactions"),
    Sticker("clap", "\U0001f44f", "Applause", "Reactions"),
    Sticker("thumbs", "\U0001f44d", "Nice", "Reactions"),
    Sticker("pray", "\U0001f64f", "Thanks", "Reactions"),
    Sticker("wave", "\U0001f44b", "Hello", "Greetings"),
    Sticker("hug", "\U0001f917", "Hug", "Greetings"),
    Sticker("kiss", "\U0001f618", "Kiss", "Greetings"),
    Sticker("wink", "\U0001f609", "Wink", "Greetings"),
    Sticker("cool", "\U0001f60e", "Cool", "Greetings"),
    Sticker("sleep", "\U0001f634", "Sleepy", "Greetings"),
    Sticker("think", "\U0001f914", "Thinking", "Greetings"),
    Sticker("shrug", "\U0001f937", "Shrug", "Greetings"),
    Sticker("eyes", "\U0001f440", "Watching", "Greetings"),
    Sticker("salute", "\U0001fae1", "Salute", "Greetings"),
    Sticker("coffee", "\u2615", "Coffee", "Everyday"),
    Sticker("pizza", "\U0001f355", "Pizza", "Everyday"),
    Sticker("cake", "\U0001f382", "Cake", "Everyday"),
    Sticker("gift", "\U0001f381", "Gift", "Everyday"),
    Sticker("money", "\U0001f4b8", "Money", "Everyday"),
    Sticker("rocket", "\U0001f680", "Rocket", "Everyday"),
    Sticker("star", "\u2b50", "Star", "Everyday"),
    Sticker("sun", "\u2600\ufe0f", "Sunny", "Everyday"),
    Sticker("rain", "\U0001f327\ufe0f", "Raining", "Everyday"),
    Sticker("moon", "\U0001f319", "Goodnight", "Everyday"),
    Sticker("check", "\u2705", "Done", "Status"),
    Sticker("cross", "\u274c", "No", "Status"),
    Sticker("clock", "\u23f0", "Running late", "Status"),
    Sticker("phone", "\U0001f4de", "Call me", "Status"),
    Sticker("mail", "\U0001f4e9", "Message me", "Status"),
    Sticker("pin", "\U0001f4cd", "Right here", "Status"),
    Sticker("lock", "\U0001f512", "Private", "Status"),
    Sticker("key", "\U0001f511", "Access", "Status"),
    Sticker("bulb", "\U0001f4a1", "Idea", "Status"),
    Sticker("muscle", "\U0001f4aa", "Strong", "Status"),
)

BY_ID: dict[str, Sticker] = {sticker.id: sticker for sticker in STICKERS}


def is_known(sticker_id: str) -> bool:
    return sticker_id in BY_ID


def preview_text(sticker_id: str | None) -> str:
    """How a sticker reads in a one-line preview, or "" when it is not one.

    The inbox row and the push alert show a single line, so a sticker has to
    become words: the emoji alone leaves the row looking empty.
    """
    if not sticker_id:
        return ""
    sticker = BY_ID.get(sticker_id)
    if sticker is None:
        # A row written by a newer deploy than this process. Showing something
        # is better than showing nothing.
        return "Sticker"
    return f"{sticker.emoji} Sticker"
