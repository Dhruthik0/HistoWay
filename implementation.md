# Histoway — Development Plan (DRAFT)

## Context

On a road trip from Bangalore to Leh, you passed hundreds of places without knowing their history or why they mattered. Histoway is meant to fix that. While you travel, it should notice what you are approaching and tell you a short, balanced, well-sourced story about it, out loud and without any taps. The first version has to cost nothing to build and run.

**The question to answer first:** "While I travel, does the app tell me interesting, relevant stories about the places I pass, without my stopping to search?" Anything that doesn't help answer this is left out of the MVP.

**Decisions you've made:**
- **Where stories are generated:** both ways. Stories are mainly pre-generated on the laptop before a trip. A live cloud LLM is the fallback for places that weren't cached or when you leave the route.
- **LLM:** you can switch between your AI Bridge (`/home/8265k/Desktop/my_proj/free_chatGpt_Api`), Gemini and Grok (xAI).
- **TTS:** *(changed after Phase 0)* every story uses the Microsoft Edge voice **en-IN-NeerjaNeural**, pre-rendered to MP3 on the laptop with `edge-tts`. The phone's built-in speech is only the fallback when an MP3 is missing. Deepgram Aura stays a switchable option for later. Whisper is kept for future voice questions, because it is speech-to-text, not TTS.
- **Platform:** a PWA (web app).

**Assumptions I made (not verified):**
- You will run the PWA in Chrome on Android, with the phone in a mount and the screen on.
- You and a few testers are the only users at first. This is not a public launch.
- The narration is in English. Other languages come later.

---

## 1. Can your AI Bridge be the LLM?

**Yes, but only in the laptop pre-generation step. It can't be called live from the phone.** I checked this in the Bridge's code and README:

| Fact (verified) | What it means for Histoway |
|---|---|
| It only accepts connections from the laptop itself (127.0.0.1). It also rejects requests addressed to any other host name, which protects against DNS rebinding (`api.py:18,55`). | A phone can't reach it, not even on the same Wi-Fi. I'm not suggesting you loosen this. It is the Bridge's main security protection. |
| It handles one request at a time, each taking about 8–10 s, and gets roughly 100–200 requests a day before ChatGPT's account limits apply. | That is too slow for live narration. It is fine for overnight batch work. |
| Each request is limited to 32 KB. `timeout` can be set anywhere from 5 to 900 s. | You can send source text for 3–4 places in one request. |
| Typed errors: `CHATGPT_RATE_LIMITED` (429), `BUSY`, `CHATGPT_NOT_AUTHENTICATED`, `CHATGPT_CHALLENGE_REQUIRED`. | The pipeline must slow down for minutes after a 429, never retry right away. On an auth error or Cloudflare challenge it should stop and ask you to step in. |
| It is against OpenAI's terms of use, and you use it at your own risk. | It's fine for a personal prototype. **It can't serve a public product.** Gemini or Grok have to take over before any public launch. |

How Histoway calls it: `POST http://127.0.0.1:8756/ask` with `{"instruction", "input", "timeout": 300}` and the bearer token read from `~/.config/ai-bridge/config.toml`. This matches the README's own Python example. **The Bridge itself doesn't change.**

**Request budget (I checked the arithmetic):**
- **Bangalore → Mysore:** about 30–40 selected places. At 3 places per request, that is about 10–14 requests, which takes minutes.
- **Bangalore → Leh:** roughly 3,000+ km by road. At about 1 place per 10 km outside cities, that is about 300 places, or about 100 requests. With 20 s of pacing between requests, each request cycle takes about 30 s, so 100 × 30 s ≈ 50 min.
- **What that rules out:** a second LLM call per place to verify the story would double this to about 200 requests, which hits the Bridge's daily limit. So in the MVP, verification is rule-based (§7), and LLM-based verification uses a different provider later.
- **Stories are cached by place**, keyed by Wikidata ID and prompt version. A place is generated once and reused on later trips, including the return journey.

---

## 2. Product definition

**Histoway is an audio storyteller for the road.** Before a trip, it builds a "trip pack" of stories about the notable places along your route. During the trip, it plays each story as you approach the place. The stories are balanced (both good and bad history), labeled by how certain they are, and linked into one continuous journey narrative.

