import { http, HttpResponse } from "msw";
import { runSummary } from "../test/fixtures";
import { nodeFormData } from "../test/node-web";
import { server } from "../test/server";
import { uploadDocument } from "./projects";

it("uploads the file and the run options as multipart form data", async () => {
  let form: FormData | undefined;
  let type = "";
  server.use(
    http.post("*/api/projects/p-1/runs", async ({ request }) => {
      type = request.headers.get("content-type") ?? "";
      form = await request.formData();
      return HttpResponse.json(runSummary, { status: 202 });
    }),
  );
  vi.stubGlobal("FormData", await nodeFormData());
  const file = new File(["Il pleut."], "رواية.txt", { type: "text/plain" });
  const run = await uploadDocument("p-1", {
    file,
    target_lang: "en",
    source_lang: null,
    pipeline_profile: "short",
    checkpoint: true,
    use_project_guide: false,
    all_languages: true,
  });
  expect(run.id).toBe("r-1");
  expect(type).toMatch(/^multipart\/form-data; boundary=/);
  expect((form?.get("file") as File).name).toBe("رواية.txt");
  expect(form?.get("target_lang")).toBe("en");
  expect(form?.get("source_lang")).toBeNull(); // unset fields are not sent
  expect(form?.get("pipeline_profile")).toBe("short");
  expect(form?.get("checkpoint")).toBe("true");
  expect(form?.get("use_project_guide")).toBe("false");
  expect(form?.get("all_languages")).toBe("true");
});
