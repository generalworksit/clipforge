"""Stage 3: pick the best standalone moments.

The LLM provider is swappable (config.yaml -> moments.provider). A provider only has to turn
a list of chat messages into text; chunking, validation, retries, sentence snapping and
de-duplication are shared. When the LLM is unavailable a heuristic picker keeps the app working.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np
import requests

from .audio import Loudness
from .config import CONFIG
from .signals import Signals

Log = Callable[[str], None]
Progress = Callable[[float, str], None]

TERMINAL = re.compile(r"[.!?…。！？]['\"”»)\]]*$")
ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "st.", "mt.", "vs.", "jr.", "sr.", "no.", "ft.", "etc.", "u.s.", "m.", "mme."}


def ends_sentence(text: str) -> bool:
    return bool(TERMINAL.search(text)) and text.lower() not in ABBREVIATIONS


# ------------------------------------------------------------------ sentences

@dataclass
class Sentence:
    start: float
    end: float
    words: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w["text"] for w in self.words)


def all_words(transcript: dict) -> list[dict]:
    words = [w for seg in transcript["segments"] for w in seg["words"]]
    return sorted(words, key=lambda w: w["start"])


# A thought cannot end on these words ("...and I went to the"), so a pause after one is a breath,
# not a sentence boundary. English and French, lowercase, punctuation stripped.
NO_END = set("""
a an the and or but nor so because cause to of in on at for with from by as if than that which who whom
whose my your his her our their its this these those is are was were be been being am do does did have has
had i i'm i've i'd i'll you you're he he's she she's we we're they they're it it's there there's gonna
wanna gotta like just very really about into onto over under then when while where what how why not no
can could will would should might must let's get got going
le la les l un une des du de d et ou mais donc car que qu qui dont à au aux en dans sur sous pour par avec
sans chez vers mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs ce cet cette ces je j tu il
elle on nous vous ils elles c c'est j'ai est sont était ai as a ont très trop plus si quand comme parce
puisque y ne n se s m t lui
""".split())
# A clip should not open on these: they lean on the sentence before ("And then...", "Which is why...").
LEANS_BACK = set("""
and but or nor because cause which whereas also plus too than
et mais ou car parce puisque dont aussi sinon
""".split())
WORD = re.compile(r"[\w'’-]+")


def _bare(text: str) -> str:
    m = WORD.search(text.lower().replace("’", "'"))
    return m.group(0).strip("'-") if m else ""


def is_punctuated(words: list[dict]) -> bool:
    """Whisper usually punctuates; on some casual audio it writes lowercase run-on text instead."""
    ends = sum(1 for w in words if ends_sentence(w["text"]))
    return bool(words) and ends / len(words) >= 1 / 45


def split_sentences(words: list[dict], long_sentence: float = 25.0) -> list[Sentence]:
    """Group words into sentences.

    Punctuated transcript: terminal punctuation is the boundary. A pause only splits when the next
    word starts with a capital (a pause inside "I decided ... to take" is a hesitation, not an end).
    Unpunctuated transcript: pauses split, except after a word that cannot end a thought or before
    one that leans on the previous sentence. Run-on stretches longer than `long_sentence` break at
    the best pause available so a sentence never grows without bound."""
    punctuated = is_punctuated(words)
    out: list[Sentence] = []
    cur: list[dict] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None:
            break
        gap = nxt["start"] - w["end"]
        run = w["end"] - cur[0]["start"]
        last, first = _bare(w["text"]), _bare(nxt["text"])
        clean_break = last not in NO_END and first not in LEANS_BACK
        if punctuated:
            upper = nxt["text"][:1].isupper()
            capital = upper and first != "i"  # "I" is capitalised mid-sentence too
            terminal = ends_sentence(w["text"]) and (upper or not nxt["text"][:1].isalpha())
            boundary = (terminal or (gap > 1.2 and capital and clean_break) or gap > 3.0
                        or (run > long_sentence and gap > 0.35 and (w["text"][-1:] in ",;:" or capital)))
        else:
            boundary = ((gap > 0.7 and clean_break) or gap > 2.5
                        or (run > long_sentence and gap > 0.35 and clean_break))
        if boundary or (run > long_sentence * 2 and gap > 0.25):
            out.append(Sentence(cur[0]["start"], cur[-1]["end"], cur))
            cur = []
    if cur:
        out.append(Sentence(cur[0]["start"], cur[-1]["end"], cur))
    return out


def _end_cost(sents: list[Sentence], j: int, target: float) -> float:
    """How bad sentence j is as the last one of a clip: distance from the wanted end, plus penalties
    for a question whose answer is cut off and for an ending that is not a clear finish."""
    if not sents[j].words:
        return 0.0  # a stretch without speech: ending there cuts nothing
    last = sents[j].words[-1]["text"]
    nxt = sents[j + 1] if j + 1 < len(sents) else None
    gap = (nxt.start - sents[j].end) if nxt else 9.0
    cost = abs(sents[j].end - target)
    if last.rstrip("\"'”»)]").endswith("?") and gap < 1.5:
        cost += 8  # the answer comes right after: ending here is a cliffhanger by accident
    if not ends_sentence(last) and gap < 0.6:
        cost += 6  # no punctuation and no pause: probably still talking
    if _bare(last) in NO_END and not ends_sentence(last):
        cost += 10  # "...it's super cold outside just"
    if nxt and nxt.words and _bare(nxt.words[0]["text"]) in LEANS_BACK and gap < 0.8:
        cost += 4  # "...and" continues the thought
    return cost


def snap(start: float, end: float, sents: list[Sentence], words: list[dict],
         min_s: float, max_s: float, duration: float) -> tuple[float, float] | None:
    """Move start/end onto sentence boundaries, keeping the length in [min_s, max_s] where the
    sentence structure allows it, then add a little breathing room."""
    if not sents:
        return None
    i = min(range(len(sents)), key=lambda k: abs(sents[k].start - start))
    # "And then he..." needs the sentence before it
    while (i > 0 and sents[i].words and _bare(sents[i].words[0]["text"]) in LEANS_BACK
           and sents[i].end - sents[i - 1].start <= max_s):
        i -= 1
    s = sents[i].start
    ends = []
    for j in range(i, len(sents)):
        if sents[j].end - s > max_s and j > i:
            break
        ends.append(j)
    in_range = [j for j in ends if min_s <= sents[j].end - s <= max_s]
    if in_range:
        e = sents[min(in_range, key=lambda k: _end_cost(sents, k, end))].end
    elif sents[ends[-1]].end - s <= max_s:
        e = sents[ends[-1]].end  # shorter than min_s: the whole remaining run of sentences
    elif sents[i].end - s <= max_s * 1.25:
        e = sents[i].end  # one long sentence: finishing it beats cutting it
    else:
        # a single sentence far longer than max_s: end at its best internal break (comma, then pause)
        fitting = [k for k, w in enumerate(sents[i].words) if w["end"] - s <= max_s] or [0]
        ws = sents[i].words
        k = max(fitting, key=lambda k: (ws[k]["text"][-1:] in ",;:") * 2 + min(ws[k + 1]["start"] - ws[k]["end"], 1.0)
                + (0 if _bare(ws[k]["text"]) in NO_END else 1) + ws[k]["end"] / max_s * 0.1)
        e = ws[k]["end"]
    return pad(s, e, words, duration)


def pad(s: float, e: float, words: list[dict], duration: float) -> tuple[float, float]:
    """Breathing room without swallowing neighbouring words: Whisper word starts can be a little late."""
    prev_end = max((w["end"] for w in words if w["end"] <= s + 0.01), default=0.0)
    next_start = min((w["start"] for w in words if w["start"] >= e - 0.01), default=duration)
    s = max(min(s, prev_end + 0.05), s - 0.3, 0.0)
    e = min(max(e, min(e + 0.5, next_start - 0.05)), duration)
    return round(s, 3), round(e, 3)


def snap_to_cuts(s: float, e: float, words: list[dict], sig: Signals | None, duration: float,
                 max_s: float) -> tuple[float, float]:
    """Films and edited videos: start on the shot change just before the first line and end on
    the one just after the last line, when no other dialogue sits in between."""
    if sig is None or not sig.cuts:
        return s, e
    before = [c for c in sig.cuts_between(s - 2.5, s)
              if not any(w["end"] > c + 0.05 and w["start"] < s - 0.05 for w in words)]
    after = [c for c in sig.cuts_between(e, e + 2.5)
             if not any(w["start"] < c - 0.05 and w["end"] > e + 0.05 for w in words)]
    if before and e - before[-1] <= max_s * 1.1:
        s = before[-1] + 0.04
    if after and after[0] - s <= max_s * 1.1:
        e = min(after[0] - 0.04, duration)
    return round(s, 3), round(e, 3)


def text_between(words: list[dict], start: float, end: float) -> str:
    return " ".join(w["text"] for w in words if w["start"] >= start - 0.01 and w["end"] <= end + 0.01)


# ------------------------------------------------------------------ heuristic picker

NUM = re.compile(r"\d")
HOOK_WORDS = re.compile(r"\b(you|your|why|how|never|secret|nobody|vous|tu|ton|pourquoi|comment|jamais|personne|secret)\b", re.I)


def remove_overlaps(moments: list[dict], n: int, taken: list[dict] | None = None) -> list[dict]:
    """Best first, no overlaps. A moment type that is already in the list counts a little less,
    so asking for funny + scary moments gives a mix instead of five times the same thing."""
    keep = list(taken or [])
    pool = sorted(moments, key=lambda m: m["score"], reverse=True)
    while len(keep) < n and pool:
        used: dict[str, int] = {}
        for k in keep:
            used[k.get("category") or ""] = used.get(k.get("category") or "", 0) + 1
        free = [m for m in pool if all(m["end"] <= k["start"] + 0.5 or m["start"] >= k["end"] - 0.5 for k in keep)]
        if not free:
            break
        best = max(free[:200], key=lambda m: m["score"] - 5.0 * used.get(m.get("category") or "", 0) * bool(m.get("category")))
        keep.append(best)
        pool.remove(best)
    return keep


def auto_count(moments: list[dict], duration: float, cap: int = 8) -> int:
    """How many clips "Auto" makes: only the moments close to the best one, at most one per
    ~90 s of video and never more than `cap`."""
    if not moments:
        return 0
    scores = sorted((m["score"] for m in moments), reverse=True)
    top = scores[0]
    limit = max(1, min(cap, int(duration // 90) + 1))
    strong = sum(1 for s in scores if s >= max(top - 7, 38))
    if duration >= 240:  # a video of several minutes nearly always has two moments worth posting
        strong = max(strong, min(2, len(scores)))
    return max(1, min(limit, strong))


# ------------------------------------------------------------------ video types and moment types

CATEGORIES: dict[str, dict] = {
    "funny":     {"label": "Funny", "emoji": "😂", "llm": "jokes, banter, awkward or absurd situations, people laughing"},
    "scary":     {"label": "Scary", "emoji": "😱", "llm": "jump scares, tension that builds then breaks, screams, creepy reveals"},
    "action":    {"label": "Action", "emoji": "💥", "llm": "fights, chases, crashes, explosions, stunts, fast intense sequences"},
    "emotional": {"label": "Emotional", "emoji": "😢", "llm": "confessions, goodbyes, grief, reunions, someone breaking down, heartfelt words"},
    "shocking":  {"label": "Shocking", "emoji": "😮", "llm": "plot twists, reveals, unbelievable claims, jaw-drop reactions, 'no way' moments"},
    "romantic":  {"label": "Romance", "emoji": "❤️", "llm": "flirting, love declarations, kisses, chemistry between two people"},
    "hype":      {"label": "Hype", "emoji": "🔥", "llm": "big wins, clutch plays, celebrations, huge money, records, crowd going wild"},
    "drama":     {"label": "Drama", "emoji": "😤", "llm": "arguments, rage, confrontations, insults, someone getting called out"},
    "insight":   {"label": "Insight", "emoji": "💡", "llm": "useful tips, hot takes, strong opinions, surprising facts, life lessons"},
    "story":     {"label": "Story", "emoji": "📖", "llm": "a complete short story or anecdote with a setup and a payoff"},
}

MODES: dict[str, dict] = {
    "viral": {
        "label": "Any video", "hint": "Let the AI find what works",
        "cats": ["funny", "shocking", "hype", "emotional", "insight", "story"],
        "framing": "face", "weights": (0.30, 0.35, 0.35),
        "focus": "This can be any kind of video. Pick the moments most likely to go viral as standalone clips.",
    },
    "youtuber": {
        "label": "YouTuber & vlog", "hint": "Challenges, vlogs, reactions, IRL streams",
        "cats": ["funny", "shocking", "hype", "drama"],
        "framing": "face", "weights": (0.20, 0.35, 0.45),
        "focus": ("This is a YouTuber video (challenge, vlog, reaction, IRL stream). Pick the peaks: the reveal, the "
                  "reaction, the chaos, the win or the fail, the funniest exchange, the big moment of the challenge. "
                  "Skip setup and explanations of the rules."),
    },
    "film": {
        "label": "Film & series", "hint": "Movies, TV episodes, anime, cartoons",
        "cats": ["funny", "scary", "action", "emotional", "shocking"],
        "framing": "blur", "weights": (0.20, 0.50, 0.30),
        "focus": ("This is a film or a TV episode. Viewers of the clip never saw the rest, so pick complete scenes: "
                  "a whole joke with its punchline, a scare from build-up to payoff, a full fight or chase, a "
                  "confession, a twist. Lines in the transcript can be dialogue from several characters. "
                  "Stretches marked (no speech) can be action, music or silent tension: use the cues to judge them."),
    },
    "talk": {
        "label": "Podcast & talk", "hint": "Podcasts, interviews, speeches, courses",
        "cats": ["insight", "story", "funny", "shocking", "emotional"],
        "framing": "face", "weights": (0.40, 0.40, 0.20),
        "focus": ("This is a podcast, interview, speech or lesson. Pick the strongest takes: bold opinions, surprising "
                  "facts, useful advice, complete stories, funny exchanges between the speakers."),
    },
    "gaming": {
        "label": "Gaming & streams", "hint": "Gameplay, Twitch / Kick VODs",
        "cats": ["hype", "funny", "drama", "shocking", "scary"],
        "framing": "crop", "weights": (0.20, 0.45, 0.35),
        "focus": ("This is gameplay or a stream. Pick clutch plays, insane kills, wins, fails, rage, funny reactions "
                  "and chat-worthy moments. The streamer's reaction (screaming, laughing) usually marks the peak."),
    },
}
FRAMINGS = {"face": "Follow the speaker's face", "split": "Two people stacked", "crop": "Centre crop",
            "blur": "Whole picture + blurred background"}


def _lex(*phrases: str) -> re.Pattern:
    return re.compile(r"(?<![\w'])(?:" + "|".join(phrases) + r")(?![\w'])", re.I)


# Words and phrases that tend to show up in each kind of moment, English and French.
LEXICON: dict[str, re.Pattern] = {
    "funny": _lex(r"ha(?:ha)+h?", r"he(?:he)+", r"lo+l", r"lmf?ao+", r"hilarious", r"funny", r"joke", r"joking",
                  r"kidding", r"bro what", r"i'm dead", r"i'm crying", r"stupid", r"idiot", r"weird",
                  r"mdr", r"ptdr", r"dr[ôo]le", r"rigole", r"blague", r"mort de rire", r"n'importe quoi", r"bizarre"),
    "scary": _lex(r"scream\w*", r"scar(?:y|ed)", r"terrif\w*", r"horror", r"ghost", r"monster", r"kill\w*", r"blood",
                  r"behind you", r"what was that", r"did you hear", r"someone'?s? (?:here|there|coming)", r"run",
                  r"hide", r"help(?: me)?", r"don't move", r"stay quiet", r"afraid", r"dead",
                  r"peur", r"fant[ôo]me", r"monstre", r"sang", r"au secours", r"derri[èe]re toi", r"cache-toi",
                  r"tu as entendu", r"cours", r"tuer"),
    "action": _lex(r"go go", r"move", r"shoot", r"fire", r"gun", r"get down", r"explosion", r"fight", r"punch",
                   r"crash\w*", r"chase", r"faster", r"watch out", r"look out", r"hold on", r"jump", r"now now",
                   r"bouge", r"tire", r"attention", r"vite", r"bagarre", r"combat", r"accroche-toi", r"saute"),
    "emotional": _lex(r"i love you", r"i'm sorry", r"i miss (?:you|him|her)", r"forgive", r"cry\w*", r"tears",
                      r"mom", r"dad", r"mother", r"father", r"goodbye", r"never forget", r"proud of", r"passed away",
                      r"lost (?:him|her|my)", r"thank you so much", r"from the bottom of my heart", r"my life",
                      r"je t'aime", r"pardon", r"d[ée]sol[ée]e?", r"tu me manques", r"pleur\w*", r"maman", r"papa",
                      r"adieu", r"fi[èe]re? de", r"merci beaucoup", r"ma vie"),
    "shocking": _lex(r"oh my god", r"omg", r"what the \w+", r"no way", r"wtf", r"holy \w+", r"impossible",
                     r"can'?t believe", r"wait what", r"are you serious", r"plot twist", r"the truth",
                     r"i knew it", r"it was you", r"he'?s alive", r"she'?s alive", r"oh no",
                     r"c'est pas possible", r"incroyable", r"s[ée]rieux", r"la v[ée]rit[ée]", r"oh mon dieu",
                     r"j'y crois pas", r"putain"),
    "romantic": _lex(r"kiss\w*", r"date", r"marry", r"beautiful", r"darling", r"babe", r"girlfriend", r"boyfriend",
                     r"in love", r"love you", r"my heart",
                     r"amour", r"bisou", r"embrasse\w*", r"[ée]pouse", r"ch[ée]rie?", r"belle", r"mon c(?:oeu|œu)r"),
    "hype": _lex(r"let'?s go+", r"yes+", r"insane", r"crazy", r"clutch", r"won", r"win", r"winner", r"record",
                 r"million", r"thousand dollars", r"\$\d[\d,.]*", r"huge", r"goat", r"unbelievable", r"we did it",
                 r"allez", r"on a gagn[ée]", r"gagn[ée]", r"fou", r"dingue", r"[ée]norme", r"incroyable"),
    "drama": _lex(r"shut up", r"liar", r"how dare", r"what'?s wrong with you", r"i hate", r"get out", r"stop it",
                  r"fuck\w*", r"shit", r"bitch", r"you lied", r"don't touch", r"back off", r"are you kidding me",
                  r"ta gueule", r"menteu[rs]e?", r"d[ée]gage", r"connard", r"merde", r"tu m'as menti", r"arr[êe]te"),
    "insight": _lex(r"the secret", r"the truth is", r"here'?s (?:the|why|how|what)", r"the reason", r"mistake",
                    r"most people", r"nobody tells you", r"the key", r"the problem is", r"you have to",
                    r"if you want", r"lesson", r"\d+ (?:percent|%|years|times)",
                    r"le secret", r"la raison", r"erreur", r"la plupart des gens", r"il faut", r"le probl[èe]me"),
    "story": _lex(r"one day", r"i remember", r"when i was", r"back then", r"this one time", r"so then",
                  r"long story", r"true story", r"happened", r"un jour", r"je me souviens", r"quand j'[ée]tais",
                  r"une fois", r"et l[àa]"),
}
LAUGH = _lex(r"ha(?:ha)+h?", r"he(?:he)+", r"lmf?ao+", r"mdr", r"ptdr", r"\(laugh\w*\)", r"\[laugh\w*\]", r"laughs?")
PROMO = _lex(r"subscribe", r"sponsor\w*", r"link in (?:the )?description", r"use code", r"promo code",
             r"welcome back", r"hit the (?:like|bell)", r"smash (?:that|the) like", r"patreon", r"merch",
             r"hello everyone", r"hey guys", r"what'?s up guys", r"in today'?s video", r"in this video",
             r"thanks for watching", r"see you (?:in the )?next",
             r"abonne\w*", r"lien en description", r"code promo", r"pouce bleu", r"cloche",
             r"salut (?:à )?tout le monde", r"bonjour à tous", r"dans (?:cette|la) vid[ée]o", r"merci d'avoir regard[ée]")

# How each moment type shows up in measurable signals (weights on 0-100 features) plus how much
# its word list counts. Signal names: energy, jump (sudden loudness), motion, cuts (edit pace),
# laugh, exclaim, question, density (speech pace), numbers, hook, calm (slow editing).
CAT_RULES: dict[str, tuple[float, dict[str, float]]] = {
    "funny":     (35, {"laugh": 0.35, "energy": 0.15, "exclaim": 0.15}),
    "scary":     (30, {"jump": 0.40, "motion": 0.10, "energy": 0.15, "question": 0.05}),
    "action":    (25, {"motion": 0.35, "cuts": 0.25, "energy": 0.15}),
    "emotional": (50, {"calm": 0.25, "density": 0.15, "question": 0.10}),
    "shocking":  (40, {"jump": 0.25, "exclaim": 0.15, "question": 0.10, "energy": 0.10}),
    "romantic":  (60, {"calm": 0.25, "density": 0.15}),
    "hype":      (30, {"energy": 0.35, "exclaim": 0.20, "jump": 0.15}),
    "drama":     (40, {"energy": 0.30, "exclaim": 0.15, "question": 0.15}),
    "insight":   (30, {"density": 0.30, "numbers": 0.20, "hook": 0.20}),
    "story":     (40, {"density": 0.35, "hook": 0.10, "calm": 0.15}),
}


def with_gaps(sents: list[Sentence], duration: float, step: float = 3.0) -> list[Sentence]:
    """Sentences plus ~3 s "no speech" units for every long silence, so a clip can be a fight, a
    chase or a scare with little dialogue (films, gameplay)."""
    out: list[Sentence] = []
    prev = 0.0
    for s in [*sents, Sentence(duration, duration, [])]:
        gap = s.start - prev
        if gap > step:
            k = max(1, int(round(gap / step)))
            for i in range(k):
                out.append(Sentence(round(prev + gap * i / k, 3), round(prev + gap * (i + 1) / k, 3), []))
        if s.words:
            out.append(s)
        prev = max(prev, s.end)
    return out


def _ranks(values: list[float]) -> list[float]:
    """Percentile of each value among all of them (0-100), so features compare within this video."""
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return []
    order = arr.argsort(kind="stable")
    ranks = np.empty(arr.size)
    ranks[order] = np.arange(arr.size)
    # ties share the same rank
    _, inv, counts = np.unique(arr, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=ranks)
    ranks = sums[inv] / counts[inv]
    return list(ranks / max(arr.size - 1, 1) * 100.0)


SIGNAL_NOTES = {"laugh": "laughter", "jump": "sudden loud sound", "motion": "lots of motion", "cuts": "fast cuts",
                "energy": "high energy", "exclaim": "exclamations", "question": "questions",
                "density": "fast talking", "numbers": "numbers", "hook": "hook-like opening", "calm": "slow, intimate"}


def heuristic_moments(units: list[Sentence], n: int, min_s: float, max_s: float,
                      taken: list[dict] | None = None, sig: Signals | None = None,
                      mode: str = "viral", cats: list[str] | None = None) -> list[dict]:
    """Score every unit-aligned window on structure (hook, clean ending, no dead air), on how
    strongly it matches the wanted moment types (words + measured signals) and on how much
    viewers rewatched it, then keep the best non-overlapping ones."""
    cfg = MODES.get(mode, MODES["viral"])
    cats = [c for c in (cats or cfg["cats"]) if c in CATEGORIES] or cfg["cats"]
    w_base, w_cat, w_replay = cfg["weights"]

    # per-unit counts, so a window is a sum over its units
    per_unit = []
    for u in units:
        text = u.text
        per_unit.append({
            "words": len(u.words), "q": text.count("?"), "x": text.count("!"),
            "num": len(NUM.findall(text)), "laugh": len(LAUGH.findall(text)), "promo": len(PROMO.findall(text)),
            "lex": {c: len(LEXICON[c].findall(text)) for c in cats},
        })

    raw = []
    for i in range(len(units)):
        for j in range(i, len(units)):
            dur = units[j].end - units[i].start
            if dur > max_s:
                break
            if dur < min_s:
                continue
            chunk, counts = units[i:j + 1], per_unit[i:j + 1]
            spoken = [u for u in chunk if u.words]
            total = {k: sum(c[k] for c in counts) for k in ("words", "q", "x", "num", "laugh", "promo")}
            lex = {c: sum(cu["lex"][c] for cu in counts) for c in cats}
            words = [w for u in spoken for w in u.words]
            silence = sum(u.end - u.start for u in chunk if not u.words)
            silence += sum(max(0.0, b["start"] - a["end"] - 1.0) for a, b in zip(words, words[1:]))
            first = spoken[0] if spoken else None
            leans_back = bool(first and first is chunk[0] and _bare(first.words[0]["text"]) in LEANS_BACK)
            hook = bool(first and (first.text.rstrip().endswith("?") or NUM.search(first.text)
                                   or HOOK_WORDS.search(first.text)))
            s, e = chunk[0].start, chunk[-1].end
            raw.append({
                "i": i, "j": j, "start": s, "end": e, "dur": dur, "lex": lex, "total": total,
                "silence": silence / dur, "leans_back": leans_back, "hook": hook,
                "first": first.text if first else "",
                "end_cost": _end_cost(units, j, e),
                "density": total["words"] / dur,
                "energy": sig.energy(s, e) if sig else 50.0,
                "jump": sig.jump(s, e) if sig else 0.0,
                "motion": sig.motion(s, e) if sig else 0.0,
                "cuts": sig.cut_rate(s, e) if sig else 0.0,
                "replay": sig.replay(s, e) if sig else None,
            })
    if not raw:
        return list(taken or [])

    pct = {k: _ranks([r[k] for r in raw]) for k in ("energy", "jump", "motion", "cuts", "density")}
    have_video = bool(sig and sig.scenes is not None)
    windows = []
    for k, r in enumerate(raw):
        t = r["total"]
        feat = {
            "energy": pct["energy"][k], "jump": r["jump"] * 0.6 + pct["jump"][k] * 0.4,
            "motion": pct["motion"][k] if have_video else 0.0, "cuts": pct["cuts"][k] if have_video else 0.0,
            "calm": (100 - pct["cuts"][k]) if have_video else 50.0,
            "density": pct["density"][k], "laugh": min(t["laugh"], 2) * 50.0,
            "exclaim": min(t["x"], 3) / 3 * 100, "question": min(t["q"], 2) * 50.0,
            "numbers": min(t["num"], 3) / 3 * 100, "hook": 100.0 if r["hook"] else 0.0,
        }
        cat_scores = {}
        for c in cats:
            lex_w, mix = CAT_RULES[c]
            measured = sum(w * feat[f] for f, w in mix.items()) / sum(mix.values())  # 0-100
            cat_scores[c] = min(r["lex"][c], 3) / 3 * lex_w + measured * (100 - lex_w) / 100
        category = max(cat_scores, key=cat_scores.get)

        # structure: a clip that opens on a hook, ends cleanly and has no dead air
        dead = r["silence"] * (1 - feat["motion"] / 100 if have_video else 1.0) * (1 - feat["energy"] / 200)
        intro = r["start"] < max(45.0, units[-1].end * 0.03)  # greetings and set-up, rarely the best part
        base = (55 + (15 if r["hook"] and not r["leans_back"] else 0) - (20 if r["leans_back"] else 0) - 12 * intro
                - min(r["end_cost"] * 3, 30) - 45 * dead - min(t["promo"], 2) * 25
                + (6 if min_s + (max_s - min_s) * 0.2 <= r["dur"] <= max_s - (max_s - min_s) * 0.15 else 0))
        base = max(0.0, min(base, 100.0))
        wc, wr = w_cat, w_replay
        if r["replay"] is None:
            wc, wr = w_cat + w_replay, 0.0
        score = w_base * base + wc * cat_scores[category] + wr * (r["replay"] or 0.0)

        notes = [f"{CATEGORIES[category]['emoji']} {CATEGORIES[category]['label']}"]
        lex_w, mix = CAT_RULES[category]
        strong = sorted((f for f in mix if feat[f] >= 60), key=lambda f: -mix[f] * feat[f])[:2]
        if r["lex"][category]:
            notes.append(f"{CATEGORIES[category]['label'].lower()} words")
        notes += [SIGNAL_NOTES[f] for f in strong]
        if r["replay"] is not None and r["replay"] >= 60:
            notes.append("one of the most replayed parts on YouTube")
        if r["hook"] and not r["leans_back"]:
            notes.append("strong opening line")
        title_words = r["first"].split()
        windows.append({
            "start": r["start"], "end": r["end"],
            "title": (" ".join(title_words[:8]) + ("…" if len(title_words) > 8 else "")) or
                     f"{CATEGORIES[category]['label']} moment",
            "hook": r["first"], "score": round(max(0.0, min(score, 100.0)), 1),
            "category": category, "cat_scores": {c: round(v, 1) for c, v in cat_scores.items()},
            "reason": "Picked for: " + ", ".join(dict.fromkeys(notes)) + ".",
            "source": "heuristic",
        })
    return remove_overlaps(windows, n, taken)


# ------------------------------------------------------------------ LLM providers

class LLMProvider(Protocol):
    name: str

    def check(self) -> str | None:
        """Return None when ready, or a human-readable reason why it cannot be used."""

    def chat(self, messages: list[dict], schema: dict) -> str:
        ...

    def release(self) -> None:
        ...


class OllamaProvider:
    def __init__(self) -> None:
        c = CONFIG["moments"]["ollama"]
        self.url = c["url"].rstrip("/")
        self.model = c["model"]
        self.cfg = c
        self.name = f"ollama:{self.model}"
        self._think_flag = True

    def check(self) -> str | None:
        try:
            tags = requests.get(f"{self.url}/api/tags", timeout=3).json()
        except requests.RequestException:
            return f"Ollama is not running at {self.url}"
        names = {m["name"] for m in tags.get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
            return f"Ollama model '{self.model}' is not pulled (run: ollama pull {self.model})"
        return None

    def chat(self, messages: list[dict], schema: dict) -> str:
        body = {
            "model": self.model, "messages": messages, "stream": False, "format": schema,
            "options": {"temperature": self.cfg["temperature"], "num_ctx": self.cfg["num_ctx"]},
        }
        if self._think_flag:
            body["think"] = False  # qwen3 & co: answer directly, no reasoning preamble
        r = requests.post(f"{self.url}/api/chat", json=body, timeout=self.cfg["timeout"])
        if r.status_code == 400 and "think" in r.text and self._think_flag:
            self._think_flag = False  # model does not support the flag
            return self.chat(messages, schema)
        if r.status_code != 200:
            raise RuntimeError(f"Ollama HTTP {r.status_code}: {r.text[:300]}")
        return r.json()["message"]["content"]

    def release(self) -> None:
        if self.cfg.get("unload_after", True):
            try:
                requests.post(f"{self.url}/api/generate", json={"model": self.model, "keep_alive": 0}, timeout=10)
            except requests.RequestException:
                pass


PROVIDERS: dict[str, Callable[[], LLMProvider]] = {
    "ollama": OllamaProvider,
}


# ------------------------------------------------------------------ LLM picking

def moment_schema(cats: list[str]) -> dict:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "start": {"type": "number"}, "end": {"type": "number"},
                "title": {"type": "string"}, "hook": {"type": "string"},
                "category": {"type": "string", "enum": cats},
                "hook_score": {"type": "integer"}, "standalone_score": {"type": "integer"},
                "payoff_score": {"type": "integer"}, "emotion_score": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["start", "end", "title", "hook", "category", "hook_score", "standalone_score",
                         "payoff_score", "emotion_score", "reason"],
        },
    }


RANK_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "score": {"type": "number"}, "reason": {"type": "string"}},
        "required": ["id", "score", "reason"],
    },
}

PICK_SYSTEM = """You are an expert short-form video editor. From a transcript you choose moments that work as standalone vertical clips (TikTok, Reels, Shorts).

