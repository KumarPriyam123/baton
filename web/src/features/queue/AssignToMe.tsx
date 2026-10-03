import { UserPlus } from "lucide-react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { Button } from "../../components/ui/Button";
import { ApiError, type ItemOut, api, unwrap } from "../../lib/api";
import { describeClaimLost } from "../../lib/errors";
import { useCommand } from "../../lib/useCommand";

/**
 * Strip quick action: claim an unowned item. No If-Match (the claim checks itself, SPEC §6.2);
 * a lost race says who won and how long ago (DESIGN §6.2) and the strip shows the new owner.
 */
export function AssignToMe({ item }: { item: ItemOut }) {
  const navigate = useNavigate();
  const claim = useCommand<undefined, ItemOut>({
    item,
    run: (_, ctx) =>
      unwrap(
        api.POST("/api/v1/items/{key}/claim", {
          params: { path: { key: item.key }, header: { "Idempotency-Key": ctx.idempotencyKey } },
        }),
      ),
    onError: (error) => {
      if (error instanceof ApiError && error.code === "ALREADY_CLAIMED") {
        toast.error(describeClaimLost(error, item.key), {
          action: {
            label: "View",
            onClick: () => void navigate(`/items/${item.key}`),
          },
        });
        return true;
      }
      return undefined;
    },
  });

  if (!item.allowed_actions.includes("claim")) return null;
  return (
    <Button
      variant="secondary"
      className="h-7 px-2 text-meta"
      pending={claim.isPending}
      onClick={() => {
        void claim.execute(undefined).then(
          () => {
            toast.success("Assigned to you");
          },
          () => undefined,
        );
      }}
    >
      <UserPlus className="size-3.5" strokeWidth={1.75} aria-hidden />
      Assign to me
    </Button>
  );
}