**Not in scope:** a navigation app, a map app, a booking or review app, or a visual guidebook.

---

## 3. Core user experience and journey (MVP)

1. **Plan the trip (laptop, once):** run `python -m histoway build "Bangalore" "Mysore"`. You get `trip_bangalore_mysore.json`, which holds the route, the ordered list of places and the story scripts. It takes about 5–15 minutes.
2. **Load the pack:** open the PWA on the phone and import the pack, either by file picker or by fetching it from the static host. The pack is saved on the phone (IndexedDB), and from then on it works without internet.
3. **Start driving:** tap **Start journey**. The screen stays on (Wake Lock), GPS tracking begins, and you hear a short opening ("Today's road to Mysore runs through two kingdoms' worth of history…").
4. **While driving:** as you approach Lalbagh, a soft chime plays, then: "You're approaching Lalbagh…". The story is 30 s to 3 min long depending on how important the place is.
5. **Controls:** there are only four big buttons: **Pause/Resume**, **Skip**, **Replay**, and **Tell me more**. Tell me more plays the longer version if one exists.
6. **Places you've already heard** are never repeated. On the return trip they are skipped, or replaced by a single "on your left again is…" line.
7. **After the trip, postponed:** a recap listing every place you heard, with its sources.

There is no account and no sign-in. The phone stores nothing beyond the pack and a "places already told" log.

---

## 4. Features: MVP vs later

| Feature | What it does | Why it's needed / what problem it solves | How the user experiences it | Stage |
|---|---|---|---|---|
| **Route planner** | Takes an origin and destination, gets the route line, and builds a corridor around it | You only want stories for places along your road | Runs as a CLI command on the laptop | **MVP** |
| **Place discovery** | Finds candidate places in the corridor using Wikidata and OpenStreetMap | Covers more than tourist spots: forts, rivers, battlefields, birthplaces | Invisible to the user | **MVP** |
| **Place selection (scoring)** | Ranks candidates and keeps a limited number per stretch of road | Stops the app from talking all the time or narrating every bus stop | You hear a few worthwhile stories, not a stream of them | **MVP** |
| **Source gathering** | Pulls Wikipedia text and Wikidata facts for each place | Stories are based on sources rather than made up by the LLM | Spoken sources are optional; written sources appear in the pack | **MVP** |
| **Story generation** | The LLM turns the sources into a conversational script with labeled claims | Turns an encyclopedia entry into a story | "You're passing…" told like a person would | **MVP** |
| **Certainty labeling** | Tags each claim as verified, widely accepted, legend, disputed or interpretation, and phrases each type differently | Keeps myths from being presented as facts | "Local legend says…", "Historians disagree on…" | **MVP** |
| **Balanced and dark history** | Requires the bad side of a place's history to be included when the sources cover it | Gives a balanced picture rather than a glorified one | Covered within the story, not as a separate mode | **MVP** (pre-generated story); a "tell me the dark history" button comes later |
| **Switchable LLM providers** | Bridge, Gemini or Grok, chosen by a setting | Free tiers change and the Bridge can break | One line of configuration | **MVP** |
| **GPS trigger engine** | Works out where you are along the route and starts each story on time | The core "no tapping" promise | Stories start before you reach the place | **MVP** |
| **Repetition control** | A permanent "already told" log, a minimum gap between stories, one story at a time | Avoids repeating places and overwhelming you | Calm pacing | **MVP** |
| **Device TTS** | The phone's built-in speech engine reads the scripts | Free and works offline | Works with no setup | **MVP** |
| **Offline playback** | The PWA, the pack and the speech all work without data | Highways in the hills and Ladakh have no signal | Works without signal | **MVP** |
| **GPX simulator** | Replays a recorded or synthetic route at 1–20× speed | Lets you test without driving 150 km each time | A developer-only button | **MVP** (for testing) |
| **Live fallback** | When you leave the route or a place has no story, uses Wikipedia's nearby-places search and Gemini or Grok to make one on the fly | Detours and unplanned trips | Same experience, needs mobile data | **Phase 2** |
| **Journey threads** | Links stories together, e.g. "Remember Tipu at Lalbagh? This is where his story ends" | Makes the trip feel like one story instead of separate articles | Callbacks between stories | **Phase 2** (MVP only passes the names of earlier places to the prompt) |
| **Region transitions** | Short stories when you cross a river, district or state line, or enter a mountain range | Geography explains why a town is where it is | "You've just crossed the Kaveri…" | **Phase 2** |
| **Deepgram TTS** | Higher-quality voice, pre-rendered to MP3 on the laptop | Sounds more human | A better voice | **Phase 2** (uses signup credit, see §12) |
| **Voice questions** | "What is that building?" using Whisper speech-to-text and the LLM | The future "ask the guide" experience | Push-to-talk | **Phase 3** |
| **Trip recap / journal** | A list of places heard, with sources and links to read more | Something to keep after the trip, and a way to check claims | Opened after the trip | **Phase 3** |
| **Moods or personas** | Kid-friendly mode, deep-history mode, "unusual facts only" | Personalization | A setting | **Postpone** |
| **Accounts, sync, sharing, community corrections, multiple languages, iOS** | — | Only needed for scale | — | **Postpone** |

