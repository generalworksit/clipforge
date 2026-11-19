"""Stage 4: ready-to-paste post copy per clip (title, description, hashtags, with emojis).

Uses the same LLM provider as moment picking; without one, builds the copy from the
transcript with simple rules so every clip still gets something usable.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Callable

import requests

from .moments import InvalidOutput, LLMProvider, _ask

Log = Callable[[str], None]

POST_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"}, "title": {"type": "string"},
            "description": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "voice_hook": {"type": "string"},
        },
        "required": ["id", "title", "description", "hashtags", "voice_hook"],
    },
}

POST_SYSTEM = """You write the post copy for short vertical videos (TikTok, Instagram Reels, YouTube Shorts).
For every clip you receive, write in the same language as the clip ({language}):
- title: at most 70 characters with 1 or 2 fitting emojis, no hashtags. It must create curiosity or tension
  (a bold claim, a surprising number, a question, a "nobody tells you" angle), not just name the topic.
  Never invent facts that are not in the clip.
- description: 1 to 3 short lines, at most 220 characters in total, 2 to 4 fitting emojis, ends with a question or a call to comment, no hashtags
- hashtags: 4 to 6 hashtags without the # sign, lowercase, no spaces, mixing the clip's specific topic with broader ones
- voice_hook: one sentence of 8 to 18 words that a narrator voice reads before the clip starts, like the
  text-to-speech intros on TikTok. It sets up the situation and teases what happens without giving away the
  ending, so people stay to see it. Plain spoken words: no emojis, no hashtags, no quotes.
  Talk about the people and what happens, never about the clip itself: no "this clip", "this moment",
  "the stream", "chat", "viewers", "replayed", "exclamation", "conversation".
  It must name the people, things or places that are really in THAT clip's text or in the video title.
  Shapes it can take (fill them with that clip's own content, never copy them):
  [who] [did something unusual], and [a hint of the result].
  [who] thought [plan], but [a hint that it went another way].
  Every clip gets its own narrator line: never the same line twice.
- The title and description follow the same rule: write about what happens, not "a moment" or "a clip".

Answer with strict JSON only: an array with one object per clip
[{{"id": integer, "title": string, "description": string, "hashtags": [string, ...], "voice_hook": string}}]
No markdown, nothing outside the JSON array."""

STOPWORDS = set("""
a an and are as at be been but by can could did do does for from had has have he her him his how i if in into is it
its just like me my no not of on or our out so some that the their them then there they this to too up us was we were
what when where which who why will with would you your yeah okay really very about because going get got know think
want one all more people thing things time said say also over only even back much well
le la les un une des et est en du de que qui ne pas pour sur dans au aux ce cette ces il elle ils elles on nous vous
je tu me te se mon ma mes ton ta tes son sa ses leur leurs mais ou donc car avec sans plus moins tout tous fait faire
être avoir été comme alors bien très aussi ça c'est j'ai y a l d qu n s m t
""".split())

EMOJI_RULES = [
    (r"\b(money|dollars?|rich|million|billion|argent|euros?|riche|business)\b", "💰"),
    (r"\b(love|heart|amour|coeur|cœur)\b", "❤️"),
    (r"\b(die|death|died|dead|mort|mourir)\b", "🕯️"),
    (r"\b(work|job|career|travail|boulot|carrière)\b", "💼"),
    (r"\b(learn|school|college|university|class|école|université|cours|apprendre)\b", "🎓"),
    (r"\b(secret|truth|vérité|secrets?)\b", "🤫"),
    (r"\b(funny|laugh|joke|drôle|rire|blague)\b", "😂"),
    (r"\b(fail|failure|mistake|échec|erreur|fired|viré)\b", "💥"),
    (r"\b(dream|future|rêve|avenir)\b", "✨"),
    (r"\b(food|eat|manger|cuisine)\b", "🍽️"),
    (r"\b(game|play|jeu|jouer)\b", "🎮"),
    (r"\?", "🤔"),
]


def _emoji_for(text: str) -> list[str]:
    found = [e for pattern, e in EMOJI_RULES if re.search(pattern, text, re.I)]
    return list(dict.fromkeys(found)) or ["🔥"]


def _keywords(text: str, k: int) -> list[str]:
    words = [w.lower().strip(".,!?;:'\"()…«»") for w in text.split()]
    words = [re.sub(r"[^\w]", "", w) for w in words if len(w) > 3 and w.lower() not in STOPWORDS]
    return [w for w, _ in Counter(w for w in words if w).most_common(k)]


# Words a narrator line uses about anything; they do not show that the line is about this clip.
VAGUE = set("""
tell told says said gets gave give take took make made came come went goes look looks see saw seen watch
believe single word words answer response reply surprise surprising surprised everyone everything nobody
someone something anything thing guy man woman girl boy people person stranger friend happen happened
next end ending start started wait right never always whole first last little big huge real really crazy
wild insane""".split())


def _stems(text: str) -> set[str]:
    words = (w.rstrip("s") for w in re.findall(r"[^\W\d_]+", text.lower()))
    return {w[:5] for w in words if len(w) > 3 and w not in STOPWORDS and w not in VAGUE}


def grounded(line: str, *sources: str) -> bool:
    """True when a narrator line is about the clip: it shares at least one real word (a name, a thing, a
    place) with the clip's text or the video title. Small models sometimes copy a line from the prompt or
    from another video word for word."""
    if not line or "[" in line or "]" in line:
        return False
    return bool(_stems(line) & _stems(" ".join(sources)))


# Used when a clip has (almost) no dialogue, like an action scene or a fail: the moment type says more than the words.
CATEGORY_COPY = {
    "funny": ("Try not to laugh at this one", "Essaie de ne pas rire", "😂", ["funny", "comedy"]),
    "scary": ("Do not watch this alone", "Ne regarde pas ça seul", "😱", ["scary", "horror"]),
    "action": ("Nobody expected what happened next", "Personne ne s'attendait à la suite", "💥", ["action", "insane"]),
    "emotional": ("This one hits different", "Celle-là touche en plein cœur", "😢", ["emotional", "feels"]),
    "shocking": ("Wait for the end", "Attends la fin", "😮", ["shocking", "plottwist"]),
    "romantic": ("They have the best chemistry", "Cette alchimie entre eux", "❤️", ["romance", "couple"]),
    "hype": ("This is insane", "C'est complètement fou", "🔥", ["hype", "insane"]),
    "drama": ("It got heated fast", "Ça a dégénéré vite", "😤", ["drama", "tension"]),
    "insight": ("Nobody tells you this", "Personne ne te dit ça", "💡", ["tips", "facts"]),
    "story": ("Listen to this story", "Écoute cette histoire", "📖", ["story", "storytime"]),
}


def rule_based_post(clip: dict, language: str) -> dict:
    emojis = _emoji_for(clip["text"])
    hook = clip["hook"].strip().rstrip(".")
    cat = CATEGORY_COPY.get(clip.get("category") or "")
    if cat and len(hook.split()) < 4:  # no real dialogue to quote: write from the moment type
        title = f"{cat[1] if language == 'fr' else cat[0]} {cat[2]}"
        cta = "Tu en penses quoi ? 👇" if language == "fr" else "What would you have done? 👇"
        return {"title": title, "description": f"{title}\n{cta}",
                "hashtags": (cat[3] + (["pourtoi", "viral"] if language == "fr" else ["fyp", "viral"]))[:6],
                "source": "rules"}
    title = hook
    if len(title) > 60:  # end on a clause break when there is one, else on a word
        cut = max(title.rfind(",", 0, 58), title.rfind(" but ", 0, 58), title.rfind(" and ", 0, 58))
        title = title[:cut] if cut > 25 else title[:57].rsplit(" ", 1)[0] + "…"
    title = f"{title} {emojis[0]}"
    first = hook if len(hook) <= 120 else hook[:117].rsplit(" ", 1)[0] + "…"
    if language == "fr":
        cta = "Tu en penses quoi ? 👇"
        broad = ["pourtoi", "motivation", "viral"]
    else:
        cta = "What do you think? 👇"
        broad = ["fyp", "motivation", "viral"]
    description = f"{first} {' '.join(emojis[:2])}\n{cta}"
    hashtags = (_keywords(clip["text"], 3) + broad)[:6]
    return {"title": title, "description": description, "hashtags": hashtags, "source": "rules"}


def _clean_tag(tag: str) -> str:
    return re.sub(r"[^\w]", "", tag.lstrip("#").lower())


def write_posts(clips: list[dict], language: str, provider: LLMProvider | None, log: Log,
                progress: Callable[[float, str], None], video_title: str = "") -> str:
    """Attach clip["post"] = {title, description, hashtags, source}. Returns the method used."""
    method = "rules"
    if provider is not None:
        listing = [{"id": i, "working_title": c["title"], "kind_of_moment": c.get("category") or "", "hook": c["hook"],
                    "text": c["text"][:900] or "(no dialogue: an action, a fail or a scene without words)"}
                   for i, c in enumerate(clips)]

        def validate(items: list) -> tuple[list, list]:
            good, problems, seen = [], [], set()
            for it in items:
                try:
                    cid = int(it["id"])
                    title, desc = str(it["title"]).strip(), str(it["description"]).strip()
                    tags = [_clean_tag(str(t)) for t in it["hashtags"]]
                except (KeyError, TypeError, ValueError):
                    problems.append(f"bad item {str(it)[:100]}")
                    continue
                if cid in seen or not 0 <= cid < len(clips):
                    problems.append(f"unknown or repeated id {cid}")
                    continue
                if not title or not desc:
                    problems.append(f"id {cid}: empty title or description")
                    continue
                seen.add(cid)
                good.append({"id": cid, "title": title[:100], "description": desc[:400],
                             "hashtags": [t for t in dict.fromkeys(tags) if t][:8],
                             "voice_hook": str(it.get("voice_hook") or "").strip()[:220]})
            return good, problems

        progress(0.1, f"{provider.name} · writing copy for {len(clips)} clip(s)")
        try:
            context = f"The clips come from a video titled: {video_title}\n" if video_title else ""
            posts = _ask(provider, POST_SYSTEM.format(language=language),
                         context + "Clips:\n" + json.dumps(listing, ensure_ascii=False, indent=1)
                         + f"\n\nWrite the post copy for all {len(clips)} clips as a JSON array.",
                         POST_SCHEMA, validate, log, "posts")
            used: set[str] = set()
            for p in posts:
                clips[p["id"]]["post"] = {**{k: p[k] for k in ("title", "description", "hashtags")},
                                          "source": provider.name}
                c, line = clips[p["id"]], p["voice_hook"]
                if line.lower() in used:
                    log(f"clip {p['id'] + 1}: narrator line repeats another clip's, dropped: {line!r}")
                elif grounded(line, c["text"], c["hook"], c["title"], video_title):
                    c["voice_hook"] = line
                    used.add(line.lower())
                elif line:
                    log(f"clip {p['id'] + 1}: narrator line not about the clip, dropped: {line!r}")
            method = provider.name
        except (requests.RequestException, InvalidOutput, RuntimeError, KeyError) as exc:
            log(f"LLM post writing failed ({exc!r}); using rule-based copy")
    missing = [c for c in clips if "post" not in c]
    for c in missing:
        c["post"] = rule_based_post(c, language)
    if missing and method != "rules":
        method += " + rules"
    return method


def post_text(post: dict) -> str:
    tags = " ".join(f"#{t}" for t in post["hashtags"])
    return f"{post['title']}\n\n{post['description']}\n\n{tags}\n"


HOOK_SCHEMA = {
    "type": "array",
    "items": {"type": "object", "properties": {"voice_hook": {"type": "string"}}, "required": ["voice_hook"]},
}

HOOK_SYSTEM = """You write the narrator line read by a text-to-speech voice at the start of a short vertical video
(TikTok, Reels, Shorts). Write it in {language}.
One sentence of 8 to 18 words. It sets up the situation and teases what happens without giving away the ending,
so people stay to see it. Plain spoken words: no emojis, no hashtags, no quotes. Never invent facts that are not in
the video: name the people, things or places that are really in it.
Shape it can take (fill it with the video's own content, never copy it):
[who] [did something unusual], and [a hint of the result].

Answer with strict JSON only: [{{"voice_hook": string}}]"""


def write_hook(text: str, title: str, language: str, provider: LLMProvider | None, log: Log) -> tuple[str, str]:
    """A narrator line for one whole video (the AI voice tool). Returns (line, method); the line
    is empty when there is no LLM, so the caller falls back to a generic teaser."""
    if provider is None or not text.strip():
        return "", "teaser"

    def validate(items: list) -> tuple[list, list]:
        good = [str(it.get("voice_hook") or "").strip() for it in items if isinstance(it, dict)]
        good = [g for g in good if len(g.split()) >= 4 and grounded(g, text, title)]
        return good, ([] if good else ["empty, too short, or not about this video: use names, things and "
                                       "places from what is said"])

    try:
        lines = _ask(provider, HOOK_SYSTEM.format(language=language),
                     f"Video title: {title}\nWhat is said in the video:\n{text[:3000]}\n\nWrite the narrator line.",
                     HOOK_SCHEMA, validate, log, "hook")
        return lines[0][:220], provider.name
    except (requests.RequestException, InvalidOutput, RuntimeError, KeyError) as exc:
        log(f"LLM hook writing failed ({exc!r}); using a teaser")
        return "", "teaser"
