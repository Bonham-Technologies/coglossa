/** Characters a file name cannot hold on common systems. */
const NOT_IN_NAMES = '\\/:*?"<>|';

function cleanName(text: string): string {
  return [...text].filter((ch) => ch >= " " && !NOT_IN_NAMES.includes(ch)).join("");
}

export function withoutExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

export function firstWords(text: string, count = 5): string {
  return text.trim().split(/\s+/).slice(0, count).join(" ");
}

/** Spec §4.1: pasted text travels as a .txt file named after its first five words. */
export function pastedFile(text: string): File {
  const base = cleanName(firstWords(text)).replace(/\s+/g, " ").trim().slice(0, 80);
  return new File([text], `${base || "Pasted text"}.txt`, { type: "text/plain" });
}

/** Spec §4.1: the set is named after its file, or "<first> and N more". */
export function setNameFor(files: File[]): string {
  const first = withoutExtension(files[0]?.name ?? "").trim() || "Translation";
  const name = files.length > 1 ? `${first} and ${files.length - 1} more` : first;
  return name.slice(0, 180); // room for " (50)" under the API's 200
}

/** Plan decision 1: "welcome", "welcome (2)", … */
export function numbered(name: string, n: number): string {
  return n <= 1 ? name : `${name} (${n})`;
}