---

## 5. How location-based discovery works

**At pre-generation time (laptop):**
1. **Geocode** the origin and destination with Nominatim (no more than 1 request per second, per its usage policy).
2. **Route** with OSRM's public demo server, which is free with fair use. Store the route line and the cumulative distance in km at each point.
3. **Corridor:** keep places within about 1 km of the route in cities and about 3 km on highways. Large features can be farther away: mountains, rivers, lakes and big forts up to about 10–15 km. These are configuration settings, not code.
4. **Candidate queries, run on segments about 20 km long:**
   - **Wikidata SPARQL:** items with coordinates in the segment's bounding box that have an English Wikipedia article. Fetch their type, number of Wikipedia language editions, inception date and notable events.
   - **Overpass (OpenStreetMap):** `historic=*`, `tourism=attraction|museum`, `natural=peak|ridge|cliff|cave_entrance`, `waterway=river`, `memorial`, `ruins`, `fort`, but only those with a `wikidata` tag. That keeps OSM objects matched to their Wikidata entries and avoids noise.
5. **Position along the route:** project each place onto the route line, which gives its **km marker** and which side of the road it is on (left or right).

**At drive time (phone):**
- `watchPosition` provides GPS position and speed, and the app projects the position onto the route line to get your **current km marker**.
- A story starts when `place_km − current_km ≤ speed × lead_time + intro_buffer`. `lead_time` is about 30 s, so stories start before you pass. Short stories can still run after you pass ("You just passed…"). Long stories need to start earlier.
- **Off-route:** if you are more than 500 m from the route line for more than 60 s, switch to live-fallback mode (Phase 2). Until then, stay quiet.

---

## 6. How places are selected

```
score = 3·log(1 + wikipedia_language_count)   # how notable the place is overall
      + 1·log(1 + article_length_kb)           # how much there is to say
      + type_bonus                              # fort/battle/birthplace/geology +; shop/station −
      + hook_bonus                              # article has History/Controversy/Legend sections
      − distance_penalty(distance_from_route, type)
```

- **Budget:** at most N stories per 10 km (about 1 on highways, 3–4 in old city centers). Within each window, keep the highest scores. All weights are settings in `config.toml`, so tuning doesn't need code changes.
- **Length tier from score:** **S** about 60 words (about 25 s at 150 words per minute), **M** about 180 words (about 70 s), **L** about 400 words (about 2.7 min). Only L places get a "Tell me more" version.
- **Places next to each other**, such as Vidhana Soudha and the High Court/Attara Kacheri across the road, are merged into one story if they are within about 300 m and score similarly.
- **Manual overrides:** `include.txt` and `exclude.txt` lists of Wikidata IDs, so you can tune with your own judgement.

---

## 7. Gathering and checking information

- **Sources (free):**
  - Wikipedia REST/Action API, for intro text and the History, Controversy and Legend sections.
  - Wikidata, for structured facts.
  - Wikivoyage, optionally, for local color.
