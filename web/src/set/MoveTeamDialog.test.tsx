import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { project } from "../test/fixtures";
import { renderAt } from "../test/render";
import { server } from "../test/server";
import { MoveTeamDialog } from "./MoveTeamDialog";

it("an empty team makes the set personal again", async () => {
  let sent: unknown;
  server.use(
    http.get("*/api/teams", () => HttpResponse.json(["desk-a"])),
    http.patch("*/api/projects/p-1", async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ ...project, team: null });
    }),
  );
  const onClose = vi.fn();
  const { user } = renderAt(
    <MoveTeamDialog project={{ ...project, team: "desk-a" }} open onClose={onClose} />,
  );
  const dialog = screen.getByRole("dialog", { name: "Move to team" });
  await user.clear(within(dialog).getByLabelText("Team"));
  await user.click(within(dialog).getByRole("button", { name: "Move" }));
  await vi.waitFor(() => expect(sent).toEqual({ team: null }));
  expect(onClose).toHaveBeenCalled();
});

it("shows why a move was refused", async () => {
  server.use(
    http.get("*/api/teams", () => HttpResponse.json([])),
    http.patch("*/api/projects/p-1", () =>
      HttpResponse.json({ detail: "team: String should match pattern" }, { status: 422 }),
    ),
  );
  const { user } = renderAt(<MoveTeamDialog project={project} open onClose={() => {}} />);
  const dialog = screen.getByRole("dialog", { name: "Move to team" });
  await user.type(within(dialog).getByLabelText("Team"), "a/b");
  await user.click(within(dialog).getByRole("button", { name: "Move" }));
  expect(await within(dialog).findByText(/should match pattern/)).toBeInTheDocument();
});
