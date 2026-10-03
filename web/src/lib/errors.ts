/** SPEC §12 codes → what the user reads (DESIGN §6.2). Errors say what happened and what to do. */
import { toast } from "sonner";

import { ApiError, NetworkError } from "./api";

export function describeError(error: unknown): string {
  if (error instanceof NetworkError) {
    return "Baton can't be reached. Check your connection and try again. Nothing was saved.";
  }
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Nothing was saved.";
  }
  switch (error.code) {
    case "VALIDATION_FAILED":
      return error.errors?.[0]?.message ?? error.detail;
    case "FORBIDDEN":
    case "ALREADY_CLAIMED":
    case "WORKFLOW_VIOLATION":
    case "RATE_LIMITED":
      return error.detail;
    case "NOT_FOUND":
      return "This item doesn't exist or you no longer have access.";
    case "APPROVAL_ALREADY_PENDING":
      return "An approval is already pending for this request.";
    case "VERSION_CONFLICT":
      return "This request changed while you were editing.";
    case "APPROVAL_REQUIRED":
      return "This needs an approval before it can be resolved. Use Request approval.";
    case "REASON_REQUIRED":
      return "Add a reason to continue.";
    case "BUSY":
      return "Baton is busy and couldn't save this. Try again in a moment.";
    case "UNAUTHENTICATED":
      return "Your session ended. Sign in to continue.";
    default:
      break;
  }
  if (error.status >= 500) {
    return `Something went wrong on our side. Nothing was saved.${
      error.requestId ? ` Reference ${error.requestId}.` : ""
    }`;
  }
  // IDEMPOTENCY_KEY_REUSED, PRECONDITION_REQUIRED, CSRF_FAILED, METHOD_NOT_ALLOWED: client bugs.
  console.error("Unexpected API error", error.code, error.requestId);
  return `Something went wrong in Baton. Try again.${
    error.requestId ? ` Reference ${error.requestId}.` : ""
  }`;
}

export function notifyError(error: unknown): void {
  if (error instanceof ApiError && error.status === 401) return; // the redirect to /login handles it
  toast.error(describeError(error));
}