{focus}

Only pick moments of these kinds, and label each one with its kind:
{categories}
Be picky: a clip must be a real highlight, the kind of moment people share. Ten average moments are worth less than three great ones. Avoid intros, greetings, sponsor reads, calls to subscribe, recaps and outros.

A good moment:
- lasts between {min_s} and {max_s} seconds,
- starts at the beginning of a sentence or a scene, with a first line (the hook) that makes people stay,
- contains one complete beat: a joke and its punchline, a build-up and its payoff, a story, an argument,
- ends cleanly on a finished sentence or the end of the action, ideally a payoff, reveal or punchline,
- makes sense to someone who never saw the rest of the video (no "as I said", no unexplained "he"/"it").

Transcript lines look like "[start-end] text" with times in seconds, one sentence per line. Some lines end
with cues measured on the audio and video, like <loud, sudden loud sound, fast cuts, most replayed>:
- "sudden loud sound": a scream, a crash, a shot, a jump scare or a burst of laughter
- "loud": people shouting, cheering or laughing
- "fast cuts" / "lots of motion": action on screen
- "most replayed": YouTube viewers rewatched this part the most, it is a proven highlight
Lines "(no speech)" are stretches without dialogue. Use the cues: they show what the words cannot.

A moment almost always spans several consecutive lines: start = the start time of its first line, end = the end
time of its last line, and end - start must be between {min_s} and {max_s}. Never start on a line that only makes
sense after the previous one (starting with "and", "but", "because", "which", or "he"/"it" with no one
introduced); start one line earlier instead. Never end on a question whose answer is on the next line.