- Each source excerpt is saved with its URL and revision ID.
- **The story rule:** the LLM may only use the source text it is given. If something isn't in the sources, it isn't in the story.
- **The LLM's output is structured JSON.** It is parsed and validated, and retried once if invalid:
  ```json
  {"script": "...", "more_script": "...|null",
   "claims": [{"text": "...", "kind": "verified|widely_accepted|legend|disputed|interpretation", "source": "url"}],
   "has_dark_history": true}
  ```
- **Rule-based checks, with no extra LLM calls:**
  - Every year, number and proper name in the script must appear in the source text. Unmatched items are flagged.
  - Stories that are flagged, or that have no claims, are marked `needs_review` and are **not played** until you approve them.
- **Later:** a second-pass LLM fact check on a *different* provider (Gemini) so the Bridge's daily limit isn't used up, plus a simple review screen.
- **How each label is phrased** (written into the prompt):
  - verified: stated plainly
  - widely accepted: "It's generally held that…"
  - legend: "Local legend says…"
  - disputed: "Accounts differ: some say…, others…"
  - interpretation: "Some historians see this as…"

## 8. Positive, negative and controversial content

- The prompt has to cover the significant bad side of a place when the sources mention it: war, exploitation, disasters, environmental damage, demolition or displacement. Leaving it out is treated as a defect. The prompt must not invent bad history either.
- **Contested figures and religious history get extra care.** Example: Tipu Sultan and Srirangapatna are politically contentious in Karnataka today. For places like this, the rule is to attribute each view ("Some historians…", "Others point to…"), avoid a verdict, avoid inflammatory wording, and stick to what the sources say. These places get a `sensitive` flag and are always manually reviewed in the MVP.
- Stories about deaths or tragedies (accident sites, massacres) use a calm tone and no "fun fact" framing.

## 9. How stories are generated

- **Prompt inputs:**
  - the trip context: route, current stretch of road, the 2 places told before this one (for continuity)
  - the place's type and size
  - the source excerpts, trimmed to about 6 KB each
  - the length tier
  - the side of the road
  - the style rules
- **Style rules:**
  - Start with what you can see or where you are ("On your left…", "The ridge ahead…").
  - Then explain why the place matters, with one surprising detail, the difficult history if there is any, and a short close.
  - Use spoken English: short sentences, no lists, no brackets, no URLs.
- **Batching:** 3 places per Bridge request (each request is limited to 32 KB), with the reply returned as a JSON array.
- Prompts are stored as files: `prompts/story.txt`. Each has a `prompt_version`, which is part of the cache key.

## 10. Audio narration

- **Default (decided in Phase 0):** the Edge voice `en-IN-NeerjaNeural` via `edge-tts`, rendered to MP3 on the laptop (`voice` in `config.toml`) and bundled in the pack, so it plays offline.
  - It needs no key and costs nothing, but it is **unofficial use of Microsoft's read-aloud service**, the same risk class as the AI Bridge. Fine for a personal prototype; a public app needs a licensed voice.
  - If rendering fails, the story ships without an MP3 and the phone reads it with `speechSynthesis` (Web Speech API, en-IN voice if installed).
- **Deepgram (switchable, Phase 2):**
  - Pre-rendered to MP3 **on the laptop**, so the API key never reaches the phone. The MP3s are bundled in the pack.
  - Size: at about 400 words per story, 300 stories works out to a few hundred MB at speech bitrates, so it is optional per trip.
- **Audio behavior:**
  - A chime before each story.
  - One story at a time.
  - Ducking or pausing around other audio is limited in a PWA, so if navigation audio cuts in, the story pauses and resumes from the last sentence (scripts are split by sentence).
- **TTS provider switch:** `tts = {device: speakDevice, deepgram: playMp3}[settings.tts]`. Only two options exist, so there's no interface layer.

## 11. Avoiding repetitive or unnecessary narration

