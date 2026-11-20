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

from .flags import COUNTRIES, flag_emoji


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
    # Big-feeling pack: the reactions people reach for when a single emoji is
    # the whole reply.
    Sticker("sob", "\U0001f62d", "Sobbing", "Moods"),
    Sticker("rofl", "\U0001f923", "Rolling", "Moods"),
    Sticker("smirk", "\U0001f60f", "Smirk", "Moods"),
    Sticker("upset", "\U0001f644", "Unimpressed", "Moods"),
    Sticker("cry", "\U0001f97a", "Pleading", "Moods"),
    Sticker("mindblown", "\U0001f92f", "Mind blown", "Moods"),
    Sticker("hot", "\U0001f975", "Overheated", "Moods"),
    Sticker("cold", "\U0001f976", "Freezing", "Moods"),
    Sticker("nervous", "\U0001f605", "Nervous laugh", "Moods"),
    Sticker("smile", "\U0001f60a", "Happy", "Moods"),
    Sticker("love", "\U0001f970", "Adoring", "Moods"),
    Sticker("neutral", "\U0001f610", "Meh", "Moods"),
    # Everyday objects and moments, for a thread that is about plans rather
    # than feelings.
    Sticker("food", "\U0001f35c", "Food", "Lifestyle"),
    Sticker("burger", "\U0001f354", "Burger", "Lifestyle"),
    Sticker("drink", "\U0001f964", "Drink", "Lifestyle"),
    Sticker("beer", "\U0001f37b", "Cheers", "Lifestyle"),
    Sticker("music", "\U0001f3b5", "Music", "Lifestyle"),
    Sticker("game", "\U0001f3ae", "Gaming", "Lifestyle"),
    Sticker("ball", "\u26bd", "Football", "Lifestyle"),
    Sticker("gym", "\U0001f3cb\ufe0f", "Gym", "Lifestyle"),
    Sticker("car", "\U0001f697", "On my way", "Lifestyle"),
    Sticker("plane", "\u2708\ufe0f", "Travelling", "Lifestyle"),
    Sticker("home", "\U0001f3e1", "Home", "Lifestyle"),
    Sticker("bed", "\U0001f6cc", "Sleeping", "Lifestyle"),
    Sticker("shop", "\U0001f6d2", "Shopping", "Lifestyle"),
    Sticker("work", "\U0001f4bb", "Working", "Lifestyle"),
    # Celebration, for the news people actually send each other.
    Sticker("tada", "\U0001f38a", "Congrats", "Celebration"),
    Sticker("trophy", "\U0001f3c6", "Winner", "Celebration"),
    Sticker("medal", "\U0001f3c5", "Medal", "Celebration"),
    Sticker("sparkles", "\u2728", "Sparkles", "Celebration"),
    Sticker("balloon", "\U0001f388", "Balloon", "Celebration"),
    Sticker("confetti", "\U0001f386", "Fireworks", "Celebration"),
    Sticker("crown", "\U0001f451", "Crown", "Celebration"),
    Sticker("gem", "\U0001f48e", "Gem", "Celebration"),
    Sticker("rainbow", "\U0001f308", "Rainbow", "Celebration"),
    Sticker("sparkler", "\U0001f387", "Sparkler", "Celebration"),
    # Animals: the shorthand a long-running thread settles into.
    Sticker("dog", "\U0001f436", "Dog", "Animals"),
    Sticker("cat", "\U0001f431", "Cat", "Animals"),
    Sticker("puppy", "\U0001f415", "Puppy", "Animals"),
    Sticker("kitten", "\U0001f408", "Kitten", "Animals"),
    Sticker("fox", "\U0001f98a", "Fox", "Animals"),
    Sticker("panda", "\U0001f43c", "Panda", "Animals"),
    Sticker("penguin", "\U0001f427", "Penguin", "Animals"),
    Sticker("unicorn", "\U0001f984", "Unicorn", "Animals"),
    Sticker("butterfly", "\U0001f98b", "Butterfly", "Animals"),
    Sticker("whale", "\U0001f433", "Whale", "Animals"),
)

# Every country flag, appended after the hand-written packs so the curated set
# above stays the part a reader edits. Ids are `flag-<code>` (lower case) because
# a sticker id is a URL-ish token, while the code itself stays upper case in the
# label. Generated rather than listed: 249 literal emoji would be unreadable and
# one bad escape would render as letters instead of a flag.
FLAGS: tuple[Sticker, ...] = tuple(
    Sticker(f"flag-{code.lower()}", flag_emoji(code), name, "Flags")
    for code, name in COUNTRIES
)

STICKERS: tuple[Sticker, ...] = STICKERS + FLAGS

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