Score every moment honestly with this rubric. Most moments are average; keep high marks for the exceptional.
- hook_score 0-25: would the first seconds stop someone scrolling?
- standalone_score 0-25: does it make complete sense with zero context?
- payoff_score 0-25: does it end on a punchline, reveal, scare, win or strong conclusion?
- emotion_score 0-25: how strongly does it make people laugh, jump, cry, gasp or feel hyped?

Answer with strict JSON only: an array of objects
[{{"start": number, "end": number, "title": string, "hook": string, "category": string, "hook_score": integer, "standalone_score": integer, "payoff_score": integer, "emotion_score": integer, "reason": string}}]
- title: catchy, at most 60 characters, in the transcript's language
- hook: the first line of the clip, quoted from the transcript
- category: one of {cat_ids}
- reason: one sentence explaining why it works
No markdown, no comments, nothing outside the JSON array."""

RANK_SYSTEM = """You are an expert short-form video editor. You receive candidate clips found in different parts of one video. {focus}
Rank them by how well each would perform as a standalone TikTok/Reels/Shorts clip: how strong the moment is ({categories}), the hook, a complete beat with a payoff, makes sense without context. "replayed" is how much YouTube viewers rewatched it (0-100) when known: trust it.

Answer with strict JSON only: an array of the best {n} candidates, best first:
[{{"id": integer, "score": number, "reason": string}}]
- id: the candidate id you were given
- score: 0 to 100
- reason: one sentence explaining why it works
No markdown, nothing outside the JSON array."""


class InvalidOutput(ValueError):
    pass


def _parse_json_array(text: str) -> list:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        lo, hi = text.find("["), text.rfind("]")
        if lo == -1 or hi <= lo:
            raise InvalidOutput("the answer is not valid JSON")
        try:
            data = json.loads(text[lo:hi + 1])
        except json.JSONDecodeError as exc:
            raise InvalidOutput(f"the answer is not valid JSON ({exc})")
    if isinstance(data, dict):  # tolerate {"moments": [...]}
        data = next((v for v in data.values() if isinstance(v, list)), None)
    if not isinstance(data, list):
        raise InvalidOutput("the answer must be a JSON array")
    return data


RUBRIC = ("hook", "standalone", "payoff", "emotion")


def _validate_moments(items: list, lo: float, hi: float, min_s: float,
                      cats: list[str] | None = None) -> tuple[list[dict], list[str]]:
    good, problems = [], []
    for k, it in enumerate(items):
        try:
            if not isinstance(it, dict):
                raise InvalidOutput("not an object")
            start, end = float(it["start"]), float(it["end"])
            if all(f"{r}_score" in it for r in RUBRIC):
                parts = {r: max(0.0, min(float(it[f"{r}_score"]), 25.0)) for r in RUBRIC}
                score = sum(parts.values())
            else:
                parts, score = {}, float(it["score"])
            title, hook, reason = (str(it[key]).strip() for key in ("title", "hook", "reason"))
        except (KeyError, TypeError, ValueError, InvalidOutput) as exc:
            problems.append(f"item {k}: missing or wrong field ({exc})")
            continue
        if not (lo - 5 <= start < end <= hi + 5):
            problems.append(f"item {k}: start/end {start}-{end} outside the transcript range {lo:.0f}-{hi:.0f}")
            continue
        # A moment that is too short is kept: small models often copy one line's times, and the
        # snapping step grows it to whole sentences of the wanted length. Retrying costs minutes.
        if end - start > 150:
            problems.append(f"item {k}: length {end - start:.0f}s is far too long")
            continue
        category = str(it.get("category") or "").strip().lower()
        good.append({"start": start, "end": end, "title": title[:80] or "Untitled", "hook": hook,
                     "score": score, "reason": reason, "rubric": parts,
                     "category": category if category in (cats or CATEGORIES) else None})
    if good and not good[0]["rubric"] and max(m["score"] for m in good) <= 10:  # 0-10 scale
        for m in good:
            m["score"] *= 10
    for m in good:
        m["score"] = round(max(0.0, min(m["score"], 100.0)), 1)
    return good, problems


def _ask(provider: LLMProvider, system: str, user: str, schema: dict, validate: Callable[[list], tuple[list, list]],
         log: Log, label: str) -> list:
    """One request with up to 2 retries that feed the validation error back to the model."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(3):
        raw = provider.chat(messages, schema)
        log(f"{label} attempt {attempt + 1} raw answer: {raw[:2000]}")
        try:
            good, problems = validate(_parse_json_array(raw))
            if problems:
                log(f"{label}: dropped invalid items: {'; '.join(problems)}")
            if good:
                return good
            error = "no valid items. " + "; ".join(problems[:5])
        except InvalidOutput as exc:
            error = str(exc)
        log(f"{label}: invalid output ({error}), retrying")
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": f"Your answer was invalid: {error}. "
                                                 "Answer again with only the JSON array, following every rule."}]
    raise InvalidOutput(f"{label}: no valid answer after 3 attempts")