- A permanent `told` log in IndexedDB, keyed by Wikidata ID, covers all trips. Already-heard places are skipped, or reduced to a one-line "on your left again…".
- At least about 45 s of silence between stories, which can be changed in settings. Never more than one story in the queue. If a place is passed while the queue is busy, low-priority stories are dropped and high-priority ones are kept as a short "you just passed".
- When stopped (speed below 5 km/h for more than 2 min, as in a traffic jam), don't start new stories based on distance creeping forward.
- The Skip button is a signal. Skipped places of a given type lower that type's bonus for the trip. This is a Phase 2 setting.

## 12. Keeping the MVP free

| Need | Free choice | Notes / limits (check before relying on them) |
|---|---|---|
| Story LLM, batch | Your AI Bridge | Personal only; against OpenAI's terms of use; about 100–200 requests a day |
| Story LLM, live or backup | Gemini via Google AI Studio free tier; Grok (xAI) | Free-tier rate limits and data-use terms change, so check both before relying on them. Gemini's free tier may use your prompts to improve Google products, per Google's terms. Grok's free credit availability has changed over time, so it may not be free. **Not verified today.** |
| Local LLM, optional | Ollama | Free; quality and speed depend on the laptop |
| Routing | OSRM public demo server | Fair use, not for production. Can be self-hosted later with the Geofabrik India extract |
| Geocoding | Nominatim | 1 request per second, with a custom User-Agent |
| Places and facts | Wikidata, Overpass, Wikipedia APIs | Free; send a descriptive User-Agent and cache everything |
| TTS | Edge voice en-IN-NeerjaNeural via `edge-tts`, MP3s rendered on the laptop; phone's Web Speech API as fallback | Free, no key; unofficial use of Microsoft's service, personal use only. MP3s play offline |
| TTS, better voice | Deepgram Aura | **Paid service that gives free credit on signup; it is not a permanent free tier.** Fine for testing. For a free alternative long-term, use Piper or Kokoro run locally on the laptop |
| Speech-to-text (Phase 3) | Whisper run locally, or in the browser with transformers.js | Free; or Chrome's Web Speech recognition, which is free but online |
| PWA hosting | GitHub Pages or Cloudflare Pages | Free static hosting. Trip packs are imported locally, not published, because they reveal travel plans |
| Proxy that hides API keys (Phase 2) | Cloudflare Workers free tier | Only needed when the PWA calls Gemini, Grok or Deepgram live |
| Storage | SQLite on the laptop, IndexedDB on the phone | Free |

**Licensing, important before any public sharing:**
- Wikipedia text is CC BY-SA, so stories derived from it need attribution and the same share-alike license if you distribute them.
- OSM data is ODbL.
- Wikidata is CC0.

Each story stores its sources, so attribution can be added.

---

## 13. Architecture

```
LAPTOP (Python, pre-generation)                         PHONE (PWA, plain JS)
┌──────────────────────────────────────────┐            ┌────────────────────────────────┐
│ histoway build A B                        │  trip      │ import pack → IndexedDB        │
│  route.py   Nominatim + OSRM → polyline   │  pack      │ gps.js   watchPosition → km    │
│  places.py  Wikidata+Overpass → score     │ ─────────▶ │ engine   trigger/queue/told    │
│  sources.py Wikipedia extracts            │  (.json    │ tts      device | deepgram mp3 │
│  narrate.py prompt → llm → parse → check  │   ±mp3)    │ wake lock, 4 buttons, sim mode │
│  llm.py     bridge | gemini | grok | ollama│            │ (P2) live fallback via Worker │
│  cache.sqlite (places, sources, stories)  │            └────────────────────────────────┘
└──────────────────────────────────────────┘
        │ HTTP 127.0.0.1:8756 (bearer token)
        ▼
   AI Bridge (unchanged)
```

**LLM provider switch.** I'm fairly confident, but haven't checked today, that Gemini, Grok, Groq and Ollama all offer OpenAI-compatible chat-completions endpoints. If so, two functions cover every provider:

```python
def bridge(prompt):            # POST 127.0.0.1:8756/ask, token from ~/.config/ai-bridge/config.toml
def openai_compat(prompt, base_url, key, model)   # Gemini / Grok / Ollama via config
PROVIDERS = {"bridge": bridge, "gemini": partial(openai_compat, ...), "grok": ..., "ollama": ...}
```

