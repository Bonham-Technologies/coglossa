import { Navigate, useParams } from "react-router";
import { useRun } from "../api/runs";
import { ErrorMessage } from "../components/ErrorMessage";
import { Loading } from "../components/Loading";

/** Old project addresses (spec §3.2). */
export function ProjectRedirect() {
  const { projectId = "" } = useParams();
  return <Navigate to={`/t/${projectId}`} replace />;
}

/** Old run addresses, and their tabs: the run names its project. */
export function RunRedirect() {
  const { runId = "" } = useParams();
  const run = useRun(runId);
  if (run.isError) return <ErrorMessage error={run.error} />;
  if (run.data === undefined) return <Loading />;
  return <Navigate to={`/t/${run.data.run.project_id}/${runId}`} replace />;
}