def unit_cues(units: list[Sentence], sig: Signals | None) -> list[list[str]]:
    """Per transcript line, what the audio, the picture and the viewers say about it. A cue is only
    given to the top ~8 % of lines for that signal (and above an absolute floor), so it means something."""
    if sig is None or not units:
        return [[] for _ in units]
    feats = {
        "loud": [sig.energy(u.start, u.end) for u in units],
        "sudden loud sound": [sig.jump(u.start - 0.5, u.end) for u in units],
        "lots of motion": [sig.motion(u.start, u.end) for u in units],
        "fast cuts": [sig.cut_rate(u.start - 1.0, u.end + 1.0) for u in units],
        "most replayed": [sig.replay(u.start, u.end) or 0.0 for u in units],
    }
    floors = {"loud": 68, "sudden loud sound": 45, "lots of motion": 70, "fast cuts": 50, "most replayed": 70}
    out: list[list[str]] = [[] for _ in units]
    for name, vals in feats.items():
        arr = np.asarray(vals)
        if not arr.size or float(arr.max()) <= 0:
            continue
        cut = max(float(np.percentile(arr, 92)), floors[name])
        for k, v in enumerate(vals):
            if v >= cut:
                out[k].append(name)
    return out


def _lines(units: list[Sentence], cues: list[list[str]] | None = None,
           lo: float = 0.0, hi: float = float("inf")) -> list[str]:
    """One line per sentence (not per Whisper segment, which often stops mid-sentence), so the
    start/end times the LLM copies are already sentence boundaries. Silences become "(no speech)"
    lines, merged while their cues stay the same."""
    cues = cues or [[] for _ in units]
    out: list[list] = []  # [start, end, text, cues]
    for u, c in zip(units, cues):
        if u.end <= lo or u.start >= hi:
            continue
        if not u.words and out and out[-1][2] == "(no speech)" and out[-1][3] == c and u.end - out[-1][0] < 12:
            out[-1][1] = u.end
            continue
        out.append([u.start, u.end, u.text or "(no speech)", c])
    return [f"[{s:.1f}-{e:.1f}] {t}" + (f"  <{', '.join(c)}>" if c else "") for s, e, t, c in out]


