export type Piece = { text: string; mark: boolean };

/** Split `text` so every occurrence of every non-empty excerpt is marked (spec §8.4: the judge's
 * error spans highlighted inline). Overlapping marks merge. */
export function highlight(text: string, excerpts: string[]): Piece[] {
  const marked = new Array<boolean>(text.length).fill(false);
  for (const excerpt of excerpts) {
    if (!excerpt) continue;
    for (let at = text.indexOf(excerpt); at !== -1; at = text.indexOf(excerpt, at + 1)) {
      marked.fill(true, at, at + excerpt.length);
    }
  }
  const pieces: Piece[] = [];
  for (let i = 0; i < text.length; i++) {
    const last = pieces.at(-1);
    if (last && last.mark === marked[i]) last.text += text[i];
    else pieces.push({ text: text[i]!, mark: marked[i]! });
  }
  return pieces;
}
