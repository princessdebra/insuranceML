/**
 * Common Kenyan roads/highways, for the voice-input road-name disambiguator.
 *
 * Why this exists: speech-to-text on a short, isolated phrase like "Thika
 * Road" or "Ngong Road" sometimes returns something completely unrelated
 * in spelling ("the car", "I am going") -- the model mis-heard the sound
 * and guessed a common English phrase instead. Because the wrong output
 * doesn't textually resemble the right answer, string-similarity against
 * the transcript text won't recover it. Phonetic similarity (how the words
 * SOUND, not how they're spelled) does much better at this.
 */

export const KENYA_ROADS = [
  "Thika Road", "Ngong Road", "Mombasa Road", "Waiyaki Way", "Uhuru Highway",
  "Jogoo Road", "Outer Ring Road", "Langata Road", "Mbagathi Way", "Enterprise Road",
  "Kiambu Road", "Limuru Road", "Moi Avenue", "Kenyatta Avenue", "Haile Selassie Avenue",
  "Argwings Kodhek Road", "James Gichuru Road", "Lower Kabete Road", "Riverside Drive",
  "Chiromo Road", "Forest Road", "Kilimani Road", "Dennis Pritt Road", "Ralph Bunche Road",
  "Valley Road", "Milimani Road", "Ojijo Road", "Muranga Road", "Kangundo Road",
  "Juja Road", "Baba Dogo Road", "Airport North Road", "South B Road", "South C Road",
  "Magadi Road", "Karen Road", "Ngong Racecourse Road", "Kiambu Ring Road",
  "Eastern Bypass", "Northern Bypass", "Southern Bypass", "Namanga Road", "Nakuru-Eldoret Road",
  "Kisumu-Kakamega Road", "Malindi Road", "Nyeri-Nanyuki Road", "Kericho-Kisumu Road",
  "Machakos-Kitui Road", "Garden Estate Road", "Naivasha Road", "Kapiti Road",
] as const;

/**
 * Metaphone-ish phonetic key: strips vowels-after-first-letter and
 * silences common English digraphs, so words that SOUND alike collapse to
 * the same or similar keys even when spelled very differently. Deliberately
 * simple (not full Double Metaphone) -- good enough for short place names.
 */
function phoneticKey(word: string): string {
  let w = word.toLowerCase().replace(/[^a-z]/g, "");
  if (!w) return "";
  w = w
    .replace(/th/g, "t")
    .replace(/ph/g, "f")
    .replace(/ck/g, "k")
    .replace(/qu/g, "k")
    .replace(/gh/g, "g")
    .replace(/wh/g, "w")
    .replace(/ng/g, "n");
  const first = w[0];
  const rest = w.slice(1).replace(/[aeiouy]/g, "").replace(/(.)\1+/g, "$1");
  return first + rest;
}

function levenshtein(a: string, b: string): number {
  const dp: number[][] = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 0; j <= b.length; j++) dp[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1]
        : 1 + Math.min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1]);
    }
  }
  return dp[a.length][b.length];
}

function similarity(a: string, b: string): number {
  const maxLen = Math.max(a.length, b.length);
  if (maxLen === 0) return 1;
  return 1 - levenshtein(a, b) / maxLen;
}

// Generic road-type words that appear in most entries -- excluded from
// per-word comparison, otherwise "road" matching "road" gives a false
// perfect score regardless of what the actual distinguishing word is
// (e.g. "Ngong Road" would score identically against "Thika Road" and
// every other "___ Road" name, purely because they share this suffix).
const _GENERIC_SUFFIXES = new Set(["road", "way", "highway", "avenue", "drive", "street", "lane", "bypass"]);

/**
 * Ranks known Kenyan roads by phonetic + textual closeness to whatever
 * text was heard (right or wrong), so a garbled transcript can still
 * surface the road the speaker actually meant as a tappable suggestion.
 */
export function findSimilarRoads(heardText: string, limit = 4): string[] {
  const heardKey = phoneticKey(heardText.replace(/\s+/g, ""));
  const heardWords = heardText.toLowerCase().split(/\s+/).filter(Boolean);

  const scored = KENYA_ROADS.map((road) => {
    const roadKey = phoneticKey(road.replace(/\s+/g, ""));
    const phoneticScore = similarity(heardKey, roadKey);

    // Also check per-word phonetic overlap (e.g. "going" vs "ngong") --
    // generic suffix words excluded so "road"=="road" doesn't drown out
    // the word that actually identifies the place.
    const roadWords = road.toLowerCase().split(/\s+/).filter((w) => !_GENERIC_SUFFIXES.has(w));
    const heardWordsFiltered = heardWords.filter((w) => !_GENERIC_SUFFIXES.has(w));
    let bestWordScore = 0;
    for (const hw of heardWordsFiltered.length ? heardWordsFiltered : heardWords) {
      for (const rw of roadWords.length ? roadWords : road.toLowerCase().split(/\s+/)) {
        const s = similarity(phoneticKey(hw), phoneticKey(rw));
        if (s > bestWordScore) bestWordScore = s;
      }
    }

    // Plain text similarity too, in case the transcript was actually close
    const textScore = similarity(heardText.toLowerCase(), road.toLowerCase());

    return { road, score: Math.max(phoneticScore, bestWordScore, textScore) };
  });

  return scored
    .filter((s) => s.score >= 0.4)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map((s) => s.road);
}
