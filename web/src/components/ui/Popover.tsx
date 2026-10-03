import * as RadixPopover from "@radix-ui/react-popover";
import type { ReactNode } from "react";

import { cn } from "./cn";

export const PopoverRoot = RadixPopover.Root;
export const PopoverAnchor = RadixPopover.Anchor;
export const PopoverTrigger = RadixPopover.Trigger;
export const PopoverClose = RadixPopover.Close;

/** A small floating panel (DESIGN §2.4): 8 px radius, shadow, 160 ms entrance. Esc closes it. */
export function PopoverContent({
  children,
  className,
  label,
  align = "start",
}: {
  children: ReactNode;
  className?: string;
  /** Accessible name of the panel. */
  label: string;
  align?: "start" | "center" | "end";
}) {
  return (
    <RadixPopover.Portal>
      <RadixPopover.Content
        aria-label={label}
        align={align}
        sideOffset={8}
        collisionPadding={12}
        className={cn(
          "float-in z-50 w-[min(360px,calc(100vw-24px))] rounded-panel border border-rule bg-sheet p-4 shadow-float",
          className,
        )}
      >
        {children}
      </RadixPopover.Content>
    </RadixPopover.Portal>
  );
}
