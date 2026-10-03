import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "./cn";

export const DialogRoot = RadixDialog.Root;

/** Radix gives focus trapping, Esc, aria roles. Entrance: 160 ms opacity + 8 px (DESIGN §2.5). */
export function DialogPanel({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <RadixDialog.Portal>
      {/* The overlay centres the panel, so the entrance animation's translate can't fight centring. */}
      <RadixDialog.Overlay className="fade-in fixed inset-0 z-40 grid place-items-center bg-ink/40 p-4">
        <RadixDialog.Content
          className={cn(
            "float-in max-h-[calc(100dvh-32px)] w-[min(640px,100%)] overflow-y-auto rounded-panel bg-sheet p-6 shadow-float",
            className,
          )}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <RadixDialog.Title className="text-item font-heading">{title}</RadixDialog.Title>
              {description ? (
                <RadixDialog.Description className="mt-1 text-meta text-pencil">
                  {description}
                </RadixDialog.Description>
              ) : (
                <RadixDialog.Description className="sr-only-live">{title}</RadixDialog.Description>
              )}
            </div>
            <RadixDialog.Close
              aria-label="Close"
              className="inline-flex size-8 cursor-pointer items-center justify-center rounded-chip text-pencil hover:bg-dispatch-wash"
            >
              <X className="size-4" strokeWidth={1.75} />
            </RadixDialog.Close>
          </div>
          {children}
        </RadixDialog.Content>
      </RadixDialog.Overlay>
    </RadixDialog.Portal>
  );
}
