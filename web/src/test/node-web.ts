// jsdom's FormData, which tests keep (react-router builds one from a form element), cannot be
// read back from a request by Node's fetch. Tests that read an upload's fields swap in Node's
// with vi.stubGlobal; File is Node's already (test/jsdom-env.ts).
export async function nodeFormData(): Promise<typeof FormData> {
  const parsed = await new Response("a=1", {
    headers: { "content-type": "application/x-www-form-urlencoded" },
  }).formData();
  return parsed.constructor as typeof FormData;
}