- The provider is chosen in `config.toml` with `llm = "bridge"` and can be overridden per run with `--llm gemini`.
- API keys come from environment variables. Model names come from config, so check them in each provider's docs; they aren't hard-coded.
- For the Bridge, `RATE_LIMITED` pauses about 5 min. `NOT_AUTHENTICATED` or `CHALLENGE` stops the build with a clear message, and the build can be resumed because the cache keeps finished stories.

**Project layout, deliberately small:**
```
Histoway/
  histoway/ __main__.py common.py (config, SQLite cache, HTTP) llm.py route.py places.py (incl. sources) narrate.py (incl. MP3s)
  prompts/story.txt
  config.toml   include.txt  exclude.txt   (the two lists are optional)
  app/ index.html app.js engine.js sw.js manifest.webmanifest icon.svg   packs/ (generated)
  tests/ test_pipeline.py test_engine.mjs
```
*(As built in Phase 1: sources.py and db.py were folded into places.py and common.py. The simulator drives along the pack's own route; any recorded GPX can be replayed too, so no fixture file is needed.)*
The PWA uses plain JS with no build step and no framework.

## 14. Data entities

- **Place:** `qid`, `name`, `lat`, `lon`, `type`, `wikipedia_language_count`, `article_kb`, `score`, `sensitive`
- **Source:** `qid`, `url`, `revision_id`, `excerpt`, `fetched_at`
- **Story:** `qid`, `prompt_version`, `tier`, `script`, `more_script`, `claims[]`, `has_dark_history`, `provider`, `model`, `status` (ok, needs_review, approved), `created_at`
- **Trip pack (exported):** `route_polyline`, `total_km`, `intro_script`, `places: [{qid, name, km, side, tier, priority, script, more_script, sources[], audio?}]`, `pack_version`
- **Told log (phone):** `qid`, `told_at`, `outcome` (completed, skipped, partial)

## 15. Offline

- The service worker caches the app.
- The pack and the told log live in IndexedDB.
- GPS works without mobile data.
- TTS works offline if the voices are installed, and MP3s are always offline.
- Only the live fallback (Phase 2) needs data. It is turned off automatically when there's no connection.
- **Ladakh and mountain stretches:** pre-generate and import the pack before leaving signal.

---

## 16. Development phases and roadmap

**Phase 0 — Test whether the stories are good (1–2 days, no app yet)** — ✅ done 2026-10-14 (storyteller prompt v3, Neerja voice chosen)
1. Pick 10 places by hand on the Bangalore → Mysore road: Vidhana Soudha, Attara Kacheri, Cubbon Park, MG Road, Lalbagh, Ramanagara, Srirangapatna, Mysore Palace, Chamundi Hill and the Kaveri crossing.
2. Write `prompts/story.txt`. Generate the stories through the Bridge with a quick script. Listen with the phone's TTS.
3. **Pass:** at least 7 of 10 sound like a storyteller, contain no made-up facts (spot-check against Wikipedia), and include the bad side where it exists (e.g. Srirangapatna 1799). If they fail, fix the prompt before building anything else.

**Phase 1 — MVP, the core experience (about 2–3 weeks part-time)** — built 2026-10-14, see *Phase 1: how to run it* below; the drive test is yours
1. `llm.py` with the Bridge and the OpenAI-compatible provider. Test it with a single prompt on each provider.
2. `route.py`: geocoding, OSRM, the route line, and projecting a point to a km marker.
3. `places.py` and `sources.py`: Wikidata and Overpass candidates, scoring, budget, merging, include and exclude lists, and Wikipedia excerpts, all cached in SQLite.
4. `narrate.py`: batched prompt, JSON parsing and validation, rule-based checks, status. Export the trip pack.
5. PWA: import the pack, Wake Lock, GPS to km marker, trigger engine, queue and told log, device TTS, the 4 buttons, and the service worker.
6. GPX simulator mode in the PWA.
7. **Drive test:** Bangalore → Mysore, for real.

**Phase 2 — Make it feel like one story (about 2–3 weeks)**
- Journey threads, region and river transitions, and the opening and closing stories
- Live fallback (Wikipedia nearby-places search plus Gemini or Grok), through a Cloudflare Worker proxy
- Deepgram MP3 pre-rendering
- LLM fact-check pass on a different provider, plus a review screen
- Tuning based on Skip presses

**Phase 3 — Ask the guide**
- Push-to-talk using Whisper, with questions answered from the nearest places' sources ("What is that building?", "Tell me the dark history", "Why was this city built here?")
- Trip recap and journal

**Phase 4 — Scale (see §20)**

## 17. Testing

- **Unit tests (pytest), no network:**
  - scoring and budget, using fixture candidates
  - km projection on a known line
  - JSON parsing of a saved LLM reply
  - the rule-based check catching a made-up year
- **Engine tests (plain JS run in node, or a test page):** feed in GPS points and check which stories start and when:
  - repeats are blocked
  - the gap between stories is respected
  - a place passed while the queue is busy is dropped
  - the jam rule holds
- **Simulation:** replay `fixtures/blr_mys.gpx` at 10× and check the order of stories and that none overlap.
- **Content review:** score 20 random stories on accuracy, balance, tone and length. Review every `sensitive` story by hand.
- **Field test:** a real drive, with a passenger noting stories that started late or early, were boring, or were wrong.
- **Provider check:** generate the same 3 places on the Bridge, Gemini and Grok, and compare quality and cost (all free).

## 18. Edge cases

- **GPS problems:** drift, tunnels, urban canyons, mountain switchbacks (Leh). Smooth the km marker so it never moves backwards by more than 200 m, and ignore readings with accuracy worse than 100 m.
- **Switchbacks:** the road passes the same peak several times, so play the story only once.
- **High speed:** a long story may run past the place. Start earlier, or drop down to the S tier.
- **Traffic jam or long stop:** don't start new stories (see §11).
- **Detours:** off-route mode (§5).
- **Return trip:** places already told (§11).
- **Several places close together** in a city: merge them, or space them out to the minimum gap.
- **Wrong or missing coordinates in Wikidata:** the include and exclude lists, plus the review screen.
- **Place names in two languages** (Bengaluru vs Bangalore): use the English Wikipedia label, and mention the local name in the story.
- **Night or bad weather**, when "on your left" isn't visible: fine for MVP. Phase 2 can lower the score of places that are only worth it if you can see them.
- **Pocket and battery:** the PWA needs the screen on, so plan for a charger. Android may kill a background tab; say so in the app.
- **Phone call or other audio:** pause and resume from the last sentence.
- **Pack older than a newer prompt version:** regenerate only the places whose stories changed (cache).

## 19. Problems, limitations and risks

- **PWA limit:** GPS stops when the screen locks, so a phone mount and charger are required. If testing shows this is unacceptable, the fix is an Android wrapper (Capacitor) with background location. Note this now and don't build it yet.
- **The Bridge is fragile:** ChatGPT redesigns its site, Cloudflare checks, throttling, and its terms of use. Switching providers is the exit plan, which is why it's in the MVP.
- **Free tiers can disappear.** Ollama is the backup that doesn't depend on anyone.
- **Coverage gaps:** small Indian sites often have short English Wikipedia pages or none, so rural stretches will be thin. Later, consider Kannada and Hindi Wikipedia and Wikivoyage.
- **Data gaps:** Wikipedia has its own biases and gaps. Labeling claims reduces the problem but doesn't remove it.
- **Driver distraction:** the app is audio-only and nothing should need reading while driving. Add a one-time warning.
- **Device TTS quality** varies by phone, and it may sound robotic. That is part of what Phase 0 tests.

## 20. Security and privacy

- **Location stays on the phone.** The pre-generated MVP sends no location anywhere.
- **Live fallback (Phase 2)** sends rough coordinates and place names to Gemini or Grok. Tell the user and make it opt-in.
- **API keys:** none on the phone in the MVP. Phase 2 keys stay in the Cloudflare Worker's secret store, never in the PWA.
- **The Bridge token** stays on the laptop. Don't change the Bridge's restriction to laptop-only connections.
- Trip packs reveal travel plans. Import them locally and don't publish them on the static host.
- **Prompt injection:** source text from Wikipedia or OSM is treated as data. The prompt fences it off, and the output is validated as JSON. Text is only played if it parses.

## 21. How it could scale later

1. Stories are cached by Wikidata ID, so the cache becomes a **shared story database**. Most trips in a region reuse the same places, so generation cost drops as more trips are built.
2. Move from the Bridge to a paid or self-hosted LLM. With switchable providers that is one config change.
3. Self-host OSRM and Overpass with the India extract.
4. A small backend serving packs by region, for any route.
5. An Android app (Capacitor) for background GPS. iOS later.
6. Multiple languages, using Wikipedia in those languages and multilingual TTS.
7. Community corrections and expert-reviewed stories.

## 22. What could make it different from existing travel guides

- **Labeling how certain each claim is, out loud.** "Legend says" and "historians disagree" are part of the story.
- **Difficult history included by default.** It isn't an add-on.
- **Journey threads:** one story told across a route (Tipu Sultan from Lalbagh to Srirangapatna; the Kaveri from Ramanagara to Mysore; the Indus along the Leh road).
- **The everyday places:** old roads, river crossings, geology, birthplaces, not just landmarks.
- **"Why is this city here?":** geography and the reasons behind it, as region transition stories.
- **Myth vs science:** e.g. Magnetic Hill near Leh, with the legend first and then the optical illusion explanation.
- **Sources you can check afterwards:** every story links back to its sources in the recap.

---

## Verification (how we'll know each step works)

- **Phase 0:** the 7-of-10 listening test described in §16.
- **Phase 1:**
  - `python -m histoway build "Bangalore" "Mysore"` produces a pack with 25–40 places and no `needs_review` stories without a reason.
  - `pytest` passes.
  - In the PWA, a 10× GPX replay plays stories in route order with no overlap and no repeats.
  - On a real drive, at least 80% of stories start before the place (a passenger logs the timing).
- **Provider switch:** the same build with `--llm gemini` and `--llm grok` produces valid packs.
- **Offline:** with airplane mode on after importing the pack, the full simulated trip still plays.

---

## Phase 1: how to run it

**On the laptop** (start the AI Bridge first: `ai-up`, then `ai-check`):
```
.venv/bin/python -m histoway build "Bangalore" "Mysore" --dry   # route + chosen places only, no LLM
.venv/bin/python -m histoway build "Bangalore" "Mysore"         # stories (cached, resumable) + Neerja MP3s -> app/packs/
.venv/bin/python -m histoway review                              # stories held for review, with their flags
.venv/bin/python -m histoway approve Q653988                     # play a held story after all; then build again (quick)
.venv/bin/python -m histoway redo Q653988 --note "include the deportations"   # regenerate one story
.venv/bin/python -m histoway build "Bangalore" "Mysore" --llm gemini           # same with Gemini (needs GEMINI_API_KEY)
```
- Places: tune `config.toml` `[select]`; force or block places with Wikidata IDs in `include.txt` / `exclude.txt`.
- Held for review (not played until approved): a year, number or name not in the source; a source with a dark-history theme the story leaves out; a contested topic (Tipu Sultan, communal, caste...); a mention of Wikipedia. Changing these rules in `config.toml` re-checks cached stories at the next build.

**On the phone** (USB, Chrome on Android):
```
python3 -m http.server 8765 --bind 127.0.0.1 -d app     # serve the app and packs, laptop only
adb reverse tcp:8765 tcp:8765                           # phone's localhost:8765 -> laptop
```
Open `http://localhost:8765/` on the phone → Set-up → choose the pack → **Download for offline**. After that it works with the laptop unplugged (same address). GPS and the service worker need a secure origin, which `localhost` is; a LAN IP over plain http would not work.

**Tests:** `.venv/bin/python tests/test_pipeline.py` and `node tests/test_engine.mjs` (no network).

**Known limits of this MVP:**
- The screen must stay on (Wake Lock); a PWA loses GPS when the screen locks.
- Opening/closing stories, "on your left again" lines, and live fallback are Phase 2.
- If a phone call pauses a story, press Resume.
- River crossings come from Overpass, whose public servers are often overloaded; the build warns and continues without them.
- Gemini and Grok are wired in but untested: no API keys on this laptop.
