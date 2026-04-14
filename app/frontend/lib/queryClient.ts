import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

function extractMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "Something went wrong";
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      // Only surface query errors that have already been retried — avoid
      // spamming toasts for transient refetch blips.
      if (query.state.fetchFailureCount > 1) {
        toast.error(extractMessage(error));
      }
    },
  }),
  mutationCache: new MutationCache({
    onError: (error) => {
      toast.error(extractMessage(error));
    },
  }),
});
