import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Toaster } from "sonner";

import { shouldRetryQuery } from "../api/queries";

/**
 * Queries retry network errors and 5xx twice with backoff, never 4xx. Mutations don't retry here:
 * `useCommand` adds the idempotent retry, because only it knows the key to reuse.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetryQuery,
        retryDelay: (attempt) => Math.min(500 * 2 ** attempt, 4000),
        staleTime: 10_000,
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
  });
}

const queryClient = makeQueryClient();

export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <Toaster
        position="bottom-right"
        toastOptions={{
          unstyled: true,
          classNames: {
            toast:
              "float-in flex w-[356px] max-w-[calc(100vw-32px)] items-start gap-3 rounded-panel border border-rule bg-sheet p-3 text-body text-ink shadow-float",
            title: "font-strong",
            description: "text-meta text-pencil",
            actionButton:
              "ml-auto h-7 cursor-pointer rounded-chip bg-dispatch px-2 text-meta font-strong text-on-dispatch",
            error: "border-p0",
          },
        }}
      />
    </QueryClientProvider>
  );
}