def _chunks(duration: float, lines: list[str], units: list[Sentence]) -> list[tuple[float, float]]:
    """Whole transcript if it is short and fits the context window, else overlapping ~10 minute
    windows. Small local models mostly pick from the start of a long list, so a long video is
    always split: every part gets its own look, then the candidates are ranked together."""
    c = CONFIG["moments"]["ollama"]
    chars = sum(len(line) for line in lines)
    budget_tokens = int(c["num_ctx"]) - 3000  # room for the instructions and the answer
    if chars / 3.0 <= budget_tokens and duration <= c["chunk_minutes"] * 60.0 * 1.5:
        return [(0.0, duration)]
    size, overlap = c["chunk_minutes"] * 60.0, c["chunk_overlap_minutes"] * 60.0
    out, lo = [], 0.0
    while lo < duration:
        out.append((lo, min(lo + size, duration)))
        if lo + size >= duration:
            break
        lo += size - overlap
    return out


def _category_text(cats: list[str]) -> str:
    return "\n".join(f"- {c}: {CATEGORIES[c]['llm']}" for c in cats)


def llm_moments(provider: LLMProvider, transcript: dict, words: list[dict], units: list[Sentence], n: int,
                log: Log, progress: Progress, min_s: float, max_s: float, sig: Signals | None,
                mode: str, cats: list[str]) -> list[dict]:
    cfg = MODES[mode]
    duration = transcript["duration"] or transcript["segments"][-1]["end"]
    system = PICK_SYSTEM.format(min_s=int(min_s), max_s=int(max_s), focus=cfg["focus"],
                                categories=_category_text(cats), cat_ids=", ".join(cats))
    cues = unit_cues(units, sig)
    chunks = _chunks(duration, _lines(units, cues), units)
    want = n or 6
    per_chunk = min(want + 2, 10) if len(chunks) == 1 else min(max(3, want), 6)
    log(f"llm: {provider.name}, mode {mode}, kinds {cats}, {len(chunks)} chunk(s), asking {per_chunk} each")
    candidates: list[dict] = []
    for idx, (lo, hi) in enumerate(chunks):
        progress(idx / (len(chunks) + 1), f"{provider.name} · part {idx + 1}/{len(chunks)}")
        user = (f"Transcript (language: {transcript['language']}), part {lo:.0f}s to {hi:.0f}s:\n"
                + "\n".join(_lines(units, cues, lo, hi))
                + f"\n\nReturn the {per_chunk} best moments as a JSON array (fewer if there are not that many real highlights).")
        try:
            found = _ask(provider, system, user, moment_schema(cats),
                         lambda items: _validate_moments(items, lo, hi, min_s, cats), log, f"chunk {idx + 1}")
        except InvalidOutput as exc:
            log(str(exc))
            continue
        for c in found:
            c["source"] = provider.name
        candidates += found
    if not candidates:
        raise InvalidOutput("the LLM returned no usable moments")

    if len(chunks) > 1 and len(candidates) > want:
        progress(len(chunks) / (len(chunks) + 1), f"{provider.name} · ranking {len(candidates)} candidates")
        listing = []
        for k, c in enumerate(candidates):
            item = {"id": k, "start": round(c["start"], 1), "end": round(c["end"], 1), "kind": c.get("category"),
                    "title": c["title"], "hook": c["hook"], "text": text_between(words, c["start"], c["end"])[:700]}
            rep = sig.replay(c["start"], c["end"]) if sig else None
            if rep is not None:
                item["replayed"] = round(rep)
            listing.append(item)

        def validate_rank(items: list) -> tuple[list, list]:
            good, problems, seen = [], [], set()
            for it in items:
                try:
                    cid, score = int(it["id"]), float(it["score"])
                except (KeyError, TypeError, ValueError):
                    problems.append(f"bad item {it!r}"[:120])
                    continue
                if cid in seen or not 0 <= cid < len(candidates):
                    problems.append(f"unknown or repeated id {cid}")
                    continue
                seen.add(cid)
                good.append({"id": cid, "score": score, "reason": str(it.get("reason", "")).strip()})
            return good, problems

        try:
            ranked = _ask(provider, RANK_SYSTEM.format(n=want, focus=cfg["focus"], categories=", ".join(cats)),
                          "Candidates:\n" + json.dumps(listing, ensure_ascii=False, indent=1)
                          + f"\n\nReturn the {want} best as a JSON array.", RANK_SCHEMA, validate_rank, log, "ranking")
            if max(r["score"] for r in ranked) <= 10:
                for r in ranked:
                    r["score"] *= 10
            for r in ranked:
                c = candidates[r["id"]]
                c["score"] = round(max(0.0, min(r["score"], 100.0)), 1)
                if r["reason"]:
                    c["reason"] = r["reason"]
                c["ranked"] = True
            # unranked candidates stay available as backups, below every ranked one
            for c in candidates:
                if not c.get("ranked"):
                    c["score"] = min(c["score"], min(r["score"] for r in ranked) - 1)
        except InvalidOutput as exc:
            log(f"{exc}; keeping per-chunk scores")
    return candidates


