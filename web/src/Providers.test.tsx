import { ApiError } from "./api/client";
import { makeQueryClient } from "./Providers";

it("retries a failed query once, but never a 4xx", () => {
  const retry = makeQueryClient().getDefaultOptions().queries?.retry as (
    count: number,
    error: unknown,
  ) => boolean;
  expect(retry(0, new ApiError(404, "gone"))).toBe(false);
  expect(retry(0, new ApiError(503, "down"))).toBe(true);
  expect(retry(0, new ApiError(0, "network"))).toBe(true);
  expect(retry(1, new ApiError(503, "down"))).toBe(false);
  expect(makeQueryClient({ retry: false }).getDefaultOptions().queries?.retry).toBe(false);
});
