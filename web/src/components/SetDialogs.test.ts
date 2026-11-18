import { ApiError } from "../api/client";
import { renameErrorText } from "./SetDialogs";

it("names a clash plainly and passes any other rename error through", () => {
  expect(renameErrorText(new ApiError(409, "conflict"))).toBe(
    "Another translation already has this name.",
  );
  expect(renameErrorText(new ApiError(422, "The name is too long."))).toBe("The name is too long.");
});
