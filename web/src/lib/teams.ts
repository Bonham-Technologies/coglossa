const KEY = "coglossa.team";

/** CGL-26: the team a new set goes to, remembered between visits; "" means Only me. */
export function rememberedTeam(teams: string[]): string {
  let saved: string | null;
  try {
    saved = localStorage.getItem(KEY);
  } catch {
    saved = null; // storage blocked: fall back to the first team
  }
  if (saved === "" || (saved !== null && teams.includes(saved))) return saved;
  return teams[0] ?? "";
}

export function rememberTeam(team: string): void {
  try {
    localStorage.setItem(KEY, team);
  } catch {
    // storage blocked: nothing to remember
  }
}

/** The ASCII whitespace the server trims from a name's ends (people.NAME_SPACE); other
 * spaces, such as U+00A0 or U+3000, are part of the name. */
const NAME_SPACE = /^[ \t\n\r\v\f]+|[ \t\n\r\v\f]+$/g;

/** CGL-31: names and team names compare as the server's people.team_key does: Unicode NFC,
 * ASCII whitespace trimmed, and the case of A-Z only ignored, so "é" and "É" stay different. */
export function teamKey(name: string): string {
  return name
    .normalize("NFC")
    .replace(NAME_SPACE, "")
    .replace(/[A-Z]/g, (letter) => letter.toLowerCase());
}