# ------------------------------------------------------------------ entry point

LENGTHS = {"short": (15.0, 30.0), "long": (45.0, 90.0)}
AUTO_MAX = 8


def get_provider() -> tuple[LLMProvider | None, str | None]:
    """The configured LLM provider when it is usable, else (None, reason)."""
    name = CONFIG["moments"]["provider"]
    if name == "heuristic":
        return None, "heuristic picker selected in config.yaml"
    factory = PROVIDERS.get(name)
    if factory is None:
        return None, f"unknown moments.provider '{name}'"
    provider = factory()
    problem = provider.check()
    return (None, problem) if problem else (provider, None)


def signal_scores(m: dict, sig: Signals | None) -> dict:
    """Measured (not LLM-judged) signals, each 0-100: vocal energy and speaking pace, plus sudden
    sounds, motion and YouTube replays when they were measured."""
    words = len(m["text"].split())
    out = {"pace": round(min(words / max(m["end"] - m["start"], 1.0) / 3.2, 1.0) * 100, 1),
           "energy": round(sig.energy(m["start"], m["end"]), 1) if sig else 50.0}
    if sig is not None:
        if sig.spike is not None:
            out["jump"] = round(sig.jump(m["start"], m["end"]), 1)
        if sig.scenes is not None:
            out["motion"] = round(sig.motion(m["start"], m["end"]), 1)
        rep = sig.replay(m["start"], m["end"])
        if rep is not None:
            out["replay"] = round(rep, 1)
    return out


