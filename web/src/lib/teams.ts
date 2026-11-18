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
