import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { itemQuery } from "../../api/queries";
import type { ItemOut } from "../../lib/api";

/** j/k and hover warm the detail cache, so opening the item is instant (DESIGN §6.3). */
export function usePrefetchItem(): (item: ItemOut) => void {
  const qc = useQueryClient();
  return useCallback(
    (item: ItemOut) => {
      void qc.query({ ...itemQuery(item.key), staleTime: 15_000 }).catch(() => undefined);
    },
    [qc],
  );
}