def measured_score(signals: dict, mode: str) -> float:
    """One 0-100 number from the measured signals, weighted for the kind of video."""
    if mode in ("film", "gaming"):
        activity = max(signals["energy"], signals.get("jump", 0), signals.get("motion", 0))
    else:
        activity = (signals["energy"] + signals["pace"]) / 2
    rep = signals.get("replay")
    return activity if rep is None else 0.4 * activity + 0.6 * rep


def clip_lengths(length: str = "normal") -> tuple[float, float]:
    """(min, max) seconds for a clip length choice; "normal" is moments.min/max_seconds."""
    m = CONFIG["moments"]
    return LENGTHS.get(length) or (float(m["min_seconds"]), float(m["max_seconds"]))


def pick_moments(transcript: dict, n: int, log: Log, progress: Progress,
                 sig: Signals | None = None, provider: LLMProvider | None = None,
                 provider_problem: str | None = None, length: str = "normal",
                 mode: str = "viral", cats: list[str] | None = None) -> tuple[list[dict], str]:
    """LLM candidates (when a provider is given) snapped to sentences and scored as 70 % LLM rubric
    + 30 % measured signals, topped up with heuristic picks. n = 0 means "auto": only the moments
    that come close to the best one."""
    mode = mode if mode in MODES else "viral"
    cats = [c for c in (cats or []) if c in CATEGORIES] or MODES[mode]["cats"]
    min_s, max_s = clip_lengths(length)
    duration = transcript["duration"] or transcript["segments"][-1]["end"]
    words = all_words(transcript)
    sents = split_sentences(words)
    units = with_gaps(sents, duration)
    to_cuts = mode in ("film", "gaming", "youtuber")
    log(f"moments: {len(words)} words, {len(sents)} sentences, {len(units)} units, clips of "
        f"{min_s:.0f}-{max_s:.0f}s, mode {mode}, kinds {cats}, {'auto count' if not n else n}, "
        f"signals {sig.available if sig else 'none'}")
    want = n or AUTO_MAX

    def finish(s: float, e: float) -> tuple[float, float]:
        return snap_to_cuts(s, e, words, sig, duration, max_s) if to_cuts else (s, e)

    picker = f"heuristic ({provider_problem})" if provider_problem else "heuristic"
    picked: list[dict] = []
    if provider is not None:
        try:
            raw = llm_moments(provider, transcript, words, units, n, log, progress, min_s, max_s, sig, mode, cats)
            snapped = []
            for c in raw:
                span = snap(c["start"], c["end"], units, words, min_s, max_s, duration)
                if not span:
                    continue
                span = finish(*span)
                c = {**c, "start": span[0], "end": span[1]}
                c["text"] = text_between(words, c["start"], c["end"])
                c["signals"] = signal_scores(c, sig)
                c["llm_score"] = c["score"]
                c["score"] = round(0.7 * c["score"] + 0.3 * measured_score(c["signals"], mode), 1)
                log(f"snap {span[0]:.2f}-{span[1]:.2f} {c.get('category')} llm={c['llm_score']} "
                    f"signals={c['signals']} -> {c['score']}  {c['title']}")
                snapped.append(c)
            picked = remove_overlaps(snapped, want)
            if not n:
                picked = sorted(picked, key=lambda p: p["score"], reverse=True)[:auto_count(picked, duration, AUTO_MAX)]
            picker = provider.name
        except (requests.RequestException, InvalidOutput, RuntimeError, KeyError) as exc:
            log(f"LLM picking failed ({exc!r}); using the heuristic picker")
            picker = "heuristic (LLM failed, see log)"

    if n and len(picked) < n or not n and not picked:
        before = len(picked)
        progress(0.9, "scoring every possible moment")
        found = heuristic_moments(units, want, min_s, max_s, taken=picked, sig=sig, mode=mode, cats=cats)
        new = [m for m in found if m not in picked]
        for m in new:
            m["start"], m["end"] = finish(*pad(m["start"], m["end"], words, duration))
        if not n:
            new = sorted(new, key=lambda p: p["score"], reverse=True)[:auto_count(new, duration, AUTO_MAX)]
        picked = picked + new
        if before and len(picked) > before:
            picker += " + heuristic"
            log(f"filled {len(picked) - before} slot(s) with heuristic picks")
    if not picked:  # video shorter than min_s or no sentence windows: take what there is
        span = snap(0.0, min(duration, max_s), units, words, 0.0, max_s, duration)
        if span:
            first = next((u.text for u in units if u.words), "Clip")
            picked = [{"start": span[0], "end": span[1], "title": first[:60], "hook": first,
                       "score": 0.0, "reason": "The video is too short to choose between moments.",
                       "source": "heuristic"}]
    for p in picked:
        p["text"] = text_between(words, p["start"], p["end"])
        p["duration"] = round(p["end"] - p["start"], 3)
        p.setdefault("rubric", {})
        p.setdefault("signals", signal_scores(p, sig))
        cat = p.get("category")
        if cat in CATEGORIES:
            p["tag"] = {"id": cat, "label": CATEGORIES[cat]["label"], "emoji": CATEGORIES[cat]["emoji"]}
    picked.sort(key=lambda p: p["score"], reverse=True)
    return picked, picker
